"""Bounded subprocess adapter for the closed Git operation set."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from .contracts import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitBaselineInspection,
    GitBaselineManifest,
    GitChangeKind,
    GitCommitMetadata,
    GitDiffEntry,
    GitDiffMetadata,
    GitEffectiveIdentity,
    GitExternalOperationInspection,
    GitExternalOperationKind,
    GitFailureCode,
    GitIdentity,
    GitLogMetadata,
    GitManifestEntry,
    GitOperationError,
    GitOperationKind,
    GitRepositoryState,
    GitRepositoryTopology,
    GitStatus,
    GitStatusEntry,
    GitTopologyKind,
)
from .process import GitProcessFailure, GitProcessResult, GitProcessRunner


_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_MANIFEST_ENTRIES = 65_536
_MAX_STATUS_ENTRIES = 65_536
_MAX_LOG_ENTRIES = 128
_MAX_ADMINISTRATIVE_ENTRIES = 65_536
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
_IDENT = re.compile(
    rb"(?P<name>[^<>\r\n]+) <(?P<email>[^<>\r\n]+)> (?P<time>[0-9]+) (?P<zone>[+-][0-9]{4})"
)
_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ZERO_OBJECT_ID = re.compile(rb"(?:0{40}|0{64})")


class _AdapterFailure(Exception):
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


class GitRepositoryAdapter:
    """Own safe fixed Git operations for one exact workspace root.

    Construction performs no filesystem access and starts no process. Every
    method validates and binds the root afresh, owns its child processes to
    completion, and returns only bounded typed projections.
    """

    __slots__ = ("_root",)

    def __init__(self, root: str | os.PathLike[str]) -> None:
        path_failure = False
        try:
            raw = os.fspath(root)
        except TypeError:
            raw = ""
            path_failure = True
        if path_failure:
            raise _cleared_error(GitFailureCode.INVALID_REQUEST)
        if type(raw) not in {str, bytes}:
            raise _cleared_error(GitFailureCode.INVALID_REQUEST)
        if isinstance(raw, bytes):
            decode_failure = False
            try:
                raw = os.fsdecode(raw)
            except UnicodeError:
                raw = ""
                decode_failure = True
            if decode_failure:
                raise _cleared_error(GitFailureCode.INVALID_REQUEST)
        try:
            encoded_root = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            encoded_root = b""
        if not encoded_root or "\x00" in raw or "\r" in raw or "\n" in raw:
            raise _cleared_error(GitFailureCode.INVALID_REQUEST)
        root_path = Path(raw)
        if not root_path.is_absolute() or root_path != Path(os.path.normpath(raw)):
            raise _cleared_error(GitFailureCode.INVALID_REQUEST)
        self._root = root_path

    def inspect_topology(self) -> GitRepositoryTopology:
        """Return one nonmutating closed topology classification."""

        return self._public_call(
            GitOperationKind.INSPECT_TOPOLOGY,
            lambda: self._inspect_topology()[0],
        )

    def inspect_baseline(
        self,
        manifest: GitBaselineManifest,
    ) -> GitBaselineInspection:
        """Return exact baseline/recovery truth for one caller manifest."""

        return self._public_call(
            GitOperationKind.INSPECT_BASELINE,
            lambda: self._inspect_baseline(_require_manifest(manifest)),
            recovery_manifest=manifest,
        )

    def create_baseline(
        self,
        manifest: GitBaselineManifest,
    ) -> GitBaselineInspection:
        """Create or detect the one exact fixed baseline commit."""

        return self._public_call(
            GitOperationKind.CREATE_BASELINE,
            lambda: self._create_baseline(_require_manifest(manifest)),
            recovery_manifest=manifest,
        )

    def status(self) -> GitStatus:
        """Return bounded literal-path index and worktree status."""

        return self._public_call(GitOperationKind.STATUS, self._status)

    def diff_metadata(self) -> GitDiffMetadata:
        """Return bounded raw staged and worktree diff metadata."""

        return self._public_call(GitOperationKind.DIFF_METADATA, self._diff_metadata)

    def log_metadata(self) -> GitLogMetadata:
        """Return bounded newest-first commit metadata from current HEAD."""

        return self._public_call(GitOperationKind.LOG_METADATA, self._log_metadata)

    def inspect_external_operation(self) -> GitExternalOperationInspection:
        """Inspect content-free external Git operation state without a process."""

        return self._public_call(
            GitOperationKind.INSPECT_EXTERNAL_OPERATION,
            self._inspect_external_operation,
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
        except _AdapterFailure as caught:
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
            return self._inspect_baseline(_require_manifest(manifest)).state
        except BaseException:
            return GitRepositoryState.RECOVERY_REQUIRED

    def _inspect_external_operation(self) -> GitExternalOperationInspection:
        try:
            root_identity = self._bind_root()
            with ExitStack() as descriptors:
                root_descriptor = _open_bound_directory(self._root, root_identity)
                descriptors.callback(os.close, root_descriptor)
                root_snapshot = _descriptor_snapshot(root_descriptor)
                git_value = os.stat(
                    ".git",
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                git_snapshot = _stat_snapshot(git_value)

                administrative = self._root / ".git"
                common_git = administrative
                git_file: tuple[bytes, tuple[int, ...]] | None = None
                common_file: tuple[bytes, tuple[int, ...]] | None = None
                backpointer_file: tuple[bytes, tuple[int, ...]] | None = None
                if stat.S_ISDIR(git_value.st_mode) and not stat.S_ISLNK(
                    git_value.st_mode
                ):
                    administrative_identity = _bind_directory(administrative)
                    if administrative_identity != git_snapshot[:2]:
                        raise _external_operation_failure()
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
                        raise _external_operation_failure()
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
                        raise _external_operation_failure()
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
                        os.fspath(self._root / ".git").encode(
                            "utf-8",
                            errors="strict",
                        )
                        + b"\n"
                    )
                    if backpointer_file[0] != expected_backpointer:
                        raise _external_operation_failure()
                else:
                    raise _external_operation_failure()

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
                    raise _external_operation_failure()
                kind = _classify_external_operation(first)

                if (
                    _descriptor_snapshot(root_descriptor) != root_snapshot
                    or _descriptor_snapshot(administrative_descriptor)
                    != administrative_snapshot
                    or _descriptor_snapshot(common_descriptor) != common_snapshot
                    or self._bind_root() != root_identity
                    or _bind_directory(administrative) != administrative_identity
                    or _bind_directory(common_git) != common_identity
                ):
                    raise _external_operation_failure()
                observed_git = os.stat(
                    ".git",
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                if _stat_snapshot(observed_git) != git_snapshot:
                    raise _external_operation_failure()
                if git_file is not None and _read_administrative_file(
                    root_descriptor,
                    ".git",
                ) != git_file:
                    raise _external_operation_failure()
                if common_file is not None and _read_administrative_file(
                    administrative_descriptor,
                    "commondir",
                ) != common_file:
                    raise _external_operation_failure()
                if backpointer_file is not None and _read_administrative_file(
                    administrative_descriptor,
                    "gitdir",
                ) != backpointer_file:
                    raise _external_operation_failure()
                final = {
                    marker: _external_marker_snapshot(
                        administrative_descriptor,
                        marker,
                        directory=marker in _EXTERNAL_OPERATION_DIRECTORIES,
                    )
                    for marker in _EXTERNAL_OPERATION_MARKERS
                }
                if final != first:
                    raise _external_operation_failure()

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
        except _AdapterFailure:
            raise
        except (OSError, UnicodeError, ValueError):
            raise _external_operation_failure() from None

    def _inspect_topology(
        self,
    ) -> tuple[GitRepositoryTopology, tuple[int, int]]:
        identity = self._bind_root()
        administrative = self._root / ".git"
        try:
            admin_stat = os.lstat(administrative)
        except FileNotFoundError:
            result = self._run_git(
                ("rev-parse", "--show-toplevel"),
                identity=identity,
                read_only=True,
                allow_failure=True,
            )
            if result.returncode != 0:
                git_directory = self._run_git(
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
            observed = _decode_single_line(result.stdout)
            if _same_real_path(observed, self._root):
                return (
                    GitRepositoryTopology(
                        GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH
                    ),
                    identity,
                )
            return GitRepositoryTopology(GitTopologyKind.PARENT_REPOSITORY), identity
        except OSError:
            raise _AdapterFailure(GitFailureCode.UNSAFE_ROOT)

        if stat.S_ISLNK(admin_stat.st_mode):
            return (
                GitRepositoryTopology(GitTopologyKind.SYMLINKED_ADMINISTRATIVE_PATH),
                identity,
            )
        if stat.S_ISREG(admin_stat.st_mode):
            superproject = self._run_git(
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
        if self._administrative_tree_has_symlink(administrative):
            return (
                GitRepositoryTopology(GitTopologyKind.SYMLINKED_ADMINISTRATIVE_PATH),
                identity,
            )
        if (administrative / "objects" / "info" / "alternates").exists():
            return (
                GitRepositoryTopology(GitTopologyKind.MALFORMED_ADMINISTRATIVE_PATH),
                identity,
            )

        top = self._run_git(
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
        if not _same_real_path(_decode_single_line(top.stdout), self._root):
            return GitRepositoryTopology(GitTopologyKind.ROOT_MISMATCH), identity

        bare = self._run_git(
            ("rev-parse", "--is-bare-repository"),
            identity=identity,
            read_only=True,
            allow_failure=True,
        )
        inside = self._run_git(
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

        superproject = self._run_git(
            ("rev-parse", "--show-superproject-working-tree"),
            identity=identity,
            read_only=True,
            allow_failure=True,
        )
        if superproject.returncode == 0 and superproject.stdout.strip():
            return GitRepositoryTopology(GitTopologyKind.SUBMODULE), identity
        sparse = self._run_git(
            ("config", "--bool", "core.sparseCheckout"),
            identity=identity,
            read_only=True,
            allow_failure=True,
        )
        if sparse.returncode == 0 and sparse.stdout.strip() == b"true":
            return GitRepositoryTopology(GitTopologyKind.SPARSE_CHECKOUT), identity
        index = self._run_git(
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

    def _administrative_tree_has_symlink(self, administrative: Path) -> bool:
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
                        raise _AdapterFailure(GitFailureCode.UNSUPPORTED_TOPOLOGY)
                    if os.path.islink(Path(directory) / name):
                        return True
        except _AdapterFailure:
            raise
        except OSError:
            raise _AdapterFailure(GitFailureCode.UNSAFE_ROOT)
        return False

    def _inspect_baseline(self, manifest: GitBaselineManifest) -> GitBaselineInspection:
        topology, identity = self._inspect_topology()
        if topology.kind is GitTopologyKind.ABSENT:
            return GitBaselineInspection(
                topology=topology,
                state=GitRepositoryState.ABSENT,
                manifest_digest=manifest.manifest_digest,
            )
        if topology.kind is not GitTopologyKind.STANDALONE:
            return GitBaselineInspection(
                topology=topology,
                state=GitRepositoryState.RECOVERY_REQUIRED,
                manifest_digest=manifest.manifest_digest,
            )
        try:
            head = self._head_id(identity, allow_absent=True)
            refs = self._references(identity)
            branch = self._symbolic_head(identity)
            if head is None:
                state = (
                    GitRepositoryState.INITIALIZED_WITHOUT_BASELINE
                    if not refs and branch == f"refs/heads/{GIT_BASELINE_BRANCH}"
                    else GitRepositoryState.RECOVERY_REQUIRED
                )
                return GitBaselineInspection(
                    topology=topology,
                    state=state,
                    manifest_digest=manifest.manifest_digest,
                )
            if refs != (f"refs/heads/{GIT_BASELINE_BRANCH}",) or branch != refs[0]:
                raise _AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
            count = self._required_git(
                ("rev-list", "--count", "HEAD"),
                identity=identity,
                read_only=True,
            )
            if count.stdout.strip() != b"1":
                raise _AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
            commit = self._commit_metadata(head, identity)
            message = self._commit_message(head, identity)
            if message != GIT_BASELINE_COMMIT_MESSAGE.encode("utf-8"):
                raise _AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
            if commit.parent_ids:
                raise _AdapterFailure(GitFailureCode.BASELINE_MISMATCH)
            expected = self._manifest_files(manifest, identity)
            index_entries = self._index_entries(identity)
            tree_entries = self._tree_entries(head, identity)
            self._verify_entries(manifest, expected, index_entries, identity)
            self._verify_entries(manifest, expected, tree_entries, identity)
            if index_entries != tree_entries or commit.tree_id != _derive_tree_id(
                index_entries
            ):
                raise _AdapterFailure(GitFailureCode.INDEX_MISMATCH)
            return GitBaselineInspection(
                topology=topology,
                state=GitRepositoryState.BASELINE_COMMITTED,
                manifest_digest=manifest.manifest_digest,
                commit_id=head,
                tree_id=commit.tree_id,
                effective_identity=GitEffectiveIdentity(
                    author=commit.author,
                    committer=commit.committer,
                ),
            )
        except _AdapterFailure:
            return GitBaselineInspection(
                topology=topology,
                state=GitRepositoryState.RECOVERY_REQUIRED,
                manifest_digest=manifest.manifest_digest,
            )

    def _create_baseline(self, manifest: GitBaselineManifest) -> GitBaselineInspection:
        initial = self._inspect_baseline(manifest)
        if initial.state is GitRepositoryState.BASELINE_COMMITTED:
            return initial
        if initial.state is GitRepositoryState.RECOVERY_REQUIRED:
            raise _AdapterFailure(
                GitFailureCode.RECOVERY_REQUIRED,
                repository_state=initial.state,
            )

        topology, identity = self._inspect_topology()
        files = self._manifest_files(manifest, identity)
        effective_identity = self._effective_identity(identity)
        if topology.kind is GitTopologyKind.ABSENT:
            self._required_git(
                (
                    "init",
                    "--quiet",
                    f"--initial-branch={GIT_BASELINE_BRANCH}",
                    "--template=",
                ),
                identity=identity,
            )
            topology, identity = self._inspect_topology()
        if topology.kind is not GitTopologyKind.STANDALONE:
            raise _AdapterFailure(
                GitFailureCode.UNSUPPORTED_TOPOLOGY,
                repository_state=GitRepositoryState.RECOVERY_REQUIRED,
            )

        self._required_git(("read-tree", "--empty"), identity=identity)
        index_rows = bytearray()
        for entry, content in files:
            result = self._required_git(
                ("hash-object", "-w", "--no-filters", "--stdin"),
                identity=identity,
                stdin=content,
            )
            object_id = _decode_object_id(result.stdout)
            index_rows.extend(
                f"100644 blob {object_id}\t{entry.path}".encode("ascii") + b"\x00"
            )
        self._required_git(
            ("update-index", "-z", "--index-info"),
            identity=identity,
            stdin=bytes(index_rows),
        )
        index_entries = self._index_entries(identity)
        self._verify_entries(manifest, files, index_entries, identity)
        tree_id = self._write_tree(identity)
        tree_entries = self._tree_entries(tree_id, identity, treeish_is_tree=True)
        self._verify_entries(manifest, files, tree_entries, identity)
        if tree_entries != index_entries:
            raise _AdapterFailure(GitFailureCode.INDEX_MISMATCH)

        commit_environment = {
            "GIT_AUTHOR_NAME": effective_identity.author.name,
            "GIT_AUTHOR_EMAIL": effective_identity.author.email,
            "GIT_COMMITTER_NAME": effective_identity.committer.name,
            "GIT_COMMITTER_EMAIL": effective_identity.committer.email,
        }
        commit = self._required_git(
            ("commit-tree", tree_id),
            identity=identity,
            stdin=GIT_BASELINE_COMMIT_MESSAGE.encode("utf-8") + b"\n",
            identity_environment=commit_environment,
        )
        commit_id = _decode_object_id(commit.stdout)
        self._required_git(
            (
                "update-ref",
                f"refs/heads/{GIT_BASELINE_BRANCH}",
                commit_id,
                "0" * len(commit_id),
            ),
            identity=identity,
        )
        self._required_git(
            ("symbolic-ref", "HEAD", f"refs/heads/{GIT_BASELINE_BRANCH}"),
            identity=identity,
        )
        completed = self._inspect_baseline(manifest)
        if completed.state is not GitRepositoryState.BASELINE_COMMITTED:
            raise _AdapterFailure(
                GitFailureCode.RECOVERY_REQUIRED,
                repository_state=completed.state,
            )
        return completed

    def _status(self) -> GitStatus:
        topology, identity = self._require_standalone()
        del topology
        branch_ref = self._symbolic_head(identity, allow_detached=True)
        branch = None
        if branch_ref is not None and branch_ref.startswith("refs/heads/"):
            branch = branch_ref.removeprefix("refs/heads/")
        head = self._head_id(identity, allow_absent=True)
        entries, _ = self._repository_changes(identity, head)
        return GitStatus(
            branch=branch,
            head_id=head,
            entries=entries,
        )

    def _diff_metadata(self) -> GitDiffMetadata:
        topology, identity = self._require_standalone()
        del topology
        head = self._head_id(identity, allow_absent=True)
        _, entries = self._repository_changes(identity, head)
        return GitDiffMetadata(head_id=head, entries=entries)

    def _repository_changes(
        self,
        identity: tuple[int, int],
        head: str | None,
    ) -> tuple[tuple[GitStatusEntry, ...], tuple[GitDiffEntry, ...]]:
        index_entries, unmerged = self._index_state(identity)
        head_entries = {} if head is None else self._tree_entries(head, identity)
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
            worktree_change = self._worktree_change(path, index_value, identity)
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
        for path in self._untracked_paths(identity):
            if path in index_entries:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            status_entries.append(
                GitStatusEntry(
                    path=path,
                    index_change=None,
                    worktree_change=GitChangeKind.UNTRACKED,
                )
            )
        if len(status_entries) > _MAX_STATUS_ENTRIES:
            raise _AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
        return tuple(status_entries), tuple(diff_entries)

    def _worktree_change(
        self,
        path: str,
        index_value: tuple[str, str] | None,
        identity: tuple[int, int],
    ) -> GitChangeKind | None:
        if index_value is None:
            return None
        mode, object_id = index_value
        target = self._root.joinpath(*path.split("/"))
        current = self._root
        for part in target.relative_to(self._root).parts[:-1]:
            current /= part
            try:
                parent = os.lstat(current)
            except FileNotFoundError:
                return GitChangeKind.DELETED
            except OSError:
                raise _AdapterFailure(GitFailureCode.IO_FAILED)
            if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
                return GitChangeKind.TYPE_CHANGED
        try:
            value = os.lstat(target)
        except FileNotFoundError:
            return GitChangeKind.DELETED
        except OSError:
            raise _AdapterFailure(GitFailureCode.IO_FAILED)
        if mode == "120000":
            if not stat.S_ISLNK(value.st_mode):
                return GitChangeKind.TYPE_CHANGED
            try:
                content = os.fsencode(os.readlink(target))
            except OSError:
                raise _AdapterFailure(GitFailureCode.IO_FAILED)
        elif mode in {"100644", "100755"}:
            if not stat.S_ISREG(value.st_mode):
                return GitChangeKind.TYPE_CHANGED
            content = self._read_bounded_file(target)
            executable = bool(value.st_mode & 0o111)
            if executable != (mode == "100755"):
                return GitChangeKind.TYPE_CHANGED
        else:
            return GitChangeKind.TYPE_CHANGED
        blob = self._required_git(
            ("cat-file", "blob", object_id),
            identity=identity,
            read_only=True,
        ).stdout
        return None if blob == content else GitChangeKind.MODIFIED

    def _read_bounded_file(self, target: Path) -> bytes:
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags)
        except OSError:
            raise _AdapterFailure(GitFailureCode.IO_FAILED)
        content = bytearray()
        try:
            while True:
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > _MAX_FILE_BYTES:
                    raise _AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
        finally:
            os.close(descriptor)
        return bytes(content)

    def _untracked_paths(self, identity: tuple[int, int]) -> tuple[str, ...]:
        result = self._required_git(
            ("ls-files", "--others", "--exclude-standard", "-z", "--"),
            identity=identity,
            read_only=True,
        )
        paths = tuple(_decode_path(raw) for raw in result.stdout.split(b"\x00") if raw)
        if len(paths) > _MAX_STATUS_ENTRIES:
            raise _AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
        return paths

    def _index_state(
        self,
        identity: tuple[int, int],
    ) -> tuple[dict[str, tuple[str, str]], set[str]]:
        result = self._required_git(
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
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            if _OBJECT_ID.fullmatch(object_id) is None:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            path = _decode_path(raw_path)
            if stage != b"0":
                unmerged.add(path)
                continue
            if path in entries:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
        return entries, unmerged

    def _log_metadata(self) -> GitLogMetadata:
        topology, identity = self._require_standalone()
        del topology
        head = self._head_id(identity, allow_absent=True)
        if head is None:
            return GitLogMetadata(commits=())
        result = self._required_git(
            ("rev-list", f"--max-count={_MAX_LOG_ENTRIES}", "HEAD"),
            identity=identity,
            read_only=True,
        )
        object_ids = tuple(line for line in result.stdout.splitlines() if line)
        if len(object_ids) > _MAX_LOG_ENTRIES or any(
            _OBJECT_ID.fullmatch(object_id) is None for object_id in object_ids
        ):
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        return GitLogMetadata(
            commits=tuple(
                self._commit_metadata(object_id.decode("ascii"), identity)
                for object_id in object_ids
            )
        )

    def _require_standalone(
        self,
    ) -> tuple[GitRepositoryTopology, tuple[int, int]]:
        topology, identity = self._inspect_topology()
        if topology.kind is not GitTopologyKind.STANDALONE:
            raise _AdapterFailure(GitFailureCode.UNSUPPORTED_TOPOLOGY)
        return topology, identity

    def _manifest_files(
        self,
        manifest: GitBaselineManifest,
        identity: tuple[int, int],
    ) -> tuple[tuple[object, bytes], ...]:
        files: list[tuple[object, bytes]] = []
        total = 0
        for entry in manifest.entries:
            self._verify_root(identity)
            target = self._root.joinpath(*entry.path.split("/"))
            self._verify_contained_file(target)
            try:
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(target, flags)
            except OSError:
                raise _AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
            content = bytearray()
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    raise _AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
                while True:
                    chunk = os.read(descriptor, _READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > _MAX_FILE_BYTES:
                        raise _AdapterFailure(GitFailureCode.INVALID_MANIFEST)
            finally:
                os.close(descriptor)
            value = bytes(content)
            if hashlib.sha256(value).hexdigest() != entry.content_digest:
                raise _AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
            total += len(value)
            if total > _MAX_MANIFEST_BYTES:
                raise _AdapterFailure(GitFailureCode.INVALID_MANIFEST)
            files.append((entry, value))
        self._verify_root(identity)
        return tuple(files)

    def _verify_contained_file(self, target: Path) -> None:
        current = self._root
        for part in target.relative_to(self._root).parts:
            current /= part
            try:
                value = os.lstat(current)
            except OSError:
                raise _AdapterFailure(GitFailureCode.MANIFEST_DRIFT)
            if stat.S_ISLNK(value.st_mode):
                raise _AdapterFailure(GitFailureCode.MANIFEST_DRIFT)

    def _effective_identity(self, identity: tuple[int, int]) -> GitEffectiveIdentity:
        configured_name = self._effective_config_value("user.name", identity)
        configured_email = self._effective_config_value("user.email", identity)
        author_expected = GitIdentity(
            name=os.environ.get("GIT_AUTHOR_NAME", configured_name or ""),
            email=os.environ.get(
                "GIT_AUTHOR_EMAIL",
                os.environ.get("EMAIL", configured_email or ""),
            ),
        )
        committer_expected = GitIdentity(
            name=os.environ.get("GIT_COMMITTER_NAME", configured_name or ""),
            email=os.environ.get(
                "GIT_COMMITTER_EMAIL",
                os.environ.get("EMAIL", configured_email or ""),
            ),
        )
        author = self._required_git(
            ("var", "GIT_AUTHOR_IDENT"),
            identity=identity,
            read_only=True,
        )
        committer = self._required_git(
            ("var", "GIT_COMMITTER_IDENT"),
            identity=identity,
            read_only=True,
        )
        observed = GitEffectiveIdentity(
            author=_parse_identity_line(author.stdout)[0],
            committer=_parse_identity_line(committer.stdout)[0],
        )
        if (
            observed.author != author_expected
            or observed.committer != committer_expected
        ):
            raise _AdapterFailure(GitFailureCode.IDENTITY_INVALID)
        return observed

    def _effective_config_value(
        self,
        key: str,
        identity: tuple[int, int],
    ) -> str | None:
        result = self._run_git(
            ("config", "--get", key),
            identity=identity,
            read_only=True,
            allow_failure=True,
        )
        if result.returncode != 0:
            return None
        return _decode_identity_component(result.stdout)

    def _head_id(
        self,
        identity: tuple[int, int],
        *,
        allow_absent: bool,
    ) -> str | None:
        result = self._run_git(
            ("rev-parse", "--verify", "HEAD"),
            identity=identity,
            read_only=True,
            allow_failure=allow_absent,
        )
        if result.returncode != 0:
            return None
        return _decode_object_id(result.stdout)

    def _symbolic_head(
        self,
        identity: tuple[int, int],
        *,
        allow_detached: bool = False,
    ) -> str | None:
        result = self._run_git(
            ("symbolic-ref", "--quiet", "HEAD"),
            identity=identity,
            read_only=True,
            allow_failure=allow_detached,
        )
        if result.returncode != 0:
            return None
        return _decode_ref(result.stdout)

    def _references(self, identity: tuple[int, int]) -> tuple[str, ...]:
        result = self._required_git(
            ("for-each-ref", "--format=%(refname)"),
            identity=identity,
            read_only=True,
        )
        references = tuple(
            _decode_ref(line) for line in result.stdout.splitlines() if line
        )
        if len(references) > _MAX_STATUS_ENTRIES:
            raise _AdapterFailure(GitFailureCode.PROCESS_OUTPUT_EXCEEDED)
        return references

    def _index_entries(
        self,
        identity: tuple[int, int],
    ) -> dict[str, tuple[str, str]]:
        result = self._required_git(
            ("ls-files", "--stage", "-z", "--"),
            identity=identity,
            read_only=True,
        )
        entries: dict[str, tuple[str, str]] = {}
        for raw in result.stdout.split(b"\x00"):
            if not raw:
                continue
            try:
                header, raw_path = raw.split(b"\t", 1)
                mode, object_id, stage = header.split(b" ")
            except ValueError:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            if stage != b"0" or _OBJECT_ID.fullmatch(object_id) is None:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            path = _decode_path(raw_path)
            if path in entries:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
        return entries

    def _tree_entries(
        self,
        treeish: str,
        identity: tuple[int, int],
        *,
        treeish_is_tree: bool = False,
    ) -> dict[str, tuple[str, str]]:
        del treeish_is_tree
        result = self._required_git(
            ("ls-tree", "-r", "-z", "--full-tree", treeish, "--"),
            identity=identity,
            read_only=True,
        )
        entries: dict[str, tuple[str, str]] = {}
        for raw in result.stdout.split(b"\x00"):
            if not raw:
                continue
            try:
                header, raw_path = raw.split(b"\t", 1)
                mode, object_type, object_id = header.split(b" ")
            except ValueError:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            if object_type != b"blob" or _OBJECT_ID.fullmatch(object_id) is None:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            path = _decode_path(raw_path)
            if path in entries:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
        return entries

    def _verify_entries(
        self,
        manifest: GitBaselineManifest,
        files: tuple[tuple[object, bytes], ...],
        entries: dict[str, tuple[str, str]],
        identity: tuple[int, int],
    ) -> None:
        if tuple(entries) != tuple(entry.path for entry in manifest.entries):
            raise _AdapterFailure(GitFailureCode.INDEX_MISMATCH)
        content_by_path = {entry.path: content for entry, content in files}
        for entry in manifest.entries:
            mode, object_id = entries[entry.path]
            if mode != "100644":
                raise _AdapterFailure(GitFailureCode.INDEX_MISMATCH)
            blob = self._required_git(
                ("cat-file", "blob", object_id),
                identity=identity,
                read_only=True,
            ).stdout
            if (
                blob != content_by_path[entry.path]
                or hashlib.sha256(blob).hexdigest() != entry.content_digest
            ):
                raise _AdapterFailure(GitFailureCode.INDEX_MISMATCH)

    def _write_tree(self, identity: tuple[int, int]) -> str:
        result = self._required_git(("write-tree",), identity=identity)
        return _decode_object_id(result.stdout)

    def _commit_message(self, object_id: str, identity: tuple[int, int]) -> bytes:
        payload = self._required_git(
            ("cat-file", "commit", object_id),
            identity=identity,
            read_only=True,
        ).stdout
        try:
            _, message = payload.split(b"\n\n", 1)
        except ValueError:
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        if not message.endswith(b"\n"):
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        return message[:-1]

    def _commit_metadata(
        self,
        object_id: str,
        identity: tuple[int, int],
    ) -> GitCommitMetadata:
        payload = self._required_git(
            ("cat-file", "commit", object_id),
            identity=identity,
            read_only=True,
        ).stdout
        try:
            raw_headers, message = payload.split(b"\n\n", 1)
        except ValueError:
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        tree_id: str | None = None
        parents: list[str] = []
        author: GitIdentity | None = None
        committer: GitIdentity | None = None
        authored_at: str | None = None
        committed_at: str | None = None
        for line in raw_headers.splitlines():
            if line.startswith(b"tree "):
                tree_id = _decode_object_id(line[5:])
            elif line.startswith(b"parent "):
                parents.append(_decode_object_id(line[7:]))
            elif line.startswith(b"author "):
                author, authored_at = _parse_identity_line(line[7:])
            elif line.startswith(b"committer "):
                committer, committed_at = _parse_identity_line(line[10:])
        if None in {tree_id, author, committer, authored_at, committed_at}:
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        return GitCommitMetadata(
            commit_id=object_id,
            tree_id=tree_id,
            parent_ids=tuple(parents),
            author=author,
            committer=committer,
            authored_at=authored_at,
            committed_at=committed_at,
            message_digest=hashlib.sha256(message).hexdigest(),
            message_utf8_bytes=len(message),
        )

    def _required_git(
        self,
        arguments: tuple[str, ...],
        *,
        identity: tuple[int, int],
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
    ) -> GitProcessResult:
        result = self._run_git(
            arguments,
            identity=identity,
            stdin=stdin,
            read_only=read_only,
            identity_environment=identity_environment,
            allow_failure=True,
        )
        if result.returncode != 0:
            raise _AdapterFailure(
                GitFailureCode.COMMAND_FAILED,
                exit_code=result.returncode,
            )
        return result

    def _run_git(
        self,
        arguments: tuple[str, ...],
        *,
        identity: tuple[int, int],
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
        allow_failure: bool = False,
    ) -> GitProcessResult:
        del allow_failure
        try:
            return GitProcessRunner(self._root).run(
                arguments,
                expected_identity=identity,
                stdin=stdin,
                read_only=read_only,
                identity_environment=identity_environment,
            )
        except GitProcessFailure as exc:
            raise _AdapterFailure(
                exc.code,
                exit_code=exc.exit_code,
            ) from None

    def _bind_root(self) -> tuple[int, int]:
        try:
            value = os.lstat(self._root)
            canonical = Path(os.path.realpath(self._root))
        except OSError:
            raise _AdapterFailure(GitFailureCode.UNSAFE_ROOT)
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or canonical != self._root
            or value.st_dev <= 0
            or value.st_ino <= 0
        ):
            raise _AdapterFailure(GitFailureCode.UNSAFE_ROOT)
        return value.st_dev, value.st_ino

    def _verify_root(self, identity: tuple[int, int]) -> None:
        if self._bind_root() != identity:
            raise _AdapterFailure(GitFailureCode.UNSAFE_ROOT)


def _bind_directory(path: Path) -> tuple[int, int]:
    try:
        value = os.lstat(path)
        canonical = Path(os.path.realpath(path))
    except OSError:
        raise _external_operation_failure() from None
    if (
        not stat.S_ISDIR(value.st_mode)
        or stat.S_ISLNK(value.st_mode)
        or canonical != path
        or value.st_dev <= 0
        or value.st_ino <= 0
        or not value.st_mode & 0o444
        or not value.st_mode & 0o111
    ):
        raise _external_operation_failure()
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
        raise _external_operation_failure() from None
    if (
        (opened.st_dev, opened.st_ino) != identity
        or not stat.S_ISDIR(opened.st_mode)
        or not opened.st_mode & 0o444
        or not opened.st_mode & 0o111
    ):
        os.close(descriptor)
        raise _external_operation_failure()
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
        raise _external_operation_failure() from None
    try:
        opened = os.fstat(descriptor)
        initial = _stat_snapshot(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not opened.st_mode & 0o444
            or opened.st_size < 0
            or opened.st_size > _MAX_ADMINISTRATIVE_FILE_BYTES
        ):
            raise _external_operation_failure()
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > _MAX_ADMINISTRATIVE_FILE_BYTES:
                raise _external_operation_failure()
        if (
            len(payload) != opened.st_size
            or _descriptor_snapshot(descriptor) != initial
        ):
            raise _external_operation_failure()
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
        raise _external_operation_failure()
    try:
        value = payload[len(prefix) : -1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _external_operation_failure() from None
    path = Path(value)
    if not value or not path.is_absolute() or path != Path(os.path.normpath(value)):
        raise _external_operation_failure()
    return path


def _decode_common_directory(payload: bytes) -> Path:
    if (
        not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
        or b"\r" in payload
        or b"\x00" in payload
    ):
        raise _external_operation_failure()
    try:
        value = payload[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _external_operation_failure() from None
    path = Path(value)
    if not value or path.is_absolute():
        raise _external_operation_failure()
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
        raise _external_operation_failure() from None
    if directory:
        valid_type = stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode)
        valid_access = bool(value.st_mode & 0o444) and bool(value.st_mode & 0o111)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    else:
        valid_type = stat.S_ISREG(value.st_mode) and not stat.S_ISLNK(value.st_mode)
        valid_access = bool(value.st_mode & 0o444)
        flags = os.O_RDONLY
    if not valid_type or not valid_access:
        raise _external_operation_failure()
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=administrative_descriptor)
    except OSError:
        raise _external_operation_failure() from None
    try:
        opened = os.fstat(descriptor)
        snapshot = _stat_snapshot(value)
        if _stat_snapshot(opened) != snapshot:
            raise _external_operation_failure()
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
    raise _external_operation_failure()


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
        raise _external_operation_failure() from None


def _physical_identity_digest(label: str, *identities: tuple[int, int]) -> str:
    payload = "\0".join(
        (label, *(f"{device}:{inode}" for device, inode in identities))
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _external_operation_failure() -> _AdapterFailure:
    return _AdapterFailure(
        GitFailureCode.RECOVERY_REQUIRED,
        repository_state=GitRepositoryState.RECOVERY_REQUIRED,
    )


def _require_manifest(value: object) -> GitBaselineManifest:
    if type(value) is not GitBaselineManifest:
        raise _cleared_error(GitFailureCode.INVALID_MANIFEST)
    try:
        supplied_entries = value.entries
        supplied_digest = value.manifest_digest
        if (
            type(supplied_entries) is not tuple
            or not supplied_entries
            or len(supplied_entries) > _MAX_MANIFEST_ENTRIES
            or any(type(entry) is not GitManifestEntry for entry in supplied_entries)
        ):
            raise _cleared_error(GitFailureCode.INVALID_MANIFEST)
        entries = tuple(
            GitManifestEntry(
                path=entry.path,
                content_digest=entry.content_digest,
            )
            for entry in supplied_entries
        )
        validated = GitBaselineManifest(entries)
    except (AttributeError, GitOperationError):
        raise _cleared_error(GitFailureCode.INVALID_MANIFEST)
    if validated.manifest_digest != supplied_digest:
        raise _cleared_error(GitFailureCode.INVALID_MANIFEST)
    return validated


def _derive_tree_id(entries: dict[str, tuple[str, str]]) -> str:
    object_lengths = {len(object_id) for _, object_id in entries.values()}
    if len(object_lengths) != 1:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    object_length = object_lengths.pop()
    if object_length == 40:
        hash_constructor = hashlib.sha1
    elif object_length == 64:
        hash_constructor = hashlib.sha256
    else:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)

    tree: dict[bytes, object] = {}
    for path, (mode, object_id) in entries.items():
        parts = tuple(part.encode("utf-8", errors="strict") for part in path.split("/"))
        if not parts or any(not part for part in parts):
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        current = tree
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if type(child) is not dict:
                raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
            current = child
        name = parts[-1]
        if name in current:
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        try:
            raw_object_id = bytes.fromhex(object_id)
        except ValueError:
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        if mode != "100644" or len(raw_object_id) != object_length // 2:
            raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
        current[name] = (mode.encode("ascii"), raw_object_id)

    def hash_tree(directory: dict[bytes, object]) -> bytes:
        rows: list[tuple[bytes, bytes]] = []
        for name, value in directory.items():
            if type(value) is dict:
                mode = b"40000"
                raw_object_id = hash_tree(value)
                sort_name = name + b"/"
            else:
                mode, raw_object_id = value
                sort_name = name + b"\x00"
            rows.append(
                (
                    sort_name,
                    mode + b" " + name + b"\x00" + raw_object_id,
                )
            )
        payload = b"".join(row for _, row in sorted(rows))
        header = f"tree {len(payload)}\0".encode("ascii")
        return hash_constructor(header + payload).digest()

    return hash_tree(tree).hex()


def _cleared_error(code: GitFailureCode) -> GitOperationError:
    error = GitOperationError(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


def _same_real_path(observed: str, expected: Path) -> bool:
    try:
        candidate = Path(os.path.realpath(observed))
    except (OSError, ValueError):
        return False
    return candidate == expected


def _decode_single_line(value: bytes) -> str:
    if not value or len(value) > 4_096:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    try:
        text = value.decode("utf-8", errors="strict").rstrip("\n")
    except UnicodeDecodeError:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    if not text or "\x00" in text or "\r" in text or "\n" in text:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return text


def _decode_object_id(value: bytes) -> str:
    raw = value.strip()
    if _OBJECT_ID.fullmatch(raw) is None:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return raw.decode("ascii")


def _decode_ref(value: bytes) -> str:
    raw = value.strip()
    if (
        not raw
        or len(raw) > 1_024
        or not raw.startswith(b"refs/")
        or any(character < 0x20 or character == 0x7F for character in raw)
        or b".." in raw
        or raw.startswith(b"-")
    ):
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)


def _decode_path(value: bytes) -> str:
    if not value or len(value) > 1_024 or b"\x00" in value:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    try:
        path = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    if path.startswith("/") or "\r" in path or "\n" in path:
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
    return path


def _parse_identity_line(value: bytes) -> tuple[GitIdentity, str]:
    match = _IDENT.fullmatch(value.strip())
    if match is None:
        raise _AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    try:
        name = match.group("name").decode("utf-8", errors="strict")
        email = match.group("email").decode("utf-8", errors="strict")
        time_value = match.group("time").decode("ascii")
        zone = match.group("zone").decode("ascii")
    except UnicodeError:
        raise _AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    try:
        identity = GitIdentity(name=name, email=email)
    except GitOperationError:
        raise _AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    return identity, f"{time_value} {zone}"


def _decode_identity_component(value: bytes) -> str:
    raw = value.rstrip(b"\n")
    if not raw or len(raw) > 1_024 or b"\x00" in raw or b"\r" in raw or b"\n" in raw:
        raise _AdapterFailure(GitFailureCode.IDENTITY_INVALID)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _AdapterFailure(GitFailureCode.IDENTITY_INVALID)


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
        raise _AdapterFailure(GitFailureCode.OUTPUT_INVALID)
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


__all__ = ["GitRepositoryAdapter"]
