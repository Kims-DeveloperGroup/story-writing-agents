"""Physical path binding and descriptor-scoped Git resource I/O."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .session_contracts import (
    GitSessionFailureCode,
    GitSessionOperationKind,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
)
from .session_resource_values import MAX_SESSION_ADMIN_FILE_BYTES, resource_error


GenerationFileFacts = tuple[int, int, int, int, int, int, int]


def generation_file_facts(value: os.stat_result) -> GenerationFileFacts:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def require_named_generation_file(
    parent: int,
    name: str,
    descriptor: int,
    facts: GenerationFileFacts,
) -> None:
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        generation_file_facts(opened) != facts
        or generation_file_facts(named) != facts
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or os.get_inheritable(descriptor)
    ):
        raise OSError


def linked_worktree_common_git(
    root: Path,
) -> tuple[Path, tuple[int, int]] | None:
    root_descriptor = -1
    administrative_descriptor = -1
    try:
        root_identity = bind_directory(root)
        root_descriptor = open_bound_directory(root, root_identity)
        git_payload = read_file(root_descriptor, ".git")
        administrative = administrative_path(git_payload)
        administrative_identity = bind_directory(administrative)
        administrative_descriptor = open_bound_directory(
            administrative,
            administrative_identity,
        )
        common_payload = read_file(administrative_descriptor, "commondir")
        common_relative = common_git_relative_path(common_payload)
        common = Path(os.path.normpath(os.fspath(administrative / common_relative)))
        if (
            common.name != ".git"
            or administrative.parent != common / "worktrees"
            or administrative.name in {"", ".", ".."}
        ):
            raise ValueError
        common_identity = bind_directory(common)
        return common, common_identity
    except (GitSessionResourceError, OSError, UnicodeError, ValueError):
        return None
    finally:
        if administrative_descriptor >= 0:
            os.close(administrative_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


def administrative_path(payload: bytes) -> Path:
    prefix = b"gitdir: "
    if (
        not payload.startswith(prefix)
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
        or b"\r" in payload
        or b"\x00" in payload
    ):
        raise ValueError
    value = payload[len(prefix) : -1].decode("utf-8", errors="strict")
    administrative = Path(value)
    if (
        not value
        or not administrative.is_absolute()
        or administrative != Path(os.path.normpath(value))
    ):
        raise ValueError
    return administrative


def common_git_relative_path(payload: bytes) -> Path:
    if (
        not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
        or b"\r" in payload
        or b"\x00" in payload
    ):
        raise ValueError
    value = payload[:-1].decode("utf-8", errors="strict")
    relative = Path(value)
    if not value or relative.is_absolute():
        raise ValueError
    return relative


def bind_directory(path: Path) -> tuple[int, int]:
    try:
        value = os.lstat(path)
        canonical = Path(os.path.realpath(path))
    except OSError:
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or canonical != path
        or value.st_dev <= 0
        or value.st_ino <= 0
    ):
        raise resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return value.st_dev, value.st_ino


def open_bound_directory(path: Path, identity: tuple[int, int]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
    except OSError:
        failure = resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        close_generation_descriptor(descriptor, failure)
        raise failure from None
    except BaseException as failure:
        close_generation_descriptor(descriptor, failure)
        raise
    if (opened.st_dev, opened.st_ino) != identity or not stat.S_ISDIR(opened.st_mode):
        failure = resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
        close_generation_descriptor(descriptor, failure)
        raise failure
    return descriptor


def open_directory(parent: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise OSError
    return descriptor


def read_file(parent: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_SESSION_ADMIN_FILE_BYTES:
            raise OSError
        result = bytearray()
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            result.extend(chunk)
            if len(result) > MAX_SESSION_ADMIN_FILE_BYTES:
                raise OSError
        completed = os.fstat(descriptor)
        if len(result) != opened.st_size or (
            completed.st_dev,
            completed.st_ino,
            completed.st_mode,
            completed.st_size,
            completed.st_mtime_ns,
            completed.st_ctime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ):
            raise OSError
        return bytes(result)
    finally:
        os.close(descriptor)


def write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def path_lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def add_generation_descriptor_cleanup_note(
    failure: BaseException,
    cleanup_failure: BaseException,
) -> None:
    try:
        failure.add_note(
            "Git session generation descriptor cleanup failed: "
            f"{type(cleanup_failure).__name__}"
        )
    except BaseException:
        pass


def close_generation_descriptor(
    descriptor: int,
    failure: BaseException,
) -> None:
    """Close one factory-local descriptor without replacing its primary failure."""

    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except BaseException as cleanup_failure:
        add_generation_descriptor_cleanup_note(failure, cleanup_failure)
