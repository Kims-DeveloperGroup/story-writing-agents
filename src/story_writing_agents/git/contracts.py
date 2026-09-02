"""Pure contracts for the closed, provider-independent Git boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum


GIT_BASELINE_BRANCH = "main"
GIT_BASELINE_COMMIT_MESSAGE = "Initialize story workspace"

_DIGEST = re.compile(r"[0-9a-f]{64}")
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
_MAX_MANIFEST_ENTRIES = 65_536
_MAX_PATH_UTF8_BYTES = 1_024
_MAX_IDENTITY_UTF8_BYTES = 1_024
_MAX_BRANCH_UTF8_BYTES = 256
_MAX_STATUS_ENTRIES = 65_536
_MAX_DIFF_ENTRIES = 131_072
_MAX_LOG_ENTRIES = 128
_MAX_COMMIT_PARENTS = 1_024
_MAX_METADATA_UTF8_BYTES = 8 * 1024 * 1024


class GitOperationKind(StrEnum):
    """Closed operations exposed by the safe Git adapter."""

    INSPECT_TOPOLOGY = "inspect_topology"
    INSPECT_BASELINE = "inspect_baseline"
    CREATE_BASELINE = "create_baseline"
    STATUS = "status"
    DIFF_METADATA = "diff_metadata"
    LOG_METADATA = "log_metadata"


class GitRepositoryState(StrEnum):
    """Exact recoverable baseline states visible to later workflows."""

    ABSENT = "absent"
    INITIALIZED_WITHOUT_BASELINE = "initialized_without_baseline"
    BASELINE_COMMITTED = "baseline_committed"
    RECOVERY_REQUIRED = "recovery_required"


class GitTopologyKind(StrEnum):
    """Closed repository topology classifications."""

    ABSENT = "absent"
    STANDALONE = "standalone"
    PARENT_REPOSITORY = "parent_repository"
    LINKED_WORKTREE = "linked_worktree"
    SUBMODULE = "submodule"
    SPARSE_CHECKOUT = "sparse_checkout"
    ROOT_MISMATCH = "root_mismatch"
    SYMLINKED_ADMINISTRATIVE_PATH = "symlinked_administrative_path"
    MALFORMED_ADMINISTRATIVE_PATH = "malformed_administrative_path"


class GitChangeKind(StrEnum):
    """Content-free status and diff classifications."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
    UNMERGED = "unmerged"
    UNTRACKED = "untracked"


class GitFailureCode(StrEnum):
    """Stable failures that never incorporate Git or workspace output."""

    INVALID_REQUEST = "invalid_request"
    INVALID_MANIFEST = "invalid_manifest"
    UNSAFE_ROOT = "unsafe_root"
    UNSUPPORTED_TOPOLOGY = "unsupported_topology"
    EXECUTABLE_UNAVAILABLE = "executable_unavailable"
    IDENTITY_INVALID = "identity_invalid"
    PROCESS_START_FAILED = "process_start_failed"
    PROCESS_TIMED_OUT = "process_timed_out"
    PROCESS_OUTPUT_EXCEEDED = "process_output_exceeded"
    COMMAND_FAILED = "command_failed"
    OUTPUT_INVALID = "output_invalid"
    MANIFEST_DRIFT = "manifest_drift"
    INDEX_MISMATCH = "index_mismatch"
    BASELINE_MISMATCH = "baseline_mismatch"
    CANCELLED = "cancelled"
    RECOVERY_REQUIRED = "recovery_required"
    IO_FAILED = "io_failed"


class GitRecoveryAction(StrEnum):
    """One safe next action attached to every operational failure."""

    NONE = "none"
    RETRY = "retry"
    INSPECT_REPOSITORY = "inspect_repository"
    RECREATE_WORKSPACE = "recreate_workspace"


_FAILURE_DIAGNOSTICS = {
    GitFailureCode.INVALID_REQUEST: "Git request is invalid.",
    GitFailureCode.INVALID_MANIFEST: "Git baseline manifest is invalid.",
    GitFailureCode.UNSAFE_ROOT: "Git workspace root is unsafe.",
    GitFailureCode.UNSUPPORTED_TOPOLOGY: "Git repository topology is unsupported.",
    GitFailureCode.EXECUTABLE_UNAVAILABLE: "Git executable is unavailable.",
    GitFailureCode.IDENTITY_INVALID: "Effective Git identity is unavailable or invalid.",
    GitFailureCode.PROCESS_START_FAILED: "Git process could not start.",
    GitFailureCode.PROCESS_TIMED_OUT: "Git operation timed out.",
    GitFailureCode.PROCESS_OUTPUT_EXCEEDED: "Git operation exceeded its output bound.",
    GitFailureCode.COMMAND_FAILED: "Git command failed.",
    GitFailureCode.OUTPUT_INVALID: "Git returned malformed bounded metadata.",
    GitFailureCode.MANIFEST_DRIFT: "Workspace files do not match the baseline manifest.",
    GitFailureCode.INDEX_MISMATCH: "Git index does not match the baseline manifest.",
    GitFailureCode.BASELINE_MISMATCH: "Git baseline does not match the requested manifest.",
    GitFailureCode.CANCELLED: "Git operation was cancelled.",
    GitFailureCode.RECOVERY_REQUIRED: "Git repository requires recovery.",
    GitFailureCode.IO_FAILED: "Git filesystem operation failed.",
}

