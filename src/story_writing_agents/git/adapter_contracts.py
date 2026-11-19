"""Exact root identity and content-free failures shared by Git adapter owners."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .contracts import GitFailureCode, GitOperationError, GitRepositoryState


class AdapterFailure(Exception):
    __slots__ = ("code", "exit_code", "repository_state")

    def __init__(
        self,
        code: GitFailureCode,
        *,
        exit_code: int | None = None,
        repository_state: GitRepositoryState | None = None,
    ) -> None:
        self.code = code
        self.exit_code = exit_code
        self.repository_state = repository_state
        super().__init__(code.value)


def bind_root(root: Path) -> tuple[int, int]:
    try:
        value = os.lstat(root)
        canonical = Path(os.path.realpath(root))
    except OSError:
        raise AdapterFailure(GitFailureCode.UNSAFE_ROOT)
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or canonical != root
        or value.st_dev <= 0
        or value.st_ino <= 0
    ):
        raise AdapterFailure(GitFailureCode.UNSAFE_ROOT)
    return value.st_dev, value.st_ino


def verify_root(root: Path, identity: tuple[int, int]) -> None:
    if bind_root(root) != identity:
        raise AdapterFailure(GitFailureCode.UNSAFE_ROOT)


def cleared_error(code: GitFailureCode) -> GitOperationError:
    error = GitOperationError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error
