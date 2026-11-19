"""Shared identity and history checks for local story commits."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .session_contracts import GitSessionResourceInspection
from .session_generation import GitSessionGenerationIdentity
from .session_history import GitSessionHistory
from .session_history_contracts import GitSessionHistoryError, GitSessionHistoryFailureCode
from .story_commit_contracts import StoryCommitError, StoryCommitFailureCode

def require_history_ready(
    history: GitSessionHistory,
    expected_generation_identity: GitSessionGenerationIdentity | None,
    *,
    expected_branch_tip: str | None = None,
    expected_internal_tip: str | None = None,
) -> GitSessionResourceInspection:
    if expected_generation_identity is None:
        return history.require_semantic_ready(
            expected_branch_tip=expected_branch_tip,
            expected_internal_tip=expected_internal_tip,
        )
    return history.require_generation_ready(
        expected_generation_identity,
        expected_branch_tip=expected_branch_tip,
        expected_internal_tip=expected_internal_tip,
    )


def history_failure_code(
    failure: GitSessionHistoryError,
    expected_generation_identity: GitSessionGenerationIdentity | None,
) -> StoryCommitFailureCode:
    if failure.code is GitSessionHistoryFailureCode.RECOVERY_REQUIRED or (
        expected_generation_identity is not None
        and failure.code is GitSessionHistoryFailureCode.UNSAFE_ROOT
    ):
        return StoryCommitFailureCode.RECOVERY_REQUIRED
    return StoryCommitFailureCode.HISTORY_FAILED


def require_bound_session_root(
    main_root: Path,
    session_id: str,
    expected: tuple[int, int] | None,
) -> None:
    if expected is None:
        return
    root = session_root(main_root, session_id)
    if directory_identity(root) != expected:
        raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)


def session_root_identity(
    root: Path, expected: tuple[int, int] | None
) -> tuple[int, int]:
    identity = directory_identity(root)
    if expected is not None and identity != expected:
        raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    return identity


def directory_identity(root: Path) -> tuple[int, int]:
    try:
        value = os.lstat(root)
        canonical = Path(os.path.realpath(root))
    except OSError:
        raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT) from None
    if (
        not root.is_absolute()
        or canonical != root
        or not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or value.st_dev <= 0
        or value.st_ino <= 0
    ):
        raise story_commit_error(StoryCommitFailureCode.CANDIDATE_DRIFT)
    return value.st_dev, value.st_ino


def session_root(main_root: Path, session_id: str) -> Path:
    return main_root.with_name(f"{main_root.name}.story-worktrees") / session_id


def candidate_operation_id(action: str, candidate_id: str) -> str:
    return hashlib.sha256(f"{action}\0{candidate_id}".encode("ascii")).hexdigest()[:32]


def story_commit_error(code: StoryCommitFailureCode) -> StoryCommitError:
    error = StoryCommitError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error