_DEFAULT_RECOVERY_ACTION = {
    GitFailureCode.INVALID_REQUEST: GitRecoveryAction.NONE,
    GitFailureCode.INVALID_MANIFEST: GitRecoveryAction.NONE,
    GitFailureCode.UNSAFE_ROOT: GitRecoveryAction.RECREATE_WORKSPACE,
    GitFailureCode.UNSUPPORTED_TOPOLOGY: GitRecoveryAction.RECREATE_WORKSPACE,
    GitFailureCode.EXECUTABLE_UNAVAILABLE: GitRecoveryAction.RETRY,
    GitFailureCode.IDENTITY_INVALID: GitRecoveryAction.RETRY,
    GitFailureCode.PROCESS_START_FAILED: GitRecoveryAction.RETRY,
    GitFailureCode.PROCESS_TIMED_OUT: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.PROCESS_OUTPUT_EXCEEDED: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.COMMAND_FAILED: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.OUTPUT_INVALID: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.MANIFEST_DRIFT: GitRecoveryAction.NONE,
    GitFailureCode.INDEX_MISMATCH: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.BASELINE_MISMATCH: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.CANCELLED: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.RECOVERY_REQUIRED: GitRecoveryAction.INSPECT_REPOSITORY,
    GitFailureCode.IO_FAILED: GitRecoveryAction.INSPECT_REPOSITORY,
}


class GitOperationError(RuntimeError):
    """One bounded content-free failure from the Git boundary."""

    def __init__(
        self,
        code: GitFailureCode,
        *,
        operation: GitOperationKind | None = None,
        exit_code: int | None = None,
        repository_state: GitRepositoryState | None = None,
        recovery_action: GitRecoveryAction | None = None,
    ) -> None:
        if type(code) is not GitFailureCode:
            raise TypeError("code must be a GitFailureCode")
        if operation is not None and type(operation) is not GitOperationKind:
            raise TypeError("operation must be a GitOperationKind or None")
        if exit_code is not None and (
            type(exit_code) is not int or not -(2**31) <= exit_code < 2**32
        ):
            raise TypeError("exit_code must be a bounded integer or None")
        if (
            repository_state is not None
            and type(repository_state) is not GitRepositoryState
        ):
            raise TypeError("repository_state must be a GitRepositoryState or None")
        if recovery_action is None:
            recovery_action = _DEFAULT_RECOVERY_ACTION[code]
        elif type(recovery_action) is not GitRecoveryAction:
            raise TypeError("recovery_action must be a GitRecoveryAction or None")
        self.code = code
        self.operation = operation
        self.exit_code = exit_code
        self.repository_state = repository_state
        self.recovery_action = recovery_action
        super().__init__(_FAILURE_DIAGNOSTICS[code])

    @property
    def diagnostic(self) -> str:
        """Return the fixed safe diagnostic for this failure category."""

        return _FAILURE_DIAGNOSTICS[self.code]


@dataclass(frozen=True, slots=True)
class GitManifestEntry:
    """One exact caller-owned workspace file identity."""

    path: str
    content_digest: str

    def __post_init__(self) -> None:
        _require_manifest_path(self.path)
        _require_digest(self.content_digest)


