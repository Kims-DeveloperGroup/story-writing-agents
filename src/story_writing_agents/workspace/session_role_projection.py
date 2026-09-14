"""Guarded Antigravity role projection inside one session worktree."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..agents import ROSTER
from ..provider_execution_workspace import ProviderExecutionWorkspace


_AGENTS_ROOT = ".agents"
_AGENTS_DIRECTORY = "agents"
_ROLE_FILE = "agent.md"
_EXCLUDE_LINE = b"/.agents/"
_MAX_EXCLUDE_BYTES = 64 * 1024
_ROLE_SELECTOR_DOMAIN = (
    b"story-writing-agents:antigravity-session-role-selector:v1\0"
)
_RegularFileIdentity = tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class SessionRoleProjection:
    """Package-internal identity of one validated projected role leaf."""

    native_agent_id: str
    path: Path
    _file_identity: _RegularFileIdentity


def qualify_session_role_id(agent_id: str, base_content: bytes) -> str:
    """Derive one strict native selector from semantic ID and base bytes."""

    if (
        agent_id not in ROSTER
        or type(base_content) is not bytes
        or not base_content
    ):
        raise OSError("session role projection is invalid")
    digest = hashlib.sha256(_ROLE_SELECTOR_DOMAIN + base_content).hexdigest()
    selector = f"{agent_id}-{digest}"
    if _semantic_role_id(selector) != agent_id:
        raise OSError("session role projection is invalid")
    return selector


def install_session_role_projection(
    worktree_descriptor: int,
    common_git_descriptor: int,
    *,
    native_agent_id: str,
    content: bytes,
) -> None:
    """Install one exact role and its Git exclusion through retained roots."""

    _require_projection_operations()
    _validate_inputs(
        worktree_descriptor,
        common_git_descriptor,
        native_agent_id,
        content,
    )
    _preflight_projection(
        worktree_descriptor,
        native_agent_id=native_agent_id,
        content=content,
    )
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
                if not all(
                    _semantic_role_id(name) is not None
                    for name in os.listdir(agents)
                ):
                    raise OSError(
                        "session role projection contains unexpected entries"
                    )
                selected = _open_projection_directory(
                    agents,
                    native_agent_id,
                    parts=(
                        _AGENTS_ROOT,
                        _AGENTS_DIRECTORY,
                        native_agent_id,
                    ),
                    created=created,
                )
                try:
                    _require_selected_directory(selected)
                    _install_immutable_regular(
                        selected,
                        _ROLE_FILE,
                        content,
                        mode=0o400,
                    )
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
    native_agent_id: str,
    content: bytes,
    expected: SessionRoleProjection | None = None,
) -> SessionRoleProjection:
    """Require exact projected bytes through the retained session capability."""

    _require_projection_operations()
    if (
        type(execution_workspace) is not ProviderExecutionWorkspace
        or _semantic_role_id(native_agent_id) is None
        or type(content) is not bytes
        or not content
        or (
            expected is not None
            and type(expected) is not SessionRoleProjection
        )
    ):
        raise OSError("session role projection is invalid")
    execution_workspace.require_current()
    descriptor = execution_workspace.duplicate_validation_descriptor()
    try:
        root = _open_existing_directory(descriptor, _AGENTS_ROOT)
        try:
            agents = _open_existing_directory(root, _AGENTS_DIRECTORY)
            try:
                selected = _open_existing_directory(agents, native_agent_id)
                try:
                    _require_selected_directory(selected)
                    observed, file_identity = _read_regular_identity(
                        selected,
                        _ROLE_FILE,
                        immutable=True,
                        exact_mode=0o400,
                    )
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
    projected_path = (
        execution_workspace.worktree_root
        / _AGENTS_ROOT
        / _AGENTS_DIRECTORY
        / native_agent_id
        / _ROLE_FILE
    )
    if (
        observed != content
        or (
            expected is not None
            and (
                expected.native_agent_id != native_agent_id
                or expected.path != projected_path
                or expected._file_identity != file_identity
            )
        )
    ):
        raise OSError("session role projection changed")
    execution_workspace.require_current()
    return SessionRoleProjection(
        native_agent_id=native_agent_id,
        path=projected_path,
        _file_identity=file_identity,
    )


def _validate_inputs(
    worktree_descriptor: int,
    common_git_descriptor: int,
    native_agent_id: str,
    content: bytes,
) -> None:
    if (
        type(worktree_descriptor) is not int
        or type(common_git_descriptor) is not int
        or _semantic_role_id(native_agent_id) is None
        or type(content) is not bytes
        or not content
        or not _safe_directory(os.fstat(worktree_descriptor))
        or not _safe_directory(os.fstat(common_git_descriptor))
    ):
        raise OSError("session role projection is invalid")


def _semantic_role_id(native_agent_id: object) -> str | None:
    if type(native_agent_id) is not str or len(native_agent_id) > 128:
        return None
    for agent_id in ROSTER:
        prefix = f"{agent_id}-"
        if not native_agent_id.startswith(prefix):
            continue
        digest = native_agent_id[len(prefix):]
        if len(digest) == 64 and all(
            character in "0123456789abcdef" for character in digest
        ):
            return agent_id
    return None


def _require_projection_operations() -> None:
    """Fail closed without descriptor-relative no-follow projection support."""

    required_callables = {
        name: getattr(os, name, None)
        for name in (
            "close",
            "dup",
            "fchmod",
            "fstat",
            "fsync",
            "geteuid",
            "link",
            "listdir",
            "mkdir",
            "open",
            "read",
            "replace",
            "rmdir",
            "set_inheritable",
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
            for name in ("link", "mkdir", "open", "rmdir", "stat", "unlink")
        )
        or any(
            required_callables[name] not in supports_follow_symlinks
            for name in ("link", "stat")
        )
        or required_callables["listdir"] not in supports_fd
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe session role projection is unsupported",
        )


def _git_exclude_lines(content: bytes) -> tuple[bytes, ...]:
    """Split only on LF and normalize one CR immediately preceding it."""

    lines: list[bytes] = []
    start = 0
    while True:
        end = content.find(b"\n", start)
        if end < 0:
            lines.append(content[start:])
            return tuple(lines)
        line = content[start:end]
        lines.append(line[:-1] if line.endswith(b"\r") else line)
        start = end + 1


def _ensure_git_exclusion(common_git_descriptor: int) -> None:
    info = _open_or_create_directory(common_git_descriptor, "info")
    try:
        try:
            current = _read_regular(info, "exclude")
        except FileNotFoundError:
            current = b""
        except OSError:
            raise OSError("session role Git exclusion is unsafe") from None
        lines = _git_exclude_lines(current)
        if _EXCLUDE_LINE in lines:
            return
        if current.endswith(b"\r"):
            raise OSError("session role Git exclusion is unsafe")
        separator = b"" if not current or current.endswith(b"\n") else b"\n"
        replacement = current + separator + _EXCLUDE_LINE + b"\n"
        if len(replacement) > _MAX_EXCLUDE_BYTES:
            raise OSError("session role Git exclusion is unsafe")
        _install_regular(
            info,
            "exclude",
            replacement,
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
    native_agent_id: str,
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
            if not all(_semantic_role_id(name) is not None for name in names):
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
                            exact_mode=0o400,
                        )
                        if name == native_agent_id and observed != content:
                            raise OSError("session role projection target differs")
                finally:
                    os.close(selected)
        finally:
            os.close(agents)
    finally:
        os.close(root)


def _require_agents_directory(descriptor: int) -> None:
    names = set(os.listdir(descriptor))
    if not all(_semantic_role_id(name) is not None for name in names):
        raise OSError("session role projection contains unexpected entries")
    for name in names:
        child = _open_existing_directory(descriptor, name)
        try:
            _require_selected_directory(child)
            _read_regular(
                child,
                _ROLE_FILE,
                immutable=True,
                exact_mode=0o400,
            )
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
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        os.set_inheritable(descriptor, False)
        if not _safe_directory(os.fstat(descriptor)):
            raise OSError("session role projection directory is unsafe")
        return descriptor
    except BaseException as failure:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except BaseException as cleanup_failure:
                try:
                    failure.add_note(
                        "session role projection descriptor cleanup failed: "
                        f"{type(cleanup_failure).__name__}"
                    )
                except BaseException:
                    pass
        if not isinstance(failure, Exception) or isinstance(failure, OSError):
            raise
        raise OSError("session role projection directory is unsafe") from failure


def _safe_directory(value: os.stat_result) -> bool:
    effective_user_id = _effective_user_id()
    return (
        effective_user_id is not None
        and stat.S_ISDIR(value.st_mode)
        and value.st_uid == effective_user_id
        and stat.S_IMODE(value.st_mode) & 0o022 == 0
    )


def _read_regular(
    parent: int,
    name: str,
    *,
    immutable: bool = False,
    exact_mode: int | None = None,
) -> bytes:
    content, _identity = _read_regular_identity(
        parent,
        name,
        immutable=immutable,
        exact_mode=exact_mode,
    )
    return content


def _read_regular_identity(
    parent: int,
    name: str,
    *,
    immutable: bool = False,
    exact_mode: int | None = None,
) -> tuple[bytes, _RegularFileIdentity]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        raise
    try:
        os.set_inheritable(descriptor, False)
        value = os.fstat(descriptor)
        if not _safe_regular(
            value,
            immutable=immutable,
            exact_mode=exact_mode,
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
        final_value = os.fstat(descriptor)
        if (
            not _safe_regular(
                final_value,
                immutable=immutable,
                exact_mode=exact_mode,
            )
            or _regular_identity(final_value) != identity
        ):
            raise OSError("session role projection file changed")
    finally:
        os.close(descriptor)
    named_value = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        not _safe_regular(
            named_value,
            immutable=immutable,
            exact_mode=exact_mode,
        )
        or _regular_identity(named_value) != identity
    ):
        raise OSError("session role projection file changed")
    return b"".join(chunks), identity


def _safe_regular(
    value: os.stat_result,
    *,
    immutable: bool,
    exact_mode: int | None,
) -> bool:
    mode = stat.S_IMODE(value.st_mode)
    effective_user_id = _effective_user_id()
    return (
        effective_user_id is not None
        and stat.S_ISREG(value.st_mode)
        and value.st_nlink == 1
        and value.st_uid == effective_user_id
        and mode & 0o022 == 0
        and (not immutable or mode & 0o222 == 0)
        and (exact_mode is None or mode == exact_mode)
    )


def _effective_user_id() -> int | None:
    operation = getattr(os, "geteuid", None)
    if not callable(operation):
        return None
    try:
        value = operation()
    except Exception:
        return None
    return value if type(value) is int and value >= 0 else None


def _regular_identity(
    value: os.stat_result,
) -> _RegularFileIdentity:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
    )


def _install_immutable_regular(
    parent: int,
    name: str,
    content: bytes,
    *,
    mode: int,
) -> None:
    """Publish one immutable leaf with atomic create-only semantics."""

    try:
        current = _read_regular(
            parent,
            name,
            immutable=True,
            exact_mode=mode,
        )
    except FileNotFoundError:
        current = None
    except OSError:
        raise OSError("session role projection target is unsafe") from None
    if current is not None:
        if current == content:
            return
        raise OSError("session role projection target differs")

    temporary = f".{name}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
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
        if (
            not _safe_regular(
                value,
                immutable=True,
                exact_mode=mode,
            )
            or value.st_size != len(content)
        ):
            raise OSError("session role projection temporary is unsafe")
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except FileExistsError:
            os.unlink(temporary, dir_fd=parent)
            present = False
            os.fsync(parent)
            try:
                winner = _read_regular(
                    parent,
                    name,
                    immutable=True,
                    exact_mode=mode,
                )
            except OSError:
                raise OSError(
                    "session role projection target is unsafe"
                ) from None
            if winner != content:
                raise OSError("session role projection target differs")
            return
        os.unlink(temporary, dir_fd=parent)
        present = False
        os.fsync(parent)
        if _read_regular(
            parent,
            name,
            immutable=True,
            exact_mode=mode,
        ) != content:
            raise OSError("session role projection install failed")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if present:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass


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
            exact_mode=None if replace_existing else mode,
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
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
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
        if _read_regular(
            parent,
            name,
            immutable=not replace_existing,
            exact_mode=mode,
        ) != content:
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
