"""Descriptor-bound schema-v2 workspace initialization and readiness."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import story_writing_agents.workspace.schema as schema

from ..agent_exchange_paths import is_agent_exchange_runtime_directory
from ..runtime_state_paths import is_orchestrator_runtime_directory
from ..errors import (
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..git import (
    GitBaselineManifest,
    GitChangeKind,
    GitFailureCode,
    GitOperationError,
    GitRepositoryAdapter,
    GitRepositoryState,
    GitTopologyKind,
)
from ..git.session_contracts import GitSessionResourceError, GitSessionResourceState
from ..git.session_resources import (
    list_git_session_resources as _list_git_session_resources,
    require_ready_git_session_resources as _require_ready_git_session_resources,
)
from .schema import (
    WORKSPACE_DIRECTORIES,
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WORKSPACE_MARKER_PATH,
    WorkspaceInitializationJournal,
    WorkspaceScaffoldEntry,
    decode_initialization_journal,
    decode_workspace_marker,
    encode_initialization_journal,
    validate_workspace_identifier,
)
from .policy_scaffold import (
    MUTABLE_CONFIGURATION_PATHS,
    baseline_manifest,
    known_baseline_manifest,
    known_initializer_scaffold,
    persona_exists,
    required_scaffold_projection,
    validate_mutable_configuration,
    validated_configuration_allowance,
)
from .root_contracts import (
    LEGACY_WORKSPACE_SENTINELS,
    WorkspaceRootInspection,
    WorkspaceRootRole,
    WorkspaceRootState,
    workspace_root_role,
)
from .root_filesystem import (
    create_and_bind_root,
    meaningful_directory_identity,
    open_directory,
    open_existing_root,
    read_regular_file,
    read_relative_regular,
    require_lexical_root,
    valid_identity,
    verify_named_identity,
    walk_directories,
    write_create_only,
)


def inspect_workspace_root(root: Path) -> WorkspaceRootInspection:
    """Classify one explicit root without mutation and validate recognized state."""

    root = require_lexical_root(root)
    try:
        observed = os.stat(root, follow_symlinks=False)
    except FileNotFoundError:
        return WorkspaceRootInspection(WorkspaceRootState.MISSING, None)
    except OSError:
        raise WorkspaceSecurityError("workspace root cannot be inspected") from None
    identity = meaningful_directory_identity(observed)
    if identity is None:
        raise WorkspaceSecurityError("workspace root is not a safe directory")
    descriptor = open_existing_root(root, identity)
    try:
        if _relative_entry_exists(
            descriptor,
            WORKSPACE_INITIALIZATION_JOURNAL_PATH,
        ):
            _validate_initializing_root(descriptor)
            return WorkspaceRootInspection(
                WorkspaceRootState.INITIALIZING,
                identity,
            )
        if _workspace_marker_present(descriptor):
            role, session_id, main_root = _verify_ready(
                descriptor,
                root,
                journal_present=False,
            )
            return WorkspaceRootInspection(
                WorkspaceRootState.READY,
                identity,
                role,
                session_id,
                main_root,
            )
        names = _root_names(descriptor)
        if not names:
            return WorkspaceRootInspection(WorkspaceRootState.EMPTY, identity)
        if _contains_legacy_boundary(descriptor):
            raise WorkspaceVersionUnsupportedError(
                "workspace version is unsupported; migration and compatibility "
                "are unsupported, so create a new workspace"
            )
        raise WorkspaceFormatError(
            "workspace is not an exact v2 workspace; create a new workspace"
        )
    finally:
        os.close(descriptor)


def initialize_workspace_root(
    root: Path,
    inspection: WorkspaceRootInspection,
) -> WorkspaceRootInspection:
    """Converge one absent, empty, or journal-backed root to exact ready v2."""

    root = require_lexical_root(root)
    if type(inspection) is not WorkspaceRootInspection:
        raise TypeError("inspection must be a WorkspaceRootInspection")
    try:
        inspection = WorkspaceRootInspection(
            state=inspection.state,
            identity=inspection.identity,
            role=inspection.role,
            session_id=inspection.session_id,
            main_root=inspection.main_root,
        )
    except TypeError:
        raise TypeError("inspection must be a valid WorkspaceRootInspection") from None
    if inspection.state is WorkspaceRootState.READY:
        identity = require_ready_workspace_root(root, inspection.identity)
        role, session_id, main_root = workspace_root_role(root)
        return WorkspaceRootInspection(
            WorkspaceRootState.READY,
            identity,
            role,
            session_id,
            main_root,
        )
    _require_initialization_operations()
    if inspection.state is WorkspaceRootState.MISSING:
        _reject_repository_ancestor(root)
        descriptor, identity = create_and_bind_root(root)
    else:
        assert inspection.identity is not None
        descriptor = open_existing_root(root, inspection.identity)
        identity = inspection.identity
    try:
        if inspection.state in {WorkspaceRootState.MISSING, WorkspaceRootState.EMPTY}:
            _require_topology(root, {GitTopologyKind.ABSENT})
        elif inspection.state is WorkspaceRootState.INITIALIZING:
            _require_topology(
                root,
                {GitTopologyKind.ABSENT, GitTopologyKind.STANDALONE},
            )
        scaffold = schema.canonical_initializer_scaffold()
        manifest = baseline_manifest(scaffold)
        journal = WorkspaceInitializationJournal(manifest.manifest_digest)
        if inspection.state in {WorkspaceRootState.MISSING, WorkspaceRootState.EMPTY}:
            if _root_names(descriptor):
                raise WorkspaceFormatError(
                    "workspace changed before initialization; create a new workspace"
                )
            write_create_only(
                descriptor,
                WORKSPACE_INITIALIZATION_JOURNAL_PATH,
                encode_initialization_journal(journal),
            )
        else:
            _validate_initializing_root(descriptor, expected_manifest=manifest)
        _install_missing_scaffold(descriptor, scaffold)
        _validate_initializing_root(descriptor, expected_manifest=manifest)
        verify_named_identity(root, descriptor, identity)
        adapter = GitRepositoryAdapter(root)
        git_failure: WorkspaceFormatError | ConfigurationError | None = None
        try:
            completed = adapter.create_baseline(manifest)
        except GitOperationError as exc:
            git_failure = _workspace_git_failure(exc)
        if git_failure is not None:
            raise git_failure
        if completed.state is not GitRepositoryState.BASELINE_COMMITTED:
            raise WorkspaceFormatError("workspace Git baseline requires recovery")
        _verify_ready(descriptor, root, journal_present=True, manifest=manifest)
        os.unlink(WORKSPACE_INITIALIZATION_JOURNAL_PATH, dir_fd=descriptor)
        os.fsync(descriptor)
        _verify_ready(descriptor, root, journal_present=False, manifest=manifest)
        verify_named_identity(root, descriptor, identity)
        return WorkspaceRootInspection(
            WorkspaceRootState.READY,
            identity,
            WorkspaceRootRole.MAIN,
            None,
            root,
        )
    finally:
        os.close(descriptor)


def require_ready_workspace_root(
    root: Path,
    expected_identity: tuple[int, int] | None,
) -> tuple[int, int]:
    """Require exact clean ready-v2 state without mutation."""

    root = require_lexical_root(root)
    if not valid_identity(expected_identity):
        raise WorkspaceSecurityError("workspace root identity is unavailable")
    assert expected_identity is not None
    descriptor = open_existing_root(root, expected_identity)
    try:
        _verify_ready(descriptor, root, journal_present=False)
        verify_named_identity(root, descriptor, expected_identity)
        return expected_identity
    finally:
        os.close(descriptor)


def _verify_ready(
    descriptor: int,
    root: Path,
    *,
    journal_present: bool,
    manifest: GitBaselineManifest | None = None,
) -> tuple[WorkspaceRootRole, str | None, Path]:
    scaffold = schema.canonical_initializer_scaffold()
    role, session_id, main_root = workspace_root_role(root)
    if role is WorkspaceRootRole.SESSION:
        try:
            marker_bytes = read_relative_regular(descriptor, WORKSPACE_MARKER_PATH)
        except (OSError, WorkspaceFormatError):
            raise WorkspaceFormatError("workspace marker is missing") from None
        decode_workspace_marker(marker_bytes)
        snapshot = required_scaffold_projection(descriptor, scaffold)
        validate_mutable_configuration(
            snapshot["config/system_defaults.json"],
            snapshot["config/agent_configs.json"],
            persona_exists=lambda name: persona_exists(descriptor, name),
        )
        assert session_id is not None
        manifest = manifest or known_baseline_manifest(main_root)
        try:
            _require_ready_git_session_resources(
                root,
                manifest,
                session_id,
                allowed_worktree_changes=validated_configuration_allowance(
                    main_root
                ),
            )
        except GitSessionResourceError:
            raise WorkspaceFormatError(
                "workspace session resources require recovery"
            ) from None
        return role, session_id, main_root
    if journal_present:
        manifest = manifest or baseline_manifest(scaffold)
        snapshot = _snapshot_relative_files(descriptor)
        marker_bytes = snapshot.get(WORKSPACE_MARKER_PATH)
        if marker_bytes is None:
            raise WorkspaceFormatError("workspace marker is missing")
        decode_workspace_marker(marker_bytes)
        _require_directories(descriptor)
        allowed_files = {entry.path: entry.content for entry in scaffold}
        journal_bytes = snapshot.get(WORKSPACE_INITIALIZATION_JOURNAL_PATH)
        if journal_bytes is None:
            raise WorkspaceFormatError("workspace initialization journal is missing")
        journal = decode_initialization_journal(journal_bytes)
        if journal.manifest_digest != manifest.manifest_digest:
            raise WorkspaceFormatError("workspace initialization journal is invalid")
        allowed_files[WORKSPACE_INITIALIZATION_JOURNAL_PATH] = journal_bytes
        if snapshot != allowed_files:
            raise WorkspaceFormatError("workspace scaffold requires recovery")
    else:
        if _relative_entry_exists(
            descriptor,
            WORKSPACE_INITIALIZATION_JOURNAL_PATH,
        ):
            raise WorkspaceFormatError("workspace initialization requires recovery")
        try:
            marker_bytes = read_relative_regular(descriptor, WORKSPACE_MARKER_PATH)
        except (OSError, WorkspaceFormatError):
            raise WorkspaceFormatError("workspace marker is missing") from None
        decode_workspace_marker(marker_bytes)
        _require_directories(descriptor)
        scaffold = known_initializer_scaffold(descriptor)
        manifest = manifest or baseline_manifest(scaffold)
        snapshot = required_scaffold_projection(descriptor, scaffold)
        validate_mutable_configuration(
            snapshot["config/system_defaults.json"],
            snapshot["config/agent_configs.json"],
            persona_exists=lambda name: persona_exists(descriptor, name),
        )
        immutable_snapshot = {
            path: content
            for path, content in snapshot.items()
            if path not in MUTABLE_CONFIGURATION_PATHS
        }
        immutable_scaffold = {
            entry.path: entry.content
            for entry in scaffold
            if entry.path not in MUTABLE_CONFIGURATION_PATHS
        }
        if immutable_snapshot != immutable_scaffold:
            raise WorkspaceFormatError("workspace scaffold requires recovery")
        try:
            inventory = _list_git_session_resources(
                root,
                manifest,
                allowed_worktree_changes=tuple(sorted(MUTABLE_CONFIGURATION_PATHS)),
            )
        except GitSessionResourceError:
            raise WorkspaceFormatError(
                "workspace Git resources require recovery"
            ) from None
        if any(
            value.state is not GitSessionResourceState.READY
            for value in inventory.sessions
        ):
            raise WorkspaceFormatError("workspace Git resources require recovery")
        return role, session_id, main_root
    adapter = GitRepositoryAdapter(root)
    git_failure: WorkspaceFormatError | ConfigurationError | None = None
    try:
        baseline = adapter.inspect_baseline(manifest)
        status = adapter.status()
    except GitOperationError as exc:
        git_failure = _workspace_git_failure(exc)
    if git_failure is not None:
        raise git_failure
    if (
        baseline.state is not GitRepositoryState.BASELINE_COMMITTED
        or baseline.topology.kind is not GitTopologyKind.STANDALONE
    ):
        raise WorkspaceFormatError("workspace Git baseline requires recovery")
    if journal_present:
        if (
            len(status.entries) != 1
            or status.entries[0].path != WORKSPACE_INITIALIZATION_JOURNAL_PATH
            or status.entries[0].index_change is not None
            or status.entries[0].worktree_change is not GitChangeKind.UNTRACKED
        ):
            raise WorkspaceFormatError("workspace status requires recovery")
    elif not status.clean:
        raise WorkspaceFormatError("workspace status requires recovery")
    return role, session_id, main_root


def _validate_initializing_root(
    descriptor: int,
    *,
    expected_manifest: GitBaselineManifest | None = None,
) -> None:
    snapshot = _snapshot_relative_files(descriptor)
    journal_bytes = snapshot.get(WORKSPACE_INITIALIZATION_JOURNAL_PATH)
    if journal_bytes is None:
        raise WorkspaceFormatError("workspace initialization journal is missing")
    journal = decode_initialization_journal(journal_bytes)
    scaffold = schema.canonical_initializer_scaffold()
    manifest = expected_manifest or baseline_manifest(scaffold)
    if journal.manifest_digest != manifest.manifest_digest:
        raise WorkspaceFormatError("workspace initialization journal is invalid")
    expected = {entry.path: entry.content for entry in scaffold}
    for path, content in snapshot.items():
        if path == WORKSPACE_INITIALIZATION_JOURNAL_PATH:
            continue
        if path not in expected or expected[path] != content:
            raise WorkspaceFormatError("workspace partial scaffold requires recovery")


def _install_missing_scaffold(
    descriptor: int,
    scaffold: tuple[WorkspaceScaffoldEntry, ...],
) -> None:
    for relative in WORKSPACE_DIRECTORIES:
        child = walk_directories(
            descriptor,
            Path(relative).parts,
            create=True,
        )
        os.close(child)
    existing = _snapshot_relative_files(descriptor)
    for entry in scaffold:
        current = existing.get(entry.path)
        if current is None:
            write_create_only(descriptor, entry.path, entry.content)
        elif current != entry.content:
            raise WorkspaceFormatError("workspace partial scaffold requires recovery")


def _snapshot_relative_files(descriptor: int) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    allowed_directories = _allowed_directories()

    def visit(directory_descriptor: int, prefix: tuple[str, ...]) -> None:
        try:
            names = sorted(os.listdir(directory_descriptor))
        except OSError:
            raise WorkspaceFormatError(
                "workspace scaffold cannot be enumerated"
            ) from None
        for name in names:
            if not prefix and name == ".git":
                continue
            relative = "/".join((*prefix, name))
            try:
                value = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise WorkspaceFormatError(
                    "workspace scaffold cannot be inspected"
                ) from None
            if stat.S_ISDIR(value.st_mode):
                candidate_shard = (
                    prefix
                    == (
                        "agent_states",
                        "orchestrator_state",
                        "consensus_materials",
                        "v1",
                    )
                    and len(name) == 2
                    and all(character in "0123456789abcdef" for character in name)
                )
                if (
                    relative not in allowed_directories
                    and not candidate_shard
                    and not is_agent_exchange_runtime_directory(relative)
                    and not is_orchestrator_runtime_directory(relative)
                    and not _is_conversation_runtime_directory(relative)
                ):
                    raise WorkspaceFormatError(
                        "workspace contains an undeclared directory"
                    )
                child = open_directory(directory_descriptor, name, create=False)
                try:
                    visit(child, (*prefix, name))
                finally:
                    os.close(child)
            elif stat.S_ISREG(value.st_mode):
                result[relative] = read_regular_file(directory_descriptor, name)
            else:
                raise WorkspaceSecurityError("workspace contains an unsafe entry")

    visit(descriptor, ())
    return result


def _is_conversation_runtime_directory(path: str) -> bool:
    """Recognize only canonical filesystem-conversation directory shapes."""

    parts = path.split("/")
    if not parts or parts[0] != "discussion_logs":
        return False
    if len(parts) == 2:
        return _is_workspace_identifier(parts[1])
    if len(parts) == 3:
        return _is_workspace_identifier(parts[1]) and parts[2] == "interactive"
    if len(parts) == 4:
        return (
            _is_workspace_identifier(parts[1])
            and parts[2:] == ["interactive", "conversations"]
        )
    return (
        len(parts) == 5
        and _is_workspace_identifier(parts[1])
        and parts[2:4] == ["interactive", "conversations"]
        and _is_workspace_identifier(parts[4])
    )


def _is_workspace_identifier(value: str) -> bool:
    try:
        validate_workspace_identifier(value)
    except SessionIdError:
        return False
    return True


def _workspace_marker_present(descriptor: int) -> bool:
    try:
        read_relative_regular(descriptor, WORKSPACE_MARKER_PATH)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        raise WorkspaceFormatError("workspace marker cannot be inspected") from None
    return True


def _relative_entry_exists(descriptor: int, relative: str) -> bool:
    """Probe one exact root-relative entry without following its final component."""

    try:
        os.stat(relative, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise WorkspaceFormatError("workspace entry cannot be inspected") from None
    return True


def _allowed_directories() -> frozenset[str]:
    values: set[str] = set()
    for relative in WORKSPACE_DIRECTORIES:
        path = Path(relative)
        for parent in (path, *path.parents):
            if parent.as_posix() != ".":
                values.add(parent.as_posix())
    return frozenset(values)


def _require_directories(descriptor: int) -> None:
    """Require every exact scaffold directory without creating it."""

    for relative in WORKSPACE_DIRECTORIES:
        try:
            child = walk_directories(
                descriptor,
                Path(relative).parts,
                create=False,
            )
        except (FileNotFoundError, NotADirectoryError, OSError):
            raise WorkspaceFormatError("workspace scaffold requires recovery") from None
        os.close(child)


def _root_names(descriptor: int) -> tuple[str, ...]:
    try:
        return tuple(sorted(os.listdir(descriptor)))
    except OSError:
        raise WorkspaceFormatError("workspace root cannot be enumerated") from None


def _contains_legacy_boundary(descriptor: int) -> bool:
    try:
        files = _snapshot_relative_files(descriptor)
    except WorkspaceSecurityError:
        raise
    except WorkspaceFormatError:
        names = _root_names(descriptor)
        return any(
            name in {"config", "agent_states", "discussion_logs", "manuscript"}
            for name in names
        )
    return any(path in files for path in LEGACY_WORKSPACE_SENTINELS)


def _reject_repository_ancestor(root: Path) -> None:
    existing = root.parent
    while not existing.exists():
        existing = existing.parent
    git_failure: WorkspaceFormatError | ConfigurationError | None = None
    try:
        topology = GitRepositoryAdapter(existing).inspect_topology()
    except GitOperationError as exc:
        git_failure = _workspace_git_failure(exc)
    if git_failure is not None:
        raise git_failure
    if topology.kind is not GitTopologyKind.ABSENT:
        raise WorkspaceSecurityError("workspace cannot be initialized inside Git")


def _require_topology(root: Path, allowed: set[GitTopologyKind]) -> None:
    git_failure: WorkspaceFormatError | ConfigurationError | None = None
    try:
        topology = GitRepositoryAdapter(root).inspect_topology()
    except GitOperationError as exc:
        git_failure = _workspace_git_failure(exc)
    if git_failure is not None:
        raise git_failure
    if topology.kind not in allowed:
        raise WorkspaceSecurityError("workspace Git topology is unsupported")


def _workspace_git_failure(
    error: GitOperationError,
) -> WorkspaceFormatError | ConfigurationError:
    if error.code is GitFailureCode.IDENTITY_INVALID:
        result: WorkspaceFormatError | ConfigurationError = ConfigurationError(
            "effective Git identity is unavailable or invalid"
        )
    elif error.code in {
        GitFailureCode.UNSAFE_ROOT,
        GitFailureCode.UNSUPPORTED_TOPOLOGY,
    }:
        result = WorkspaceFormatError("workspace Git topology is unsupported")
    else:
        result = WorkspaceFormatError("workspace Git state requires recovery")
    result.__cause__ = None
    result.__context__ = None
    result.__suppress_context__ = True
    return result


def _require_initialization_operations() -> None:
    required_callables = {
        name: getattr(os, name, None)
        for name in (
            "close",
            "dup",
            "fstat",
            "fsync",
            "link",
            "listdir",
            "mkdir",
            "open",
            "read",
            "stat",
            "unlink",
            "write",
        )
    }
    required_flags = (
        getattr(os, "O_WRONLY", None),
        getattr(os, "O_DIRECTORY", None),
        getattr(os, "O_NOFOLLOW", None),
        getattr(os, "O_CREAT", None),
        getattr(os, "O_EXCL", None),
    )
    read_only_flag = getattr(os, "O_RDONLY", None)
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    supports_follow_symlinks = getattr(
        os,
        "supports_follow_symlinks",
        frozenset(),
    )
    supports_fd = getattr(os, "supports_fd", frozenset())
    if (
        any(not callable(operation) for operation in required_callables.values())
        or any(type(flag) is not int or flag <= 0 for flag in required_flags)
        or type(read_only_flag) is not int
        or read_only_flag < 0
        or any(
            required_callables[name] not in supports_dir_fd
            for name in ("link", "mkdir", "open", "stat", "unlink")
        )
        or any(
            required_callables[name] not in supports_follow_symlinks
            for name in ("link", "stat")
        )
        or required_callables["listdir"] not in supports_fd
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe workspace initialization is unsupported",
        )


__all__: list[str] = []
