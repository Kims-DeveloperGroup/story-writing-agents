"""Descriptor-bound schema-v2 workspace initialization and readiness lifecycle."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from ..errors import (
    ConfigurationError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..git import (
    GitBaselineManifest,
    GitChangeKind,
    GitFailureCode,
    GitManifestEntry,
    GitOperationError,
    GitRepositoryAdapter,
    GitRepositoryState,
    GitTopologyKind,
)
from ..git.story_commits import StoryCommitService
from ..git.session_contracts import (
    GIT_SESSION_WORKTREE_SIBLING_SUFFIX,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceInventory,
    GitSessionResourceState,
)
from ..git.session_history import GitSessionHistorySnapshot, open_git_session_history
from ..git.session_resources import (
    inspect_git_session_resources as _inspect_git_session_resources,
    list_git_session_resources as _list_git_session_resources,
    provision_git_session_resources as _provision_git_session_resources,
    require_ready_git_session_resources as _require_ready_git_session_resources,
)
from .schema import (
    WORKSPACE_DIRECTORIES,
    WORKSPACE_INITIALIZATION_JOURNAL_PATH,
    WORKSPACE_MARKER_PATH,
    WorkspaceInitializationJournal,
    WorkspaceScaffoldEntry,
    canonical_initializer_scaffold,
    decode_initialization_journal,
    decode_workspace_marker,
    encode_initialization_journal,
    validate_workspace_identifier,
)


_LEGACY_SENTINELS = (
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
        elif not _valid_identity(self.identity):
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


def inspect_workspace_root(root: Path) -> WorkspaceRootInspection:
    """Classify one explicit root without mutation and validate recognized state."""

    root = _require_lexical_root(root)
    try:
        observed = os.stat(root, follow_symlinks=False)
    except FileNotFoundError:
        return WorkspaceRootInspection(WorkspaceRootState.MISSING, None)
    except OSError:
        raise WorkspaceSecurityError("workspace root cannot be inspected") from None
    identity = _meaningful_directory_identity(observed)
    if identity is None:
        raise WorkspaceSecurityError("workspace root is not a safe directory")
    descriptor = _open_existing_root(root, identity)
    try:
        names = _root_names(descriptor)
        if not names:
            return WorkspaceRootInspection(WorkspaceRootState.EMPTY, identity)
        if WORKSPACE_INITIALIZATION_JOURNAL_PATH in names:
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

    root = _require_lexical_root(root)
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
        role, session_id, main_root = _workspace_role(root)
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
        descriptor, identity = _create_and_bind_root(root)
    else:
        assert inspection.identity is not None
        descriptor = _open_existing_root(root, inspection.identity)
        identity = inspection.identity
    try:
        if inspection.state in {WorkspaceRootState.MISSING, WorkspaceRootState.EMPTY}:
            _require_topology(root, {GitTopologyKind.ABSENT})
        elif inspection.state is WorkspaceRootState.INITIALIZING:
            _require_topology(
                root,
                {GitTopologyKind.ABSENT, GitTopologyKind.STANDALONE},
            )
        scaffold = canonical_initializer_scaffold()
        manifest = _baseline_manifest(scaffold)
        journal = WorkspaceInitializationJournal(manifest.manifest_digest)
        if inspection.state in {WorkspaceRootState.MISSING, WorkspaceRootState.EMPTY}:
            if _root_names(descriptor):
                raise WorkspaceFormatError(
                    "workspace changed before initialization; create a new workspace"
                )
            _write_create_only(
                descriptor,
                WORKSPACE_INITIALIZATION_JOURNAL_PATH,
                encode_initialization_journal(journal),
            )
        else:
            _validate_initializing_root(descriptor, expected_manifest=manifest)
        _install_missing_scaffold(descriptor, scaffold)
        _validate_initializing_root(descriptor, expected_manifest=manifest)
        _verify_named_identity(root, descriptor, identity)
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
        _verify_named_identity(root, descriptor, identity)
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

    root = _require_lexical_root(root)
    if not _valid_identity(expected_identity):
        raise WorkspaceSecurityError("workspace root identity is unavailable")
    assert expected_identity is not None
    descriptor = _open_existing_root(root, expected_identity)
    try:
        _verify_ready(descriptor, root, journal_present=False)
        _verify_named_identity(root, descriptor, expected_identity)
        return expected_identity
    finally:
        os.close(descriptor)


def inspect_session_resources(
    root: Path,
    session_id: str,
) -> GitSessionResourceInspection:
    """Inspect one session through the bounded workspace-owned composition seam."""

    manifest = _baseline_manifest(canonical_initializer_scaffold())
    return _inspect_git_session_resources(_main_root(root), manifest, session_id)


def inspect_session_history(
    root: Path,
    session_id: str,
) -> GitSessionHistorySnapshot:
    """Return one immutable complete history snapshot without creating storage."""

    manifest = _baseline_manifest(canonical_initializer_scaffold())
    with open_git_session_history(
        _main_root(root),
        manifest,
        session_id,
        create=False,
    ) as history:
        return history.snapshot()


def list_session_resources(root: Path) -> GitSessionResourceInventory:
    """List the complete correlated session inventory without mutation."""

    manifest = _baseline_manifest(canonical_initializer_scaffold())
    return _list_git_session_resources(_main_root(root), manifest)


def provision_session_resources(
    root: Path,
    session_id: str,
) -> GitSessionResourceInspection:
    """Create or resume one exact session triple under the repository lock."""

    main_root = _main_root(root)
    manifest = _baseline_manifest(canonical_initializer_scaffold())
    return _provision_git_session_resources(main_root, manifest, session_id)


def require_ready_session_resources(
    root: Path,
    session_id: str,
) -> GitSessionResourceInspection:
    """Require one exact ready session triple without mutation."""

    manifest = _baseline_manifest(canonical_initializer_scaffold())
    return _require_ready_git_session_resources(
        _main_root(root),
        manifest,
        session_id,
    )


def compose_story_commit_service(root: Path, session_id: str) -> StoryCommitService:
    """Compose commit authority for one already-ready workspace session."""

    main_root = _main_root(root)
    manifest = _baseline_manifest(canonical_initializer_scaffold())
    _require_ready_git_session_resources(main_root, manifest, session_id)
    return StoryCommitService(main_root, manifest, session_id)


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
    current = _lexical_absolute(start)
    try:
        if not current.is_dir():
            current = current.parent
    except OSError:
        current = current.parent
    for candidate in (current, *current.parents):
        if _recognizable_boundary(candidate):
            return candidate
    return None


def _verify_ready(
    descriptor: int,
    root: Path,
    *,
    journal_present: bool,
    manifest: GitBaselineManifest | None = None,
) -> tuple[WorkspaceRootRole, str | None, Path]:
    scaffold = canonical_initializer_scaffold()
    manifest = manifest or _baseline_manifest(scaffold)
    role, session_id, main_root = _workspace_role(root)
    if role is WorkspaceRootRole.SESSION:
        try:
            marker_bytes = _read_relative_regular(descriptor, WORKSPACE_MARKER_PATH)
        except OSError:
            raise WorkspaceFormatError("workspace marker is missing") from None
        decode_workspace_marker(marker_bytes)
        assert session_id is not None
        try:
            _require_ready_git_session_resources(root, manifest, session_id)
        except GitSessionResourceError:
            raise WorkspaceFormatError(
                "workspace session resources require recovery"
            ) from None
        return role, session_id, main_root
    snapshot = _snapshot_relative_files(descriptor)
    marker_bytes = snapshot.get(WORKSPACE_MARKER_PATH)
    if marker_bytes is None:
        raise WorkspaceFormatError("workspace marker is missing")
    decode_workspace_marker(marker_bytes)
    _require_directories(descriptor)
    allowed_files = {entry.path: entry.content for entry in scaffold}
    if journal_present:
        journal_bytes = snapshot.get(WORKSPACE_INITIALIZATION_JOURNAL_PATH)
        if journal_bytes is None:
            raise WorkspaceFormatError("workspace initialization journal is missing")
        journal = decode_initialization_journal(journal_bytes)
        if journal.manifest_digest != manifest.manifest_digest:
            raise WorkspaceFormatError("workspace initialization journal is invalid")
        allowed_files[WORKSPACE_INITIALIZATION_JOURNAL_PATH] = journal_bytes
    elif WORKSPACE_INITIALIZATION_JOURNAL_PATH in snapshot:
        raise WorkspaceFormatError("workspace initialization requires recovery")
    scaffold_snapshot = {
        path: content
        for path, content in snapshot.items()
        if not _is_consensus_runtime_file(path)
    }
    if scaffold_snapshot != allowed_files:
        raise WorkspaceFormatError("workspace scaffold requires recovery")
    if not journal_present:
        try:
            inventory = _list_git_session_resources(root, manifest)
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


def _workspace_role(root: Path) -> tuple[WorkspaceRootRole, str | None, Path]:
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


def _main_root(root: Path) -> Path:
    lexical = _require_lexical_root(root)
    _role, _session_id, main_root = _workspace_role(lexical)
    return main_root


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
    scaffold = canonical_initializer_scaffold()
    manifest = expected_manifest or _baseline_manifest(scaffold)
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
        child = _walk_directories(
            descriptor,
            Path(relative).parts,
            create=True,
        )
        os.close(child)
    existing = _snapshot_relative_files(descriptor)
    for entry in scaffold:
        current = existing.get(entry.path)
        if current is None:
            _write_create_only(descriptor, entry.path, entry.content)
        elif current != entry.content:
            raise WorkspaceFormatError("workspace partial scaffold requires recovery")


def _baseline_manifest(
    scaffold: tuple[WorkspaceScaffoldEntry, ...],
) -> GitBaselineManifest:
    return GitBaselineManifest(
        tuple(
            GitManifestEntry(entry.path, hashlib.sha256(entry.content).hexdigest())
            for entry in scaffold
        )
    )


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
                if relative not in allowed_directories and not candidate_shard:
                    raise WorkspaceFormatError(
                        "workspace contains an undeclared directory"
                    )
                child = _open_directory(directory_descriptor, name, create=False)
                try:
                    visit(child, (*prefix, name))
                finally:
                    os.close(child)
            elif stat.S_ISREG(value.st_mode):
                result[relative] = _read_regular_file(directory_descriptor, name)
            else:
                raise WorkspaceSecurityError("workspace contains an unsafe entry")

    visit(descriptor, ())
    return result


def _is_consensus_runtime_file(path: str) -> bool:
    """Recognize only canonical runtime-owned consensus storage leaves."""

    workflow_prefix = "agent_states/orchestrator_state/consensus_workflows/v2/"
    if path.startswith(workflow_prefix):
        name = path.removeprefix(workflow_prefix)
        return name == ".store.lock" or (
            len(name) == 69
            and name.endswith(".json")
            and all(character in "0123456789abcdef" for character in name[:-5])
        )
    material_prefix = "agent_states/orchestrator_state/consensus_materials/v1/"
    if not path.startswith(material_prefix):
        return False
    relative = path.removeprefix(material_prefix)
    parts = relative.split("/")
    if len(parts) != 2 or len(parts[0]) != 2 or any(
        character not in "0123456789abcdef" for character in parts[0]
    ):
        return False
    if parts[1] == ".lock":
        return True
    return (
        len(parts[1]) == 67
        and parts[1].endswith(".utf8")
        and all(character in "0123456789abcdef" for character in parts[1][:-5])
    )


def _workspace_marker_present(descriptor: int) -> bool:
    try:
        _read_relative_regular(descriptor, WORKSPACE_MARKER_PATH)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        raise WorkspaceFormatError("workspace marker cannot be inspected") from None
    return True


def _read_relative_regular(descriptor: int, relative: str) -> bytes:
    parts = Path(relative).parts
    parent = _walk_directories(descriptor, parts[:-1], create=False)
    try:
        return _read_regular_file(parent, parts[-1])
    finally:
        os.close(parent)


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
            child = _walk_directories(
                descriptor,
                Path(relative).parts,
                create=False,
            )
        except (FileNotFoundError, NotADirectoryError, OSError):
            raise WorkspaceFormatError("workspace scaffold requires recovery") from None
        os.close(child)


def _read_regular_file(parent: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    for option in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY"):
        flags |= getattr(os, option, 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_size > 64 * 1024 * 1024:
            raise WorkspaceFormatError("workspace file is invalid")
        chunks: list[bytes] = []
        remaining = value.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise WorkspaceFormatError("workspace file changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise WorkspaceFormatError("workspace file changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_create_only(root_descriptor: int, relative: str, content: bytes) -> None:
    raw = Path(relative)
    parent = _walk_directories(root_descriptor, raw.parts[:-1], create=True)
    temporary: str | None = None
    temporary_descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        for option in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY"):
            flags |= getattr(os, option, 0)
        for attempt in range(16):
            temporary = f".{raw.name}.{uuid4().hex}.tmp"
            try:
                temporary_descriptor = os.open(
                    temporary,
                    flags,
                    0o600,
                    dir_fd=parent,
                )
                break
            except FileExistsError:
                if attempt == 15:
                    raise
        assert temporary_descriptor is not None and temporary is not None
        remaining = memoryview(content)
        while remaining:
            written = os.write(temporary_descriptor, remaining)
            if written <= 0:
                raise OSError(errno.EIO, "workspace write made no progress")
            remaining = remaining[written:]
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        try:
            os.link(
                temporary,
                raw.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing = _read_regular_file(parent, raw.name)
            if existing != content:
                raise WorkspaceFormatError(
                    "workspace partial scaffold requires recovery"
                )
        os.unlink(temporary, dir_fd=parent)
        temporary = None
        os.fsync(parent)
    finally:
        if temporary_descriptor is not None:
            try:
                os.close(temporary_descriptor)
            except BaseException:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent)
            except BaseException:
                pass
        os.close(parent)


def _create_and_bind_root(root: Path) -> tuple[int, tuple[int, int]]:
    flags = _directory_flags()
    anchor = os.open(root.anchor, flags)
    descriptor = _walk_directories(
        anchor,
        root.parts[1:],
        create=True,
        close_start=True,
    )
    identity = _meaningful_directory_identity(os.fstat(descriptor))
    if identity is None:
        os.close(descriptor)
        raise WorkspaceSecurityError("workspace root identity is unavailable")
    _verify_named_identity(root, descriptor, identity)
    return descriptor, identity


def _open_existing_root(root: Path, expected: tuple[int, int]) -> int:
    descriptor = os.open(root, _directory_flags())
    try:
        _verify_named_identity(root, descriptor, expected)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_named_identity(
    root: Path,
    descriptor: int,
    expected: tuple[int, int],
) -> None:
    opened = _meaningful_directory_identity(os.fstat(descriptor))
    named = _meaningful_directory_identity(os.stat(root, follow_symlinks=False))
    if opened != expected or named != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")


def _walk_directories(
    start: int,
    components: tuple[str, ...],
    *,
    create: bool,
    close_start: bool = False,
) -> int:
    descriptor: int | None = start if close_start else None
    parent = start
    try:
        for component in components:
            child = _open_directory(parent, component, create=create)
            if descriptor is not None:
                os.close(descriptor)
            descriptor = child
            parent = child
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                pass
        raise
    if descriptor is None:
        return os.dup(start)
    return descriptor


def _open_directory(parent: int, name: str, *, create: bool) -> int:
    try:
        return os.open(name, _directory_flags(), dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, dir_fd=parent)
        except FileExistsError:
            pass
        return os.open(name, _directory_flags(), dir_fd=parent)


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
    return any(path in files for path in _LEGACY_SENTINELS)


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
            for path in _LEGACY_SENTINELS
        )
    except OSError:
        return False


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


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _require_lexical_root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("root must be a Path")
    root = _lexical_absolute(path)
    if root != path:
        raise WorkspaceSecurityError("workspace root must be lexical and absolute")
    return root


def _directory_flags() -> int:
    flags = os.O_RDONLY
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, name, 0)
    return flags


def _meaningful_directory_identity(value: os.stat_result) -> tuple[int, int] | None:
    if not stat.S_ISDIR(value.st_mode):
        return None
    identity = (value.st_dev, value.st_ino)
    return identity if _valid_identity(identity) else None


def _valid_identity(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(type(item) is int and item > 0 for item in value)
    )


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
