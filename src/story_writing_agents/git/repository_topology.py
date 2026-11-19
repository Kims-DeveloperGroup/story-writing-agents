"""Inspect exact Git topology and its administrative path."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .adapter_contracts import AdapterFailure, bind_root
from .adapter_execution import GitAdapterExecution, decode_single_line
from .contracts import GitFailureCode, GitRepositoryTopology, GitTopologyKind


_MAX_ADMINISTRATIVE_ENTRIES = 65_536


def inspect_topology(
    execution: GitAdapterExecution,
) -> tuple[GitRepositoryTopology, tuple[int, int]]:
    identity = bind_root(execution.root)
    administrative = execution.root / ".git"
    try:
        admin_stat = os.lstat(administrative)
    except FileNotFoundError:
        result = execution.run_git(
            ("rev-parse", "--show-toplevel"),
            identity=identity,
            read_only=True,
            allow_failure=True,
        )
        if result.returncode != 0:
            git_directory = execution.run_git(
                ("rev-parse", "--git-dir"),
                identity=identity,
                read_only=True,
                allow_failure=True,
            )
            if git_directory.returncode == 0:
                return (
                    GitRepositoryTopology(GitTopologyKind.ROOT_MISMATCH),
                    identity,
                )
            return GitRepositoryTopology(GitTopologyKind.ABSENT), identity
        observed = decode_single_line(result.stdout)
        if _same_real_path(observed, execution.root):
            return (
                GitRepositoryTopology(
                    GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH
                ),
                identity,
            )
        return GitRepositoryTopology(GitTopologyKind.PARENT_REPOSITORY), identity
    except OSError:
        raise AdapterFailure(GitFailureCode.UNSAFE_ROOT)

    if stat.S_ISLNK(admin_stat.st_mode):
        return (
            GitRepositoryTopology(GitTopologyKind.SYMLINKED_ADMINISTRATIVE_PATH),
            identity,
        )
    if stat.S_ISREG(admin_stat.st_mode):
        superproject = execution.run_git(
            ("rev-parse", "--show-superproject-working-tree"),
            identity=identity,
            read_only=True,
            allow_failure=True,
        )
        kind = (
            GitTopologyKind.SUBMODULE
            if superproject.returncode == 0 and superproject.stdout.strip()
            else GitTopologyKind.LINKED_WORKTREE
        )
        return GitRepositoryTopology(kind), identity
    if not stat.S_ISDIR(admin_stat.st_mode):
        return (
            GitRepositoryTopology(GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH),
            identity,
        )
    if _administrative_tree_has_symlink(administrative):
        return (
            GitRepositoryTopology(GitTopologyKind.SYMLINKED_ADMINISTRATIVE_PATH),
            identity,
        )
    if (administrative / "objects" / "info" / "alternates").exists():
        return (
            GitRepositoryTopology(GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH),
            identity,
        )

    top = execution.run_git(
        ("rev-parse", "--show-toplevel"),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    if top.returncode != 0:
        return (
            GitRepositoryTopology(GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH),
            identity,
        )
    if not _same_real_path(decode_single_line(top.stdout), execution.root):
        return GitRepositoryTopology(GitTopologyKind.ROOT_MISMATCH), identity

    bare = execution.run_git(
        ("rev-parse", "--is-bare-repository"),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    inside = execution.run_git(
        ("rev-parse", "--is-inside-work-tree"),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    if (
        bare.returncode != 0
        or inside.returncode != 0
        or bare.stdout.strip() != b"false"
        or inside.stdout.strip() != b"true"
    ):
        return GitRepositoryTopology(GitTopologyKind.ROOT_MISMATCH), identity

    superproject = execution.run_git(
        ("rev-parse", "--show-superproject-working-tree"),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    if superproject.returncode == 0 and superproject.stdout.strip():
        return GitRepositoryTopology(GitTopologyKind.SUBMODULE), identity
    sparse = execution.run_git(
        ("config", "--bool", "core.sparseCheckout"),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    if sparse.returncode == 0 and sparse.stdout.strip() == b"true":
        return GitRepositoryTopology(GitTopologyKind.SPARSE_CHECKOUT), identity
    index = execution.run_git(
        ("ls-files", "--stage", "-z", "--"),
        identity=identity,
        read_only=True,
        allow_failure=True,
    )
    if index.returncode == 0 and any(
        row.startswith(b"160000 ") for row in index.stdout.split(b"\x00") if row
    ):
        return GitRepositoryTopology(GitTopologyKind.SUBMODULE), identity
    return GitRepositoryTopology(GitTopologyKind.STANDALONE), identity


def _administrative_tree_has_symlink(administrative: Path) -> bool:
    seen = 0
    try:
        for directory, directories, files in os.walk(
            administrative,
            topdown=True,
            followlinks=False,
        ):
            for name in (*directories, *files):
                seen += 1
                if seen > _MAX_ADMINISTRATIVE_ENTRIES:
                    raise AdapterFailure(GitFailureCode.UNSUPPORTED_TOPOLOGY)
                if os.path.islink(Path(directory) / name):
                    return True
    except AdapterFailure:
        raise
    except OSError:
        raise AdapterFailure(GitFailureCode.UNSAFE_ROOT)
    return False


def _same_real_path(observed: str, expected: Path) -> bool:
    try:
        candidate = Path(os.path.realpath(observed))
    except (OSError, ValueError):
        return False
    return candidate == expected