@dataclass(frozen=True, slots=True)
class GitBaselineManifest:
    """Canonical complete baseline selection supplied by the workspace owner."""

    entries: tuple[GitManifestEntry, ...]
    manifest_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or not self.entries:
            raise _contract_error(GitFailureCode.INVALID_MANIFEST)
        if len(self.entries) > _MAX_MANIFEST_ENTRIES or any(
            type(entry) is not GitManifestEntry for entry in self.entries
        ):
            raise _contract_error(GitFailureCode.INVALID_MANIFEST)
        paths = tuple(entry.path for entry in self.entries)
        if paths != tuple(sorted(paths, key=lambda item: item.encode("utf-8"))):
            raise _contract_error(GitFailureCode.INVALID_MANIFEST)
        if len(set(paths)) != len(paths):
            raise _contract_error(GitFailureCode.INVALID_MANIFEST)
        payload = json.dumps(
            [
                {"content_digest": entry.content_digest, "path": entry.path}
                for entry in self.entries
            ],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        object.__setattr__(self, "manifest_digest", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class GitIdentity:
    """One bounded effective Git author or committer identity."""

    name: str = field(repr=False)
    email: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_identity_component(self.name, name=True)
        _require_identity_component(self.email, name=False)


@dataclass(frozen=True, slots=True)
class GitEffectiveIdentity:
    """Exact effective author and committer identities used by Git."""

    author: GitIdentity = field(repr=False)
    committer: GitIdentity = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.author) is not GitIdentity
            or type(self.committer) is not GitIdentity
        ):
            raise _contract_error(GitFailureCode.IDENTITY_INVALID)


@dataclass(frozen=True, slots=True)
class GitRepositoryTopology:
    """One closed content-free repository topology fact."""

    kind: GitTopologyKind

    def __post_init__(self) -> None:
        if type(self.kind) is not GitTopologyKind:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)


@dataclass(frozen=True, slots=True)
class GitBaselineInspection:
    """Exact nonmutating baseline and recovery truth."""

    topology: GitRepositoryTopology
    state: GitRepositoryState
    manifest_digest: str
    commit_id: str | None = None
    tree_id: str | None = None
    effective_identity: GitEffectiveIdentity | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.topology) is not GitRepositoryTopology:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        if type(self.state) is not GitRepositoryState:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        _require_digest(self.manifest_digest)
        _require_optional_object_id(self.commit_id)
        _require_optional_object_id(self.tree_id)
        if (
            self.effective_identity is not None
            and type(self.effective_identity) is not GitEffectiveIdentity
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        if self.state is GitRepositoryState.BASELINE_COMMITTED:
            if (
                self.commit_id is None
                or self.tree_id is None
                or self.effective_identity is None
            ):
                raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        elif any(
            value is not None
            for value in (self.commit_id, self.tree_id, self.effective_identity)
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)


@dataclass(frozen=True, slots=True)
class GitStatusEntry:
    """One bounded literal path and its index/worktree status."""

    path: str
    index_change: GitChangeKind | None
    worktree_change: GitChangeKind | None
    previous_path: str | None = None

    def __post_init__(self) -> None:
        _require_projection_path(self.path)
        if self.previous_path is not None:
            _require_projection_path(self.previous_path)
        for value in (self.index_change, self.worktree_change):
            if value is not None and type(value) is not GitChangeKind:
                raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        if self.index_change is None and self.worktree_change is None:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)


@dataclass(frozen=True, slots=True)
class GitStatus:
    """Bounded current-branch and literal-path status projection."""

    branch: str | None
    head_id: str | None
    entries: tuple[GitStatusEntry, ...]

    def __post_init__(self) -> None:
        if self.branch is not None:
            _require_bounded_text(self.branch, maximum_bytes=_MAX_BRANCH_UTF8_BYTES)
        _require_optional_object_id(self.head_id)
        if type(self.entries) is not tuple or any(
            type(entry) is not GitStatusEntry for entry in self.entries
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        if len(self.entries) > _MAX_STATUS_ENTRIES:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)

    @property
    def clean(self) -> bool:
        """Return whether no index or worktree change is present."""

        return not self.entries


@dataclass(frozen=True, slots=True)
class GitDiffEntry:
    """One content-free raw-diff metadata entry."""

    path: str
    previous_path: str | None
    change: GitChangeKind
    staged: bool
    old_object_id: str
    new_object_id: str

    def __post_init__(self) -> None:
        _require_projection_path(self.path)
        if self.previous_path is not None:
            _require_projection_path(self.previous_path)
        if type(self.change) is not GitChangeKind or type(self.staged) is not bool:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        _require_object_id(self.old_object_id)
        _require_object_id(self.new_object_id)


@dataclass(frozen=True, slots=True)
class GitDiffMetadata:
    """Bounded staged and worktree diff metadata relative to the index/HEAD."""

    head_id: str | None
    entries: tuple[GitDiffEntry, ...]

    def __post_init__(self) -> None:
        _require_optional_object_id(self.head_id)
        if type(self.entries) is not tuple or any(
            type(entry) is not GitDiffEntry for entry in self.entries
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        if len(self.entries) > _MAX_DIFF_ENTRIES:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)


