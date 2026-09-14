"""Guarded Antigravity role projection inside one session worktree."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4

from ..agents import ROSTER
from ..provider_execution_workspace import ProviderExecutionWorkspace


_AGENTS_ROOT = ".agents"
_AGENTS_DIRECTORY = "agents"
_ROLE_FILE = "agent.md"
_EXCLUDE_LINE = b"/.agents/"
_MAX_EXCLUDE_BYTES = 64 * 1024


def install_session_role_projection(
    worktree_descriptor: int,
    common_git_descriptor: int,
    *,
    agent_id: str,
    content: bytes,
) -> None:
    """Install one exact role and its Git exclusion through retained roots."""

    _validate_inputs(worktree_descriptor, common_git_descriptor, agent_id, content)
    _preflight_projection(worktree_descriptor, agent_id=agent_id, content=content)
    _ensure_git_exclusion(common_git_descriptor)
    created: list[tuple[tuple[str, ...], tuple[int, int]]] = []
    try:
        root = _open_projection_directory(
            worktree_descriptor,
            _AGENTS_ROOT,
            parts=(_AGENTS_ROOT,),
            created=created,
        )
        try:
            if not set(os.listdir(root)) <= {_AGENTS_DIRECTORY}:
                raise OSError("session role projection contains unexpected entries")
            agents = _open_projection_directory(
                root,
                _AGENTS_DIRECTORY,
                parts=(_AGENTS_ROOT, _AGENTS_DIRECTORY),
                created=created,
            )
            try:
                if not set(os.listdir(agents)) <= set(ROSTER):
                    raise OSError(
                        "session role projection contains unexpected entries"
                    )
                selected = _open_projection_directory(
                    agents,
                    agent_id,
                    parts=(_AGENTS_ROOT, _AGENTS_DIRECTORY, agent_id),
                    created=created,
                )
                try:
                    _require_selected_directory(selected)
                    _install_regular(selected, _ROLE_FILE, content, mode=0o400)
                finally:
                    os.close(selected)
                _require_agents_directory(agents)
            finally:
                os.close(agents)
            _require_root_directory(root)
        finally:
            os.close(root)
    except BaseException as failure:
        _cleanup_created_projection_directories(
            worktree_descriptor,
            created,
            failure=failure,
        )
        raise


def require_session_role_projection(
    execution_workspace: ProviderExecutionWorkspace,
    *,
    agent_id: str,
    content: bytes,
) -> Path:
    """Require exact projected bytes through the retained session capability."""

    if (
        type(execution_workspace) is not ProviderExecutionWorkspace
        or agent_id not in ROSTER
        or type(content) is not bytes
        or not content
    ):
        raise OSError("session role projection is invalid")
    execution_workspace.require_current()
    descriptor = execution_workspace.duplicate_validation_descriptor()
    try:
        root = _open_existing_directory(descriptor, _AGENTS_ROOT)
        try:
            agents = _open_existing_directory(root, _AGENTS_DIRECTORY)
            try:
                selected = _open_existing_directory(agents, agent_id)
                try:
                    _require_selected_directory(selected)
                    observed = _read_regular(selected, _ROLE_FILE, immutable=True)
                finally:
                    os.close(selected)
                _require_agents_directory(agents)
            finally:
                os.close(agents)
            _require_root_directory(root)
        finally:
            os.close(root)
    finally:
        os.close(descriptor)
    if observed != content:
        raise OSError("session role projection changed")
    execution_workspace.require_current()
    return (
        execution_workspace.worktree_root
        / _AGENTS_ROOT
        / _AGENTS_DIRECTORY
        / agent_id
        / _ROLE_FILE
    )


def _validate_inputs(
    worktree_descriptor: int,
    common_git_descriptor: int,
    agent_id: str,
    content: bytes,
) -> None:
    if (
        type(worktree_descriptor) is not int
        or type(common_git_descriptor) is not int
        or agent_id not in ROSTER
        or type(content) is not bytes
        or not content
        or not _safe_directory(os.fstat(worktree_descriptor))
        or not _safe_directory(os.fstat(common_git_descriptor))
    ):
        raise OSError("session role projection is invalid")


def _ensure_git_exclusion(common_git_descriptor: int) -> None:
    info = _open_or_create_directory(common_git_descriptor, "info")
    try:
        try:
            current = _read_regular(info, "exclude")
        except FileNotFoundError:
            current = b""
        except OSError:
            raise OSError("session role Git exclusion is unsafe") from None
        lines = current.splitlines()
        if _EXCLUDE_LINE in lines:
            return
        if len(current) > _MAX_EXCLUDE_BYTES:
            raise OSError("session role Git exclusion is unsafe")
        separator = b"" if not current or current.endswith(b"\n") else b"\n"
        _install_regular(
            info,
            "exclude",
            current + separator + _EXCLUDE_LINE + b"\n",
            mode=0o600,
            replace_existing=True,
        )
    finally:
        os.close(info)


def _require_root_directory(descriptor: int) -> None:
    names = set(os.listdir(descriptor))
    if names != {_AGENTS_DIRECTORY}:
        raise OSError("session role projection contains unexpected entries")


def _preflight_projection(
    worktree_descriptor: int,
    *,
    agent_id: str,
    content: bytes,
) -> None:
    try:
        root = _open_existing_directory(worktree_descriptor, _AGENTS_ROOT)
    except FileNotFoundError:
        return
    try:
        names = set(os.listdir(root))
        if not names <= {_AGENTS_DIRECTORY}:
            raise OSError("session role projection contains unexpected entries")
        if _AGENTS_DIRECTORY not in names:
            return
        agents = _open_existing_directory(root, _AGENTS_DIRECTORY)
        try:
            names = set(os.listdir(agents))
            if not names <= set(ROSTER):
                raise OSError("session role projection contains unexpected entries")
            for name in names:
                selected = _open_existing_directory(agents, name)
                try:
                    _require_selected_directory(selected)
                    entries = set(os.listdir(selected))
                    if _ROLE_FILE in entries:
                        observed = _read_regular(
                            selected,
                            _ROLE_FILE,
                            immutable=True,
                        )
                        if name == agent_id and observed != content:
                            raise OSError("session role projection target differs")
                finally:
                    os.close(selected)
        finally:
            os.close(agents)
    finally:
        os.close(root)


def _require_agents_directory(descriptor: int) -> None:
    names = set(os.listdir(descriptor))
    if not names <= set(ROSTER):
        raise OSError("session role projection contains unexpected entries")
    for name in names:
        child = _open_existing_directory(descriptor, name)
        try:
            _require_selected_directory(child)
            _read_regular(child, _ROLE_FILE, immutable=True)
        finally:
            os.close(child)


def _require_selected_directory(descriptor: int) -> None:
    if set(os.listdir(descriptor)) not in (set(), {_ROLE_FILE}):
        raise OSError("session role projection contains unexpected entries")


def _open_or_create_directory(parent: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        created = True
    except FileExistsError:
        pass
    descriptor = _open_existing_directory(parent, name)
    try:
        if created:
            os.fchmod(descriptor, 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_projection_directory(
    parent: int,
    name: str,
    *,
    parts: tuple[str, ...],
    created: list[tuple[tuple[str, ...], tuple[int, int]]],
) -> int:
    """Open one projection directory and record only call-owned creation."""

    existed = True
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        existed = False
    except FileExistsError:
        pass
    descriptor = _open_existing_directory(parent, name)
    try:
        if not existed:
            observed = os.fstat(descriptor)
            created.append((parts, (observed.st_dev, observed.st_ino)))
            os.fchmod(descriptor, 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _cleanup_created_projection_directories(
    worktree_descriptor: int,
    created: list[tuple[tuple[str, ...], tuple[int, int]]],
    *,
    failure: BaseException,
) -> None:
    """Best-effort remove unchanged empty call-owned directories leaf first."""

    for parts, expected_identity in reversed(created):
        parent = -1
        child = -1
        try:
            parent = os.dup(worktree_descriptor)
            os.set_inheritable(parent, False)
            for component in parts[:-1]:
                next_parent = _open_existing_directory(parent, component)
                os.close(parent)
                parent = next_parent
            name = parts[-1]
            child = _open_existing_directory(parent, name)
            observed = os.fstat(child)
            if (
                (observed.st_dev, observed.st_ino) != expected_identity
                or os.listdir(child)
            ):
                continue
            os.close(child)
            child = -1
            os.rmdir(name, dir_fd=parent)
            os.fsync(parent)
        except FileNotFoundError:
            continue
        except BaseException as cleanup_failure:
            try:
                failure.add_note(
                    "session role projection cleanup failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            except BaseException:
                pass
        finally:
            if child >= 0:
                try:
                    os.close(child)
                except OSError:
                    pass
            if parent >= 0:
                try:
                    os.close(parent)
                except OSError:
                    pass


def _open_existing_directory(parent: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    os.set_inheritable(descriptor, False)
    if not _safe_directory(os.fstat(descriptor)):
        os.close(descriptor)
        raise OSError("session role projection directory is unsafe")
    return descriptor


def _safe_directory(value: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(value.st_mode)
        and value.st_uid == os.geteuid()
        and stat.S_IMODE(value.st_mode) & 0o022 == 0
    )


def _read_regular(parent: int, name: str, *, immutable: bool = False) -> bytes:
    content, _identity = _read_regular_identity(
        parent,
        name,
        immutable=immutable,
    )
    return content


def _read_regular_identity(
    parent: int,
    name: str,
    *,
    immutable: bool = False,
) -> tuple[bytes, tuple[int, int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        raise
    try:
        os.set_inheritable(descriptor, False)
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_nlink != 1
            or value.st_uid != os.geteuid()
            or stat.S_IMODE(value.st_mode) & 0o022
            or (immutable and stat.S_IMODE(value.st_mode) & 0o222)
        ):
            raise OSError("session role projection file is unsafe")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_EXCLUDE_BYTES and name == "exclude":
                raise OSError("session role Git exclusion is unsafe")
        identity = _regular_identity(value)
        if _regular_identity(os.fstat(descriptor)) != identity:
            raise OSError("session role projection file changed")
    finally:
        os.close(descriptor)
    if _regular_identity(
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    ) != identity:
        raise OSError("session role projection file changed")
    return b"".join(chunks), identity


def _regular_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        stat.S_IMODE(value.st_mode),
    )


def _install_regular(
    parent: int,
    name: str,
    content: bytes,
    *,
    mode: int,
    replace_existing: bool = False,
) -> None:
    try:
        current, initial_identity = _read_regular_identity(
            parent,
            name,
            immutable=not replace_existing,
        )
    except FileNotFoundError:
        current = None
        initial_identity = None
    except OSError:
        raise OSError("session role projection target is unsafe") from None
    if current is not None and not replace_existing:
        if current == content:
            return
        raise OSError("session role projection target differs")
    temporary = f".{name}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
    present = True
    try:
        os.set_inheritable(descriptor, False)
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("session role projection write failed")
            remaining = remaining[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise OSError("session role projection temporary is unsafe")
        try:
            current_identity = _regular_identity(
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            )
        except FileNotFoundError:
            current_identity = None
        if current_identity != initial_identity:
            raise OSError("session role projection target changed")
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        present = False
        if _read_regular(parent, name, immutable=not replace_existing) != content:
            raise OSError("session role projection install failed")
        os.fsync(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if present:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
