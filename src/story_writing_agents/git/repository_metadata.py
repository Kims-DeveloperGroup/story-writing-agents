"""Read bounded Git status, diff metadata, and log metadata."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .adapter_contracts import AdapterFailure, bind_root
from .adapter_execution import OBJECT_ID_PATTERN, GitAdapterExecution, decode_path
from .contracts import (
    GitChangeKind,
    GitDiffEntry,
    GitDiffMetadata,
    GitFailureCode,
    GitLogMetadata,
    GitRepositoryTopology,
    GitStatus,
    GitStatusEntry,
    GitTopologyKind,
)
from .external_operation_inspection import external_operation_failure, inspect_external_operation
from .repository_topology import inspect_topology


_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_STATUS_ENTRIES = 65_536
_MAX_LOG_ENTRIES = 128
_READ_CHUNK_BYTES = 64 * 1024


def status(execution: GitAdapterExecution) -> GitStatus:
    with _guard_status_or_log_read(execution) as identity:
        branch_ref = execution.symbolic_head(identity, allow_detached=True)
        branch = None
        if branch_ref is not None and branch_ref.startswith("refs/heads/"):
            branch = branch_ref.removeprefix("refs/heads/")
        head = execution.head_id(identity, allow_absent=True)
        entries, _ = _repository_changes(execution, identity, head)
        return GitStatus(
            branch=branch,
            head_id=head,
            entries=entries,
        )


def diff_metadata(execution: GitAdapterExecution) -> GitDiffMetadata:
    topology, identity = _require_standalone(execution)
    del topology
    head = execution.head_id(identity, allow_absent=True)
    _, entries = _repository_changes(execution, identity, head)
    return GitDiffMetadata(head_id=head, entries=entries)


def _repository_changes(
    execution: GitAdapterExecution,
    identity: tuple[int, int],
    head: str | None,
) -> tuple[tuple[GitStatusEntry, ...], tuple[GitDiffEntry, ...]]:
    index_entries, unmerged = _index_state(execution, identity)
    head_entries = {} if head is None else execution.tree_entries(head, identity)
    object_length = _object_length(index_entries, head_entries)
    zero = "0" * object_length
    status_entries: list[GitStatusEntry] = []
    diff_entries: list[GitDiffEntry] = []
    for path in sorted(
        set(head_entries) | set(index_entries) | unmerged,
        key=lambda item: item.encode("utf-8"),
    ):
        if path in unmerged:
            status_entries.append(
                GitStatusEntry(
                    path=path,
                    index_change=GitChangeKind.UNMERGED,
                    worktree_change=GitChangeKind.UNMERGED,
                )
            )
            continue
        head_value = head_entries.get(path)
        index_value = index_entries.get(path)
        index_change = _entry_change(head_value, index_value)
        worktree_change = _worktree_change(execution, path, index_value, identity)
        if index_change is not None or worktree_change is not None:
            status_entries.append(
                GitStatusEntry(
                    path=path,
                    index_change=index_change,
                    worktree_change=worktree_change,
                )
            )
        if index_change is not None:
            diff_entries.append(
                GitDiffEntry(
                    path=path,
                    previous_path=None,
                    change=index_change,
                    staged=True,
                    old_object_id=head_value[1] if head_value else zero,
                    new_object_id=index_value[1] if index_value else zero,
                )
            )
        if worktree_change is not None:
            diff_entries.append(
                GitDiffEntry(
                    path=path,
                    previous_path=None,
                    change=worktree_change,
                    staged=False,
                    old_object_id=index_value[1] if index_value else zero,
                    new_object_id=zero,
                )
            )
    for path in _untracked_paths(execution, identity):
        if path in index_entries:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        status_entries.append(
            GitStatusEntry(
                path=path,
                index_change=None,
                worktree_change=GitChangeKind.UNTRACKED,
            )
        )
    if len(status_entries) > _MAX_STATUS_ENTRIES:
        raise AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
    return tuple(status_entries), tuple(diff_entries)


def _worktree_change(
    execution: GitAdapterExecution,
    path: str,
    index_value: tuple[str, str] | None,
    identity: tuple[int, int],
) -> GitChangeKind | None:
    if index_value is None:
        return None
    mode, object_id = index_value
    target = execution.root.joinpath(*path.split("/"))
    current = execution.root
    for part in target.relative_to(execution.root).parts[:-1]:
        current /= part
        try:
            parent = os.lstat(current)
        except FileNotFoundError:
            return GitChangeKind.DELETED
        except OSError:
            raise AdapterFailure(GitFailureCode.IO_FAILED)
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            return GitChangeKind.TYPE_CHANGED
    try:
        value = os.lstat(target)
    except FileNotFoundError:
        return GitChangeKind.DELETED
    except OSError:
        raise AdapterFailure(GitFailureCode.IO_FAILED)
    if mode == "120000":
        if not stat.S_ISLNK(value.st_mode):
            return GitChangeKind.TYPE_CHANGED
        try:
            content = os.fsencode(os.readlink(target))
        except OSError:
            raise AdapterFailure(GitFailureCode.IO_FAILED)
    elif mode in {"100644", "100755"}:
        if not stat.S_ISREG(value.st_mode):
            return GitChangeKind.TYPE_CHANGED
        content = _read_bounded_file(target)
        executable = bool(value.st_mode & 0o111)
        if executable != (mode == "100755"):
            return GitChangeKind.TYPE_CHANGED
    else:
        return GitChangeKind.TYPE_CHANGED
    blob = execution.required_git(
        ("cat-file", "blob", object_id),
        identity=identity,
        read_only=True,
    ).stdout
    return None if blob == content else GitChangeKind.MODIFIED


def _read_bounded_file(target: Path) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
    except OSError:
        raise AdapterFailure(GitFailureCode.IO_FAILED)
    content = bytearray()
    try:
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > _MAX_FILE_BYTES:
                raise AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
    finally:
        os.close(descriptor)
    return bytes(content)


def _untracked_paths(execution: GitAdapterExecution, identity: tuple[int, int]) -> tuple[str, ...]:
    result = execution.required_git(
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
        identity=identity,
        read_only=True,
    )
    paths = tuple(decode_path(raw) for raw in result.stdout.split(b"\x00") if raw)
    if len(paths) > _MAX_STATUS_ENTRIES:
        raise AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
    return paths


def _index_state(
    execution: GitAdapterExecution,
    identity: tuple[int, int],
) -> tuple[dict[str, tuple[str, str]], set[str]]:
    result = execution.required_git(
        ("ls-files", "--stage", "-z", "--"),
        identity=identity,
        read_only=True,
    )
    entries: dict[str, tuple[str, str]] = {}
    unmerged: set[str] = set()
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        try:
            header, raw_path = raw.split(b"\t", 1)
            mode, object_id, stage = header.split(b" ")
        except ValueError:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        if OBJECT_ID_PATTERN.fullmatch(object_id) is None:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        path = decode_path(raw_path)
        if stage != b"0":
            unmerged.add(path)
            continue
        if path in entries:
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
    return entries, unmerged


def log_metadata(execution: GitAdapterExecution) -> GitLogMetadata:
    with _guard_status_or_log_read(execution) as identity:
        head = execution.head_id(identity, allow_absent=True)
        if head is None:
            return GitLogMetadata(commits=())
        result = execution.required_git(
            ("rev-list", f"--max-count={_MAX_LOG_ENTRIES}", "HEAD"),
            identity=identity,
            read_only=True,
        )
        object_ids = tuple(line for line in result.stdout.splitlines() if line)
        if len(object_ids) > _MAX_LOG_ENTRIES or any(
            OBJECT_ID_PATTERN.fullmatch(object_id) is None for object_id in object_ids
        ):
            raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        return GitLogMetadata(
            commits=tuple(
                execution.commit_metadata(object_id.decode("ascii"), identity)
                for object_id in object_ids
            )
        )


@contextmanager
def _guard_status_or_log_read(execution: GitAdapterExecution) -> Iterator[tuple[int, int]]:
    """Guard status/log reads in only standalone or stable linked worktrees."""

    topology, identity = inspect_topology(execution)
    if topology.kind is GitTopologyKind.STANDALONE:
        yield identity
        return
    if topology.kind is not GitTopologyKind.LINKED_WORKTREE:
        raise AdapterFailure(GitFailureCode.UNSUPPORTED_TOPOLOGY)

    opening = inspect_external_operation(execution.root)
    try:
        yield identity
    finally:
        drifted = False
        try:
            closing = inspect_external_operation(execution.root)
            drifted = bind_root(execution.root) != identity or closing != opening
        except BaseException:
            drifted = True
        if drifted:
            raise external_operation_failure()


def _require_standalone(
    execution: GitAdapterExecution,
) -> tuple[GitRepositoryTopology, tuple[int, int]]:
    topology, identity = inspect_topology(execution)
    if topology.kind is not GitTopologyKind.STANDALONE:
        raise AdapterFailure(GitFailureCode.UNSUPPORTED_TOPOLOGY)
    return topology, identity


def _object_length(
    index_entries: dict[str, tuple[str, str]],
    head_entries: dict[str, tuple[str, str]],
) -> int:
    lengths = {
        len(object_id)
        for entries in (index_entries, head_entries)
        for _, object_id in entries.values()
    }
    if not lengths:
        return 40
    if len(lengths) != 1 or next(iter(lengths)) not in {40, 64}:
        raise AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return next(iter(lengths))


def _entry_change(
    base: tuple[str, str] | None,
    projected: tuple[str, str] | None,
) -> GitChangeKind | None:
    if base is None and projected is None:
        return None
    if base is None:
        return GitChangeKind.ADDED
    if projected is None:
        return GitChangeKind.DELETED
    if base[0] != projected[0]:
        return GitChangeKind.TYPE_CHANGED
    if base[1] != projected[1]:
        return GitChangeKind.MODIFIED
    return None
