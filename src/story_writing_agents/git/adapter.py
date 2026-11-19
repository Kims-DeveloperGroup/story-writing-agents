"""Provider-free Git adapter facade for one exact workspace root."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .adapter_contracts import AdapterFailure, cleared_error
from .adapter_execution import GitAdapterExecution
from .contracts import (
    GitBaselineInspection,
    GitBaselineManifest,
    GitDiffMetadata,
    GitExternalOperationInspection,
    GitFailureCode,
    GitLogMetadata,
    GitOperationError,
    GitOperationKind,
    GitRepositoryState,
    GitRepositoryTopology,
    GitStatus,
)
from .external_operation_inspection import inspect_external_operation
from .repository_baseline import create_baseline, inspect_baseline, require_manifest
from .repository_metadata import diff_metadata, log_metadata, status
from .repository_topology import inspect_topology


class GitRepositoryAdapter:
    """Own safe fixed Git operations for one exact workspace root.

    Construction performs no filesystem access and starts no process. Every
    method validates and binds the root afresh, owns its child processes to
    completion, and returns only bounded typed projections.
    """

    __slots__ = ("_execution",)

    def __init__(self, root: str | os.PathLike[str]) -> None:
        path_failure = False
        try:
            raw = os.fspath(root)
        except TypeError:
            raw = ""
            path_failure = True
        if path_failure:
            raise cleared_error(GitFailureCode.INVALID_REQUEST)
        if type(raw) not in {str, bytes}:
            raise cleared_error(GitFailureCode.INVALID_REQUEST)
        if isinstance(raw, bytes):
            decode_failure = False
            try:
                raw = os.fsdecode(raw)
            except UnicodeError:
                raw = ""
                decode_failure = True
            if decode_failure:
                raise cleared_error(GitFailureCode.INVALID_REQUEST)
        try:
            encoded_root = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            encoded_root = b""
        if not encoded_root or "\x00" in raw or "\r" in raw or "\n" in raw:
            raise cleared_error(GitFailureCode.INVALID_REQUEST)
        root_path = Path(raw)
        if not root_path.is_absolute() or root_path != Path(os.path.normpath(raw)):
            raise cleared_error(GitFailureCode.INVALID_REQUEST)
        self._execution = GitAdapterExecution(root_path)

    def inspect_topology(self) -> GitRepositoryTopology:
        """Return one nonmutating closed topology classification."""

        return self._public_call(
            GitOperationKind.INSPECT_TOPOLOGY,
            lambda: inspect_topology(self._execution)[0],
        )

    def inspect_baseline(
        self,
        manifest: GitBaselineManifest,
    ) -> GitBaselineInspection:
        """Return exact baseline/recovery truth for one caller manifest."""

        return self._public_call(
            GitOperationKind.INSPECT_BASELINE,
            lambda: inspect_baseline(self._execution, require_manifest(manifest)),
            recovery_manifest=manifest,
        )

    def create_baseline(
        self,
        manifest: GitBaselineManifest,
    ) -> GitBaselineInspection:
        """Create or detect the one exact fixed baseline commit."""

        return self._public_call(
            GitOperationKind.CREATE_BASELINE,
            lambda: create_baseline(self._execution, require_manifest(manifest)),
            recovery_manifest=manifest,
        )

    def status(self) -> GitStatus:
        """Return bounded literal-path index and worktree status."""

        return self._public_call(GitOperationKind.STATUS, lambda: status(self._execution))

    def diff_metadata(self) -> GitDiffMetadata:
        """Return bounded raw staged and worktree diff metadata."""

        return self._public_call(GitOperationKind.DIFF_METADATA, lambda: diff_metadata(self._execution))

    def log_metadata(self) -> GitLogMetadata:
        """Return bounded newest-first commit metadata from current HEAD."""

        return self._public_call(GitOperationKind.LOG_METADATA, lambda: log_metadata(self._execution))

    def inspect_external_operation(self) -> GitExternalOperationInspection:
        """Inspect content-free external Git operation state without a process."""

        return self._public_call(
            GitOperationKind.INSPECT_EXTERNAL_OPERATION,
            lambda: inspect_external_operation(self._execution.root),
        )

    def _public_call(
        self,
        operation: GitOperationKind,
        action: Callable[[], object],
        *,
        recovery_manifest: object | None = None,
    ) -> object:
        failure: GitOperationError | None = None
        try:
            return action()
        except GitOperationError as caught:
            failure = GitOperationError(
                caught.code,
                operation=operation,
                exit_code=caught.exit_code,
                repository_state=caught.repository_state,
                recovery_action=caught.recovery_action,
            )
        except AdapterFailure as caught:
            failure = GitOperationError(
                caught.code,
                operation=operation,
                exit_code=caught.exit_code,
                repository_state=caught.repository_state,
            )
        except (KeyboardInterrupt, SystemExit):
            state = self._recover_state(recovery_manifest)
            failure = GitOperationError(
                GitFailureCode.CANCELLED,
                operation=operation,
                repository_state=state,
            )
        except (OSError, UnicodeError):
            failure = GitOperationError(
                GitFailureCode.IO_FAILED,
                operation=operation,
                repository_state=self._recover_state(recovery_manifest),
            )
        except Exception:
            failure = GitOperationError(
                GitFailureCode.OUTPUT_INVALID,
                operation=operation,
                repository_state=self._recover_state(recovery_manifest),
            )
        assert failure is not None
        failure.__cause__ = None
        failure.__context__ = None
        failure.__suppress_context__ = True
        raise failure

    def _recover_state(self, manifest: object | None) -> GitRepositoryState | None:
        if type(manifest) is not GitBaselineManifest:
            return None
        try:
            return inspect_baseline(self._execution, require_manifest(manifest)).state
        except BaseException:
            return GitRepositoryState.RECOVERY_REQUIRED


__all__ = ["GitRepositoryAdapter"]
