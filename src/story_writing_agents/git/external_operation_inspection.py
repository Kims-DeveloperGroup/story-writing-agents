"""Inspect external Git operation markers with descriptor-scoped reads."""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import ExitStack
from pathlib import Path

from .adapter_contracts import AdapterFailure, bind_root
from .contracts import (
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitFailureCode,
    GitRepositoryState,
)


_MAX_ADMINISTRATIVE_FILE_BYTES = 4_096
_READ_CHUNK_BYTES = 64 * 1024
_EXTERNAL_OPERATION_MARKERS = (
    "MERGE_HEAD",
    "rebase-merge",
    "rebase-apply",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "sequencer",
)
_EXTERNAL_OPERATION_DIRECTORIES = frozenset(
    {"rebase-merge", "rebase-apply", "sequencer"}
)


def inspect_external_operation(root: Path) -> GitExternalOperationInspection:
    try:
        root_identity = bind_root(root)
        with ExitStack() as descriptors:
            root_descriptor = _open_bound_directory(root, root_identity)
            descriptors.callback(os.close, root_descriptor)
            root_snapshot = _descriptor_snapshot(root_descriptor)
            git_value = os.stat(
                ".git",
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            git_snapshot = _stat_snapshot(git_value)

            administrative = root / ".git"
            common_git = administrative
            git_file: tuple[bytes, tuple[int, ...]] | None = None
            common_file: tuple[bytes, tuple[int, ...]] | None = None
            backpointer_file: tuple[bytes, tuple[int, ...]] | None = None
            if stat.S_ISDIR(git_value.st_mode) and not stat.S_ISLNK(
                git_value.st_mode
            ):
                administrative_identity = _bind_directory(administrative)
                if administrative_identity != git_snapshot[:2]:
                    raise external_operation_failure()
                administrative_descriptor = _open_bound_directory(
                    administrative,
                    administrative_identity,
                )
                descriptors.callback(os.close, administrative_descriptor)
                common_identity = administrative_identity
                common_descriptor = administrative_descriptor
            elif stat.S_ISREG(git_value.st_mode) and not stat.S_ISLNK(
                git_value.st_mode
            ):
                git_file = _read_administrative_file(root_descriptor, ".git")
                if git_file[1] != git_snapshot:
                    raise external_operation_failure()
                administrative = _decode_git_directory(git_file[0])
                administrative_identity = _bind_directory(administrative)
                administrative_descriptor = _open_bound_directory(
                    administrative,
                    administrative_identity,
                )
                descriptors.callback(os.close, administrative_descriptor)

                common_file = _read_administrative_file(
                    administrative_descriptor,
                    "commondir",
                )
                common_relative = _decode_common_directory(common_file[0])
                common_git = Path(
                    os.path.normpath(
                        os.fspath(administrative / common_relative)
                    )
                )
                if (
                    common_git.name != ".git"
                    or administrative.parent != common_git / "worktrees"
                    or administrative.name in {"", ".", ".."}
                ):
                    raise external_operation_failure()
                common_identity = _bind_directory(common_git)
                common_descriptor = _open_bound_directory(
                    common_git,
                    common_identity,
                )
                descriptors.callback(os.close, common_descriptor)

                backpointer_file = _read_administrative_file(
                    administrative_descriptor,
                    "gitdir",
                )
                expected_backpointer = (
                    os.fspath(root / ".git").encode(
                        "utf-8",
                        errors="strict",
                    )
                    + b"\n"
                )
                if backpointer_file[0] != expected_backpointer:
                    raise external_operation_failure()
            else:
                raise external_operation_failure()

            administrative_snapshot = _descriptor_snapshot(
                administrative_descriptor
            )
            common_snapshot = _descriptor_snapshot(common_descriptor)
            first = {
                marker: _external_marker_snapshot(
                    administrative_descriptor,
                    marker,
                    directory=marker in _EXTERNAL_OPERATION_DIRECTORIES,
                )
                for marker in _EXTERNAL_OPERATION_MARKERS
            }
            second = {
                marker: _external_marker_snapshot(
                    administrative_descriptor,
                    marker,
                    directory=marker in _EXTERNAL_OPERATION_DIRECTORIES,
                )
                for marker in _EXTERNAL_OPERATION_MARKERS
            }
            if first != second:
                raise external_operation_failure()
            kind = _classify_external_operation(first)

            if (
                _descriptor_snapshot(root_descriptor) != root_snapshot
                or _descriptor_snapshot(administrative_descriptor)
                != administrative_snapshot
                or _descriptor_snapshot(common_descriptor) != common_snapshot
                or bind_root(root) != root_identity
                or _bind_directory(administrative) != administrative_identity
                or _bind_directory(common_git) != common_identity
            ):
                raise external_operation_failure()
            observed_git = os.stat(
                ".git",
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            if _stat_snapshot(observed_git) != git_snapshot:
                raise external_operation_failure()
            if git_file is not None and _read_administrative_file(
                root_descriptor,
                ".git",
            ) != git_file:
                raise external_operation_failure()
            if common_file is not None and _read_administrative_file(
                administrative_descriptor,
                "commondir",
            ) != common_file:
                raise external_operation_failure()
            if backpointer_file is not None and _read_administrative_file(
                administrative_descriptor,
                "gitdir",
            ) != backpointer_file:
                raise external_operation_failure()
            final = {
                marker: _external_marker_snapshot(
                    administrative_descriptor,
                    marker,
                    directory=marker in _EXTERNAL_OPERATION_DIRECTORIES,
                )
                for marker in _EXTERNAL_OPERATION_MARKERS
            }
            if final != first:
                raise external_operation_failure()

            return GitExternalOperationInspection(
                kind=kind,
                repository_identity_digest=_physical_identity_digest(
                    "repository",
                    common_identity,
                ),
                worktree_identity_digest=_physical_identity_digest(
                    "worktree",
                    root_identity,
                    administrative_identity,
                ),
            )
    except AdapterFailure:
        raise
    except (OSError, UnicodeError, ValueError):
        raise external_operation_failure() from None


def _bind_directory(path: Path) -> tuple[int, int]:
    try:
        value = os.lstat(path)
        canonical = Path(os.path.realpath(path))
    except OSError:
        raise external_operation_failure() from None
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or canonical != path
        or value.st_dev <= 0
        or value.st_ino <= 0
        or not value.st_mode & 0o444
        or not value.st_mode & 0o111
    ):
        raise external_operation_failure()
    return value.st_dev, value.st_ino


def _open_bound_directory(path: Path, identity: tuple[int, int]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise external_operation_failure() from None
    if (
        (opened.st_dev, opened.st_ino) != identity
        or not stat.S_ISDIR(opened.st_mode)
        or not opened.st_mode & 0o444
        or not opened.st_mode & 0o111
    ):
        os.close(descriptor)
        raise external_operation_failure()
    return descriptor


def _read_administrative_file(
    parent_descriptor: int,
    name: str,
) -> tuple[bytes, tuple[int, ...]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError:
        raise external_operation_failure() from None
    try:
        opened = os.fstat(descriptor)
        initial = _stat_snapshot(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not opened.st_mode & 0o444
            or opened.st_size < 0
            or opened.st_size > _MAX_ADMINISTRATIVE_FILE_BYTES
        ):
            raise external_operation_failure()
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > _MAX_ADMINISTRATIVE_FILE_BYTES:
                raise external_operation_failure()
        if (
            len(payload) != opened.st_size
            or _descriptor_snapshot(descriptor) != initial
        ):
            raise external_operation_failure()
        return bytes(payload), initial
    finally:
        os.close(descriptor)


def _decode_git_directory(payload: bytes) -> Path:
    prefix = b"gitdir: "
    if (
        not payload.startswith(prefix)
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
        or b"\r" in payload
        or b"\x00" in payload
    ):
        raise external_operation_failure()
    try:
        value = payload[len(prefix) : -1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise external_operation_failure() from None
    path = Path(value)
    if not value or not path.is_absolute() or path != Path(os.path.normpath(value)):
        raise external_operation_failure()
    return path


def _decode_common_directory(payload: bytes) -> Path:
    if (
        not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
        or b"\r" in payload
        or b"\x00" in payload
    ):
        raise external_operation_failure()
    try:
        value = payload[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise external_operation_failure() from None
    path = Path(value)
    if not value or path.is_absolute():
        raise external_operation_failure()
    return path


def _external_marker_snapshot(
    administrative_descriptor: int,
    name: str,
    *,
    directory: bool,
) -> tuple[int, ...] | None:
    try:
        value = os.stat(
            name,
            dir_fd=administrative_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise external_operation_failure() from None
    if directory:
        valid_type = stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode)
        valid_access = bool(value.st_mode & 0o444) and bool(value.st_mode & 0o111)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    else:
        valid_type = stat.S_ISREG(value.st_mode) and not stat.S_ISLNK(value.st_mode)
        valid_access = bool(value.st_mode & 0o444)
        flags = os.O_RDONLY
    if not valid_type or not valid_access:
        raise external_operation_failure()
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=administrative_descriptor)
    except OSError:
        raise external_operation_failure() from None
    try:
        opened = os.fstat(descriptor)
        snapshot = _stat_snapshot(value)
        if _stat_snapshot(opened) != snapshot:
            raise external_operation_failure()
        return snapshot
    finally:
        os.close(descriptor)


def _classify_external_operation(
    markers: dict[str, tuple[int, ...] | None],
) -> GitExternalOperationKind:
    present = {name for name, value in markers.items() if value is not None}
    merge = "MERGE_HEAD" in present
    rebase = present & {"rebase-merge", "rebase-apply"}
    cherry_pick = "CHERRY_PICK_HEAD" in present
    revert = "REVERT_HEAD" in present
    sequencer = "sequencer" in present
    if not present:
        return GitExternalOperationKind.NONE
    if merge and present == {"MERGE_HEAD"}:
        return GitExternalOperationKind.MERGE
    if len(rebase) == 1 and present == rebase:
        return GitExternalOperationKind.REBASE
    if cherry_pick and present in (
        {"CHERRY_PICK_HEAD"},
        {"CHERRY_PICK_HEAD", "sequencer"},
    ):
        return GitExternalOperationKind.CHERRY_PICK
    if revert and present in (
        {"REVERT_HEAD"},
        {"REVERT_HEAD", "sequencer"},
    ):
        return GitExternalOperationKind.REVERT
    if sequencer and present == {"sequencer"}:
        return GitExternalOperationKind.SEQUENCER
    raise external_operation_failure()


def _stat_snapshot(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _descriptor_snapshot(descriptor: int) -> tuple[int, ...]:
    try:
        return _stat_snapshot(os.fstat(descriptor))
    except OSError:
        raise external_operation_failure() from None


def _physical_identity_digest(label: str, *identities: tuple[int, int]) -> str:
    payload = "\0".join(
        (label, *(f"{device}:{inode}" for device, inode in identities))
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def external_operation_failure() -> AdapterFailure:
    return AdapterFailure(
        GitFailureCode.RECOVERY_REQUIRED,
        repository_state=GitRepositoryState.RECOVERY_REQUIRED,
    )
