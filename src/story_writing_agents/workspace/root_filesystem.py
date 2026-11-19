"""Descriptor-bound workspace filesystem operations."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Protocol, TypeVar
from uuid import uuid4

from ..errors import WorkspaceFormatError, WorkspaceSecurityError


def read_relative_regular(descriptor: int, relative: str) -> bytes:
    parts = Path(relative).parts
    parent = walk_directories(descriptor, parts[:-1], create=False)
    try:
        return read_regular_file(parent, parts[-1])
    finally:
        os.close(parent)


def read_regular_file(parent: int, name: str) -> bytes:
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


def write_create_only(root_descriptor: int, relative: str, content: bytes) -> None:
    raw = Path(relative)
    parent = walk_directories(root_descriptor, raw.parts[:-1], create=True)
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
            existing = read_regular_file(parent, raw.name)
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


def create_and_bind_root(root: Path) -> tuple[int, tuple[int, int]]:
    flags = _directory_flags()
    anchor = os.open(root.anchor, flags)
    descriptor = walk_directories(
        anchor,
        root.parts[1:],
        create=True,
        close_start=True,
    )
    identity = meaningful_directory_identity(os.fstat(descriptor))
    if identity is None:
        os.close(descriptor)
        raise WorkspaceSecurityError("workspace root identity is unavailable")
    verify_named_identity(root, descriptor, identity)
    return descriptor, identity


def open_existing_root(root: Path, expected: tuple[int, int]) -> int:
    descriptor = os.open(root, _directory_flags())
    try:
        verify_named_identity(root, descriptor, expected)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def verify_named_identity(
    root: Path,
    descriptor: int,
    expected: tuple[int, int],
) -> None:
    opened = meaningful_directory_identity(os.fstat(descriptor))
    named = meaningful_directory_identity(os.stat(root, follow_symlinks=False))
    if opened != expected or named != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")


def walk_directories(
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
            child = open_directory(parent, component, create=create)
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


def open_directory(parent: int, name: str, *, create: bool) -> int:
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


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def require_lexical_root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("root must be a Path")
    root = lexical_absolute(path)
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


def meaningful_directory_identity(value: os.stat_result) -> tuple[int, int] | None:
    if not stat.S_ISDIR(value.st_mode):
        return None
    identity = (value.st_dev, value.st_ino)
    return identity if valid_identity(identity) else None


def valid_identity(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(type(item) is int and item > 0 for item in value)
    )



class _WorkspaceRootPath(Protocol):
    @property
    def root(self) -> Path: ...


_WorkspaceRootT = TypeVar("_WorkspaceRootT", bound=_WorkspaceRootPath)


def open_workspace_root_descriptor(
    workspace: _WorkspaceRootT,
    expected: tuple[int, int],
    verify: Callable[[_WorkspaceRootT, int], None],
) -> int:
    """Open one named ready workspace and verify its descriptor before transfer."""
    _require_workspace_root_directory_operations()
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
    named_identity = _workspace_descriptor_identity(
        os.stat(workspace.root, follow_symlinks=False)
    )
    if named_identity != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")
    descriptor = os.open(workspace.root, flags)
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        verify(workspace, descriptor)
        return descriptor
    except BaseException as failure:
        failure_is_control = not isinstance(failure, Exception)
        cleanup_control: tuple[BaseException, TracebackType | None] | None = None
        try:
            os.close(descriptor)
        except Exception:
            pass
        except BaseException as cleanup_failure:
            cleanup_control = (
                cleanup_failure,
                cleanup_failure.__traceback__,
            )
        if not failure_is_control and cleanup_control is not None:
            cleanup_failure, cleanup_traceback = cleanup_control
            raise cleanup_failure.with_traceback(cleanup_traceback)
        raise


def verify_workspace_root_descriptor(
    workspace: _WorkspaceRootPath,
    descriptor: int,
    expected: tuple[int, int],
) -> None:
    """Check both the live descriptor and the canonical name against the identity."""
    _require_workspace_root_directory_operations()
    opened = os.fstat(descriptor)
    named = os.stat(workspace.root, follow_symlinks=False)
    opened_identity = _workspace_descriptor_identity(opened)
    named_identity = _workspace_descriptor_identity(named)
    if opened_identity != expected or named_identity != expected:
        raise WorkspaceSecurityError("workspace root directory identity changed")


def _require_workspace_root_directory_operations() -> None:
    """Fail closed without no-follow root-directory primitives."""

    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or os.stat not in supports_follow_symlinks
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe workspace root directory access is unsupported",
        )


def _workspace_descriptor_identity(
    value: os.stat_result,
) -> tuple[int, int] | None:
    """Return a nonzero directory identity or ``None`` when unavailable."""

    if not stat.S_ISDIR(value.st_mode):
        return None
    device = value.st_dev
    inode = value.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        return None
    return device, inode