@dataclass(frozen=True, slots=True)
class GitCommitMetadata:
    """Bounded commit identity and content-free message facts."""

    commit_id: str
    tree_id: str
    parent_ids: tuple[str, ...]
    author: GitIdentity = field(repr=False)
    committer: GitIdentity = field(repr=False)
    authored_at: str
    committed_at: str
    message_digest: str
    message_utf8_bytes: int

    def __post_init__(self) -> None:
        _require_object_id(self.commit_id)
        _require_object_id(self.tree_id)
        if (
            type(self.parent_ids) is not tuple
            or len(self.parent_ids) > _MAX_COMMIT_PARENTS
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        for parent_id in self.parent_ids:
            _require_object_id(parent_id)
        if (
            type(self.author) is not GitIdentity
            or type(self.committer) is not GitIdentity
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        _require_bounded_text(self.authored_at, maximum_bytes=64)
        _require_bounded_text(self.committed_at, maximum_bytes=64)
        _require_digest(self.message_digest)
        if (
            type(self.message_utf8_bytes) is not int
            or self.message_utf8_bytes < 0
            or self.message_utf8_bytes > _MAX_METADATA_UTF8_BYTES
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)


@dataclass(frozen=True, slots=True)
class GitLogMetadata:
    """Bounded newest-first commit metadata."""

    commits: tuple[GitCommitMetadata, ...]

    def __post_init__(self) -> None:
        if type(self.commits) is not tuple or any(
            type(commit) is not GitCommitMetadata for commit in self.commits
        ):
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)
        if len(self.commits) > _MAX_LOG_ENTRIES:
            raise _contract_error(GitFailureCode.OUTPUT_INVALID)


def _contract_error(code: GitFailureCode) -> GitOperationError:
    error = GitOperationError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


def _require_digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _contract_error(GitFailureCode.INVALID_MANIFEST)
    return value


def _require_object_id(value: object) -> str:
    if type(value) is not str or _OBJECT_ID.fullmatch(value) is None:
        raise _contract_error(GitFailureCode.OUTPUT_INVALID)
    return value


def _require_optional_object_id(value: object | None) -> str | None:
    if value is not None:
        _require_object_id(value)
    return value


def _require_manifest_path(value: object) -> str:
    if type(value) is not str:
        raise _contract_error(GitFailureCode.INVALID_MANIFEST)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = b""
    if (
        not encoded
        or len(encoded) > _MAX_PATH_UTF8_BYTES
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
    ):
        raise _contract_error(GitFailureCode.INVALID_MANIFEST)
    parts = value.split("/")
    if any(
        not part
        or part in {".", ".."}
        or _PATH_SEGMENT.fullmatch(part) is None
        or part.casefold() == ".git"
        for part in parts
    ):
        raise _contract_error(GitFailureCode.INVALID_MANIFEST)
    return value


def _require_projection_path(value: object) -> str:
    if type(value) is not str:
        raise _contract_error(GitFailureCode.OUTPUT_INVALID)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = b""
    if (
        not encoded
        or len(encoded) > _MAX_PATH_UTF8_BYTES
        or value.startswith("/")
        or value.endswith("/")
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise _contract_error(GitFailureCode.OUTPUT_INVALID)
    return value


def _require_identity_component(value: object, *, name: bool) -> str:
    if type(value) is not str:
        raise _contract_error(GitFailureCode.IDENTITY_INVALID)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = b""
    forbidden = "<>\x00\r\n" if name else "<>\x00\r\n "
    if (
        not encoded
        or len(encoded) > _MAX_IDENTITY_UTF8_BYTES
        or any(character in value for character in forbidden)
    ):
        raise _contract_error(GitFailureCode.IDENTITY_INVALID)
    return value


def _require_bounded_text(value: object, *, maximum_bytes: int) -> str:
    if type(value) is not str:
        raise _contract_error(GitFailureCode.OUTPUT_INVALID)
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        encoded = b""
    if (
        not encoded
        or len(encoded) > maximum_bytes
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise _contract_error(GitFailureCode.OUTPUT_INVALID)
    return value


__all__ = [
    "GIT_BASELINE_BRANCH",
    "GIT_BASELINE_COMMIT_MESSAGE",
    "GitOperationKind",
    "GitRepositoryState",
    "GitTopologyKind",
    "GitChangeKind",
    "GitFailureCode",
    "GitRecoveryAction",
    "GitOperationError",
    "GitManifestEntry",
    "GitBaselineManifest",
    "GitIdentity",
    "GitEffectiveIdentity",
    "GitRepositoryTopology",
    "GitBaselineInspection",
    "GitStatusEntry",
    "GitStatus",
    "GitDiffEntry",
    "GitDiffMetadata",
    "GitCommitMetadata",
    "GitLogMetadata",
]
