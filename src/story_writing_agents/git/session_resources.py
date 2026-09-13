"""Locked, restart-safe ownership of exact local Git session resources."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Iterator

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on unsupported platforms
    _fcntl = None  # type: ignore[assignment]

from .contracts import (
    GIT_BASELINE_BRANCH,
    GIT_BASELINE_COMMIT_MESSAGE,
    GitBaselineManifest,
    GitEffectiveIdentity,
    GitIdentity,
    GitManifestEntry,
    GitOperationError,
)
from .process import GitProcessFailure, GitProcessResult, GitProcessRunner
from .repository_lock import (
    GitRepositoryLease,
    GitRepositoryLockFailure,
    GitRepositoryLockFailureCode,
    current_thread_token,
    lock_git_repository,
)
from .session_contracts import (
    GIT_SESSION_BRANCH_PREFIX,
    GIT_SESSION_INTERNAL_REF_PREFIX,
    GIT_SESSION_PROVISIONING_DIRECTORY,
    GIT_SESSION_PROVISIONING_LOCK_NAME,
    GIT_SESSION_SEED_MESSAGE_PREFIX,
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionProvisioningJournal,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceInventory,
    GitSessionResourceState,
    decode_session_provisioning_journal,
    encode_session_provisioning_journal,
)


_WORKSPACE_MUTABLE_CONFIGURATION_PATHS = frozenset(
    {"config/system_defaults.json", "config/agent_configs.json"}
)


_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_MAX_REFS = 65_536
_MAX_WORKTREES = 65_536
_MAX_ADMIN_FILE_BYTES = 64 * 1024
_ZERO_BY_LENGTH = {40: "0" * 40, 64: "0" * 64}


_GENERATION_DESCRIPTOR_COUNT = 6
_GenerationFileFacts = tuple[int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class GitSessionGenerationIdentity:
    """Content-free physical identity of one validated linked worktree."""

    session: GitSessionIdentity = dataclass_field(repr=False)
    administrative_root: Path = dataclass_field(repr=False)
    control_root_identity: tuple[int, int]
    common_git_identity: tuple[int, int]
    administrative_identity: tuple[int, int]
    worktree_identity: tuple[int, int]
    git_indirection_facts: _GenerationFileFacts = dataclass_field(repr=False)
    common_indirection_facts: _GenerationFileFacts = dataclass_field(repr=False)
    git_indirection_sha256: str = dataclass_field(repr=False)
    common_indirection_sha256: str = dataclass_field(repr=False)
    _creator_pid: int = dataclass_field(init=False, repr=False, compare=False)
    _creator_thread_token: object = dataclass_field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if (
            type(self.session) is not GitSessionIdentity
            or not isinstance(self.administrative_root, Path)
            or not self.administrative_root.is_absolute()
            or self.administrative_root
            != Path(os.path.normpath(os.fspath(self.administrative_root)))
            or any(
                not _valid_generation_directory_identity(identity)
                for identity in (
                    self.control_root_identity,
                    self.common_git_identity,
                    self.administrative_identity,
                    self.worktree_identity,
                )
            )
            or any(
                not _valid_generation_file_facts(facts)
                for facts in (
                    self.git_indirection_facts,
                    self.common_indirection_facts,
                )
            )
            or any(
                type(digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for digest in (
                    self.git_indirection_sha256,
                    self.common_indirection_sha256,
                )
            )
        ):
            raise TypeError("Git session generation identity is invalid")
        object.__setattr__(self, "_creator_pid", os.getpid())
        object.__setattr__(
            self,
            "_creator_thread_token",
            current_thread_token(),
        )

    def _require_creator(self) -> None:
        if (
            self._creator_pid != os.getpid()
            or self._creator_thread_token is not current_thread_token()
        ):
            raise _generation_error()


class GitSessionGenerationPin:
    """Opaque retained descriptors for one physical linked-worktree generation."""

    __slots__ = (
        "_creator_pid",
        "_creator_thread_token",
        "_descriptors",
        "_identity",
        "_lock",
    )

    def __init__(
        self,
        identity: GitSessionGenerationIdentity,
        descriptors: tuple[int, ...],
    ) -> None:
        if (
            type(identity) is not GitSessionGenerationIdentity
            or type(descriptors) is not tuple
            or len(descriptors) != _GENERATION_DESCRIPTOR_COUNT
            or any(type(descriptor) is not int or descriptor < 0 for descriptor in descriptors)
        ):
            raise TypeError("Git session generation pin is invalid")
        identity._require_creator()
        _require_retained_generation_descriptors(identity, descriptors)
        self._identity = identity
        self._descriptors = descriptors
        self._creator_pid = os.getpid()
        self._creator_thread_token = current_thread_token()
        self._lock = threading.Lock()

    def require_current(self) -> None:
        """Require the named topology and every retained identity to remain exact."""

        if (
            self._creator_pid != os.getpid()
            or self._creator_thread_token is not current_thread_token()
        ):
            raise _generation_error()
        identity = self._identity
        with lock_git_repository(identity.session.main_root) as lease:
            current, descriptors = _capture_git_session_generation(
                lease,
                identity.session,
            )
            try:
                if current != identity:
                    raise _generation_error()
            finally:
                _close_generation_descriptors(descriptors)
            with self._lock:
                retained = self._descriptors
                if not retained:
                    raise _generation_error()
                _require_retained_generation_descriptors(identity, retained)

    def close(self) -> None:
        """Release every retained descriptor idempotently."""

        if self._creator_pid != os.getpid():
            descriptors = self._descriptors
            self._descriptors = ()
            _close_generation_descriptors(descriptors)
            return
        with self._lock:
            descriptors = self._descriptors
            self._descriptors = ()
        _close_generation_descriptors(descriptors)

    def __repr__(self) -> str:
        return "GitSessionGenerationPin(<opaque>)"

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("Git session generation pins cannot be serialized")


def capture_locked_git_session_generation(
    lease: GitRepositoryLease,
    resources: GitSessionResourceInspection,
    *,
    expected_worktree_identity: tuple[int, int],
) -> GitSessionGenerationIdentity:
    """Capture physical linked-worktree identity through an existing lease."""

    if (
        type(resources) is not GitSessionResourceInspection
        or resources.state is not GitSessionResourceState.READY
        or not _valid_generation_directory_identity(expected_worktree_identity)
    ):
        raise TypeError("ready Git session resources are required")
    identity, descriptors = _capture_git_session_generation(
        lease,
        resources.identity,
    )
    try:
        if identity.worktree_identity != expected_worktree_identity:
            raise _generation_error()
        return identity
    finally:
        _close_generation_descriptors(descriptors)


def acquire_git_session_generation_pin(
    identity: GitSessionGenerationIdentity,
) -> GitSessionGenerationPin:
    """Acquire an opaque pin under one short physical-identity lock."""

    if type(identity) is not GitSessionGenerationIdentity:
        raise TypeError("Git session generation identity is invalid")
    identity._require_creator()
    pin: GitSessionGenerationPin | None = None
    descriptors: tuple[int, ...] = ()
    try:
        with lock_git_repository(identity.session.main_root) as lease:
            current, descriptors = _capture_git_session_generation(
                lease,
                identity.session,
            )
            if current != identity:
                raise _generation_error()
            pin = GitSessionGenerationPin(identity, descriptors)
            descriptors = ()
        return pin
    except BaseException:
        _close_generation_descriptors(descriptors)
        if pin is not None:
            pin.close()
        raise


@dataclass(frozen=True, slots=True)
class GitSessionRecoveryTopology:
    """Exact session topology whose only deferred fact is index cleanliness."""

    resources: GitSessionResourceInspection
    main_status_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.resources) is not GitSessionResourceInspection
            or self.resources.state is not GitSessionResourceState.READY
            or type(self.main_status_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.main_status_sha256) is None
        ):
            raise TypeError("recovery topology requires exact ready resources")


def inspect_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Inspect one requested triple without creating or repairing it."""

    failure: GitSessionResourceError | None = None
    try:
        main_root, expected_manifest, identity = _request(root, manifest, session_id)
        with _repository_lock(main_root, create=False) as repository:
            return repository.inspect(
                expected_manifest,
                identity,
                allowed_worktree_changes=allowed_worktree_changes,
            )
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


def list_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInventory:
    """Return the exhaustive correlated inventory without mutation."""

    failure: GitSessionResourceError | None = None
    try:
        main_root, expected_manifest = _root_and_manifest(root, manifest)
        with _repository_lock(main_root, create=False) as repository:
            return repository.list(
                expected_manifest,
                allowed_worktree_changes=allowed_worktree_changes,
            )
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


def provision_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Create or resume only one exact ordered session-resource prefix."""

    failure: GitSessionResourceError | None = None
    try:
        supplied = _path(root)
        main_root, expected_manifest, identity = _request(root, manifest, session_id)
        if supplied != main_root:
            raise _resource_error(
                GitSessionFailureCode.INVALID_REQUEST,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.NONE,
            )
        with _repository_lock(main_root, create=True) as repository:
            return repository.provision(
                expected_manifest,
                identity,
                allowed_worktree_changes=allowed_worktree_changes,
            )
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


def require_ready_git_session_resources(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Require one exact ready triple without provisioning it."""

    failure: GitSessionResourceError | None = None
    try:
        result = inspect_git_session_resources(
            root,
            manifest,
            session_id,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        if result.state is not GitSessionResourceState.READY:
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.REQUIRE_READY,
                result.state,
                result.recovery_action,
            )
        return result
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


def inspect_locked_git_session_resources(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Inspect one session through an already-held shared repository lease."""

    failure: GitSessionResourceError | None = None
    try:
        repository, expected_manifest, identity = _locked_request(
            lease,
            manifest,
            session_id,
        )
        return repository.inspect(
            expected_manifest,
            identity,
            allowed_worktree_changes=allowed_worktree_changes,
        )
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


def require_ready_locked_git_session_resources(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionResourceInspection:
    """Require one ready session through an already-held repository lease."""

    failure: GitSessionResourceError | None = None
    try:
        result = inspect_locked_git_session_resources(
            lease,
            manifest,
            session_id,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        if result.state is not GitSessionResourceState.READY:
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.REQUIRE_READY,
                result.state,
                result.recovery_action,
            )
        return result
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


def require_story_commit_recovery_locked_git_session_resources(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
    *,
    allowed_worktree_changes: tuple[str, ...] = (),
) -> GitSessionRecoveryTopology:
    """Require exact recovery topology while deferring only session-index cleanliness."""

    failure: GitSessionResourceError | None = None
    try:
        repository, expected_manifest, identity = _locked_request(
            lease,
            manifest,
            session_id,
        )
        baseline = repository._verify_main(
            expected_manifest,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        main_status_sha256 = repository._main_status_sha256(
            allowed_worktree_changes=allowed_worktree_changes,
        )
        resources = repository._classify(
            identity,
            baseline,
            repository._facts(),
            require_clean_session_index=False,
        )
        if resources.state is not GitSessionResourceState.READY:
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.REQUIRE_READY,
                resources.state,
                resources.recovery_action,
            )
        return GitSessionRecoveryTopology(resources, main_status_sha256)
    except GitSessionResourceError as exc:
        failure = _clear_resource_error(exc)
    raise failure


class _Repository:
    __slots__ = ("common_descriptor", "common_git", "identity", "main_root", "runner")

    def __init__(
        self,
        main_root: Path,
        common_git: Path,
        common_descriptor: int,
        identity: tuple[int, int],
    ) -> None:
        self.main_root = main_root
        self.common_git = common_git
        self.common_descriptor = common_descriptor
        self.identity = identity
        self.runner = GitProcessRunner(main_root)

    def inspect(
        self,
        manifest: GitBaselineManifest,
        identity: GitSessionIdentity,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> GitSessionResourceInspection:
        baseline = self._verify_main(
            manifest,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        facts = self._facts()
        return self._classify(identity, baseline, facts)

    def list(
        self,
        manifest: GitBaselineManifest,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> GitSessionResourceInventory:
        baseline = self._verify_main(
            manifest,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        facts = self._facts()
        session_ids = self._inventory_session_ids(facts)
        sessions = tuple(
            self._classify(
                GitSessionIdentity(self.main_root, session_id), baseline, facts
            )
            for session_id in session_ids
        )
        self._require_exhaustive(facts, sessions)
        return GitSessionResourceInventory(baseline, sessions)

    def provision(
        self,
        manifest: GitBaselineManifest,
        identity: GitSessionIdentity,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> GitSessionResourceInspection:
        baseline = self._verify_main(
            manifest,
            allowed_worktree_changes=allowed_worktree_changes,
        )
        before = self._facts()
        self._require_other_sessions_exact(identity, baseline, before)
        current = self._classify(identity, baseline, before)
        if current.state is GitSessionResourceState.READY:
            return current
        if current.state not in {
            GitSessionResourceState.ABSENT,
            GitSessionResourceState.PROVISIONING,
        }:
            return current
        if self._unregistered_worktree_collides(identity, before):
            return _recovery(identity, baseline)
        journal = self._journal(identity)
        if journal is None:
            if current.state is not GitSessionResourceState.ABSENT:
                return _recovery(identity, baseline)
            effective_identity = self._effective_identity()
            journal = GitSessionProvisioningJournal(
                baseline_commit_id=baseline,
                effective_identity=effective_identity,
                provisioning_id=uuid.uuid4().hex,
                seed_timestamp_seconds=int(time.time()),
                session_id=identity.session_id,
            )
            self._write_journal(journal)
        elif (
            journal.baseline_commit_id != baseline
            or journal.session_id != identity.session_id
        ):
            return _recovery(identity, baseline)

        try:
            observed = self._facts()
            prefix = self._classify(identity, baseline, observed, journal=journal)
            if prefix.state is not GitSessionResourceState.PROVISIONING:
                return prefix
            seed = self._seed(journal)
            self._create_seed(seed, journal)
            observed = self._facts()
            branch_tip = observed.branches.get(identity.branch)
            internal_tip = observed.internal_refs.get(identity.internal_ref)
            worktree = observed.worktrees.get(identity.worktree_root)
            if internal_tip is None:
                self._cas_ref(identity.internal_ref, seed)
            elif internal_tip != seed and not self._root_anchor(internal_tip, seed):
                return _recovery(identity, baseline)
            observed = self._facts()
            branch_tip = observed.branches.get(identity.branch)
            if branch_tip is None:
                self._cas_ref(f"refs/heads/{identity.branch}", baseline)
            elif not self._root_anchor(branch_tip, baseline):
                return _recovery(identity, baseline)
            observed = self._facts()
            worktree = observed.worktrees.get(identity.worktree_root)
            if worktree is None:
                if _path_lexists(identity.worktree_root):
                    return _recovery(identity, baseline)
                self._required(
                    (
                        "worktree",
                        "add",
                        "--no-guess-remote",
                        os.fspath(identity.worktree_root),
                        identity.branch,
                    )
                )
            completed_facts = self._facts()
            completed = self._classify(
                identity,
                baseline,
                completed_facts,
                journal=journal,
            )
            if (
                completed.state is not GitSessionResourceState.PROVISIONING
                or completed_facts.internal_refs.get(identity.internal_ref) is None
                or completed_facts.branches.get(identity.branch) is None
                or completed_facts.worktrees.get(identity.worktree_root) is None
            ):
                return completed
            self._remove_journal(identity)
            final_baseline = self._verify_main(
                manifest,
                allowed_worktree_changes=allowed_worktree_changes,
            )
            if final_baseline != baseline:
                return _recovery(identity, baseline)
            return self._classify(identity, baseline, self._facts())
        except BaseException as exc:
            after: GitSessionResourceInspection | None = None
            try:
                after = self._classify(
                    identity, baseline, self._facts(), journal=journal
                )
            except BaseException:
                pass
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if after is not None and after.state is GitSessionResourceState.READY:
                return after
            if after is not None and after.state in {
                GitSessionResourceState.PROVISIONING,
                GitSessionResourceState.RECOVERY_REQUIRED,
            }:
                return after
            if isinstance(exc, GitSessionResourceError):
                raise exc from None
            return _unknown(identity, baseline)

    def _unregistered_worktree_collides(
        self,
        identity: GitSessionIdentity,
        facts: _Facts,
    ) -> bool:
        if identity.worktree_root in facts.worktrees:
            return False
        parent = identity.worktree_root.parent
        try:
            observed = os.lstat(parent)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if (
            not stat.S_ISDIR(observed.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or Path(os.path.realpath(parent)) != parent
        ):
            return True
        descriptor = -1
        try:
            descriptor = _open_bound_directory(
                parent,
                (observed.st_dev, observed.st_ino),
            )
        except GitSessionResourceError:
            pass
        if descriptor < 0:
            return True
        try:
            try:
                os.stat(
                    identity.worktree_root.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return False
            except OSError:
                return True
            return True
        finally:
            os.close(descriptor)

    def _verify_main(
        self,
        manifest: GitBaselineManifest,
        *,
        allowed_worktree_changes: tuple[str, ...] = (),
    ) -> str:
        baseline = _object_id(
            self._required(
                ("rev-parse", "--verify", f"refs/heads/{GIT_BASELINE_BRANCH}"),
                read_only=True,
            ).stdout
        )
        symbolic = _line(
            self._required(("symbolic-ref", "--quiet", "HEAD"), read_only=True).stdout
        )
        if symbolic != f"refs/heads/{GIT_BASELINE_BRANCH}":
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        parents = _lines(
            self._required(
                ("show", "-s", "--format=%P", baseline), read_only=True
            ).stdout
        )
        message = self._required(
            ("show", "-s", "--format=%B", baseline), read_only=True
        ).stdout.rstrip(b"\n")
        if (
            parents not in {(), ("",)}
            or message != GIT_BASELINE_COMMIT_MESSAGE.encode()
        ):
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        expected = {entry.path: entry.content_digest for entry in manifest.entries}
        tree = self._tree_digests(baseline)
        if tree != expected:
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        status = self._required(
            ("status", "--porcelain=v1", "-z", "--untracked-files=no"), read_only=True
        )
        if not _status_matches_allowed_worktree_changes(
            status.stdout,
            allowed_worktree_changes,
        ):
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return baseline

    def _main_status_sha256(
        self,
        *,
        allowed_worktree_changes: tuple[str, ...],
    ) -> str:
        """Return exact allowed main-worktree status bytes under this lease."""

        status_arguments = (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=no",
        )
        diff_arguments = (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--",
            *allowed_worktree_changes,
        )
        status = self._required(status_arguments, read_only=True).stdout
        content_delta = self._required(diff_arguments, read_only=True).stdout
        confirmed_status = self._required(status_arguments, read_only=True).stdout
        confirmed_delta = self._required(diff_arguments, read_only=True).stdout
        if not _status_matches_allowed_worktree_changes(
            status,
            allowed_worktree_changes,
        ) or (status, content_delta) != (confirmed_status, confirmed_delta):
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return hashlib.sha256(status + b"\0" + content_delta).hexdigest()

    def _tree_digests(self, commit: str) -> dict[str, str]:
        result = self._required(("ls-tree", "-r", "-z", commit), read_only=True)
        values: dict[str, str] = {}
        for row in result.stdout.split(b"\x00"):
            if not row:
                continue
            try:
                header, raw_path = row.split(b"\t", 1)
                mode, kind, object_id = header.split(b" ")
            except ValueError:
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                ) from None
            if mode != b"100644" or kind != b"blob":
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            path = _utf8(raw_path)
            blob = self._required(
                ("cat-file", "blob", object_id.decode("ascii")), read_only=True
            ).stdout
            values[path] = hashlib.sha256(blob).hexdigest()
            if len(values) > 65_536:
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
        return values

    def _facts(self) -> _Facts:
        branches: dict[str, str] = {}
        internal: dict[str, str] = {}
        refs = self._required(
            ("for-each-ref", "--format=%(refname)%00%(objectname)"), read_only=True
        )
        for row in refs.stdout.splitlines():
            if not row:
                continue
            malformed = False
            try:
                raw_name, raw_oid = row.split(b"\x00", 1)
            except ValueError:
                malformed = True
            if malformed:
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            name = _utf8(raw_name)
            oid = _object_id(raw_oid)
            if name.startswith("refs/heads/"):
                branches[name.removeprefix("refs/heads/")] = oid
            elif name.startswith(GIT_SESSION_INTERNAL_REF_PREFIX):
                internal[name] = oid
            else:
                raise _resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            if len(branches) + len(internal) > _MAX_REFS:
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
        worktrees = self._worktrees()
        journals = self._journals()
        return _Facts(branches, internal, worktrees, journals)

    def _worktrees(self) -> dict[Path, tuple[str, str]]:
        output = self._required(
            ("worktree", "list", "--porcelain", "-z"), read_only=True
        ).stdout
        records: dict[Path, tuple[str, str]] = {}
        current: dict[str, str] = {}
        for field in output.split(b"\x00"):
            if not field:
                if current:
                    self._finish_worktree(current, records)
                    current = {}
                continue
            try:
                key, value = field.split(b" ", 1)
            except ValueError:
                key, value = field, b""
            current[key.decode("ascii")] = _utf8(value)
        if current:
            self._finish_worktree(current, records)
        if len(records) > _MAX_WORKTREES:
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return records

    def _finish_worktree(
        self,
        current: dict[str, str],
        records: dict[Path, tuple[str, str]],
    ) -> None:
        if (
            "worktree" not in current
            or "HEAD" not in current
            or "branch" not in current
        ):
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        path = Path(current["worktree"])
        if not path.is_absolute() or path in records:
            raise _resource_error(
                GitSessionFailureCode.RESOURCE_COLLISION,
                GitSessionOperationKind.INSPECT,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        records[path] = (_object_id(current["HEAD"]), current["branch"])

    def _journals(self) -> dict[str, GitSessionProvisioningJournal]:
        directory = self._open_provisioning_directory(create=False)
        if directory is None:
            return {}
        try:
            result: dict[str, GitSessionProvisioningJournal] = {}
            for name in sorted(os.listdir(directory)):
                if name == GIT_SESSION_PROVISIONING_LOCK_NAME:
                    continue
                if not name.endswith(".json"):
                    raise _resource_error(
                        GitSessionFailureCode.JOURNAL_INVALID,
                        GitSessionOperationKind.INSPECT,
                        GitSessionResourceState.RECOVERY_REQUIRED,
                        GitSessionRecoveryAction.OPERATOR_RECOVERY,
                    )
                session_id = name[:-5]
                payload = _read_file(directory, name)
                journal = decode_session_provisioning_journal(payload)
                if journal.session_id != session_id or session_id in result:
                    raise _resource_error(
                        GitSessionFailureCode.JOURNAL_INVALID,
                        GitSessionOperationKind.INSPECT,
                        GitSessionResourceState.RECOVERY_REQUIRED,
                        GitSessionRecoveryAction.OPERATOR_RECOVERY,
                    )
                result[session_id] = journal
            return result
        finally:
            os.close(directory)

    def _journal(
        self, identity: GitSessionIdentity
    ) -> GitSessionProvisioningJournal | None:
        return self._facts().journals.get(identity.session_id)

    def _write_journal(self, journal: GitSessionProvisioningJournal) -> None:
        directory = self._open_provisioning_directory(create=True)
        assert directory is not None
        name = f"{journal.session_id}.json"
        payload = encode_session_provisioning_journal(journal)
        descriptor = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, 0o600, dir_fd=directory)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.fsync(directory)
        except OSError:
            raise _resource_error(
                GitSessionFailureCode.MUTATION_FAILED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory)

    def _remove_journal(self, identity: GitSessionIdentity) -> None:
        directory = self._open_provisioning_directory(create=False)
        if directory is None:
            raise _resource_error(
                GitSessionFailureCode.OUTCOME_UNKNOWN,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        try:
            os.unlink(f"{identity.session_id}.json", dir_fd=directory)
            os.fsync(directory)
        except OSError:
            raise _resource_error(
                GitSessionFailureCode.OUTCOME_UNKNOWN,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        finally:
            os.close(directory)

    def _open_provisioning_directory(self, *, create: bool) -> int | None:
        current = os.dup(self.common_descriptor)
        try:
            for part in GIT_SESSION_PROVISIONING_DIRECTORY.split("/"):
                try:
                    child = _open_directory(current, part)
                except FileNotFoundError:
                    if not create:
                        os.close(current)
                        return None
                    os.mkdir(part, 0o700, dir_fd=current)
                    os.fsync(current)
                    child = _open_directory(current, part)
                os.close(current)
                current = child
            return current
        except BaseException:
            try:
                os.close(current)
            except OSError:
                pass
            raise

    def _classify(
        self,
        identity: GitSessionIdentity,
        baseline: str,
        facts: _Facts,
        *,
        journal: GitSessionProvisioningJournal | None = None,
        require_clean_session_index: bool = True,
    ) -> GitSessionResourceInspection:
        journal = journal or facts.journals.get(identity.session_id)
        branch = facts.branches.get(identity.branch)
        internal = facts.internal_refs.get(identity.internal_ref)
        worktree = facts.worktrees.get(identity.worktree_root)
        present = (internal is not None, branch is not None, worktree is not None)
        if present == (False, False, False):
            if journal is None:
                return _absent(identity, baseline)
            if (
                journal.baseline_commit_id == baseline
                and journal.session_id == identity.session_id
            ):
                return _provisioning(identity, baseline)
            return _recovery(identity, baseline)
        if journal is None and present != (True, True, True):
            return _recovery(identity, baseline)
        if present not in {
            (True, False, False),
            (True, True, False),
            (True, True, True),
        }:
            return _recovery(identity, baseline)
        if branch is not None and not self._root_anchor(branch, baseline):
            return _recovery(identity, baseline)
        seed = None
        if internal is not None:
            seed = self._root_commit(internal)
            if seed is None or not self._verify_seed(seed, identity, journal):
                return _recovery(identity, baseline)
        if journal is not None and (
            (branch is not None and branch != baseline)
            or (internal is not None and internal != seed)
        ):
            return _recovery(identity, baseline)
        if worktree is not None:
            worktree_head, worktree_branch = worktree
            if (
                worktree_branch != f"refs/heads/{identity.branch}"
                or worktree_head != branch
            ):
                return _recovery(identity, baseline)
            if (
                not self._verify_worktree_indirection(identity)
                or (
                    require_clean_session_index
                    and not self._session_index_is_clean(identity)
                )
            ):
                return _recovery(identity, baseline)
        if present == (True, True, True):
            return GitSessionResourceInspection(
                identity=identity,
                state=(
                    GitSessionResourceState.READY
                    if journal is None
                    else GitSessionResourceState.PROVISIONING
                ),
                recovery_action=(
                    GitSessionRecoveryAction.REOPEN
                    if journal is None
                    else GitSessionRecoveryAction.RETRY_CREATE
                ),
                baseline_commit_id=baseline,
                seed_commit_id=seed,
                initial_branch_tip=baseline,
                current_branch_tip=branch,
                initial_internal_tip=seed,
                current_internal_tip=internal,
            )
        if journal is not None:
            return GitSessionResourceInspection(
                identity=identity,
                state=GitSessionResourceState.PROVISIONING,
                recovery_action=GitSessionRecoveryAction.RETRY_CREATE,
                baseline_commit_id=baseline,
                seed_commit_id=seed,
                initial_branch_tip=baseline if branch is not None else None,
                current_branch_tip=branch,
                initial_internal_tip=seed,
                current_internal_tip=internal,
            )
        return _recovery(identity, baseline)

    def _inventory_session_ids(self, facts: _Facts) -> tuple[str, ...]:
        values: set[str] = set(facts.journals)
        for branch in facts.branches:
            if branch == GIT_BASELINE_BRANCH:
                continue
            if not branch.startswith(GIT_SESSION_BRANCH_PREFIX):
                raise _resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.LIST,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            values.add(branch.removeprefix(GIT_SESSION_BRANCH_PREFIX))
        for ref in facts.internal_refs:
            values.add(ref.removeprefix(GIT_SESSION_INTERNAL_REF_PREFIX))
        for path in facts.worktrees:
            if path == self.main_root:
                continue
            parent = self.main_root.with_name(f"{self.main_root.name}.story-worktrees")
            try:
                relative = path.relative_to(parent)
            except ValueError:
                raise _resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.LIST,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                ) from None
            if len(relative.parts) != 1:
                raise _resource_error(
                    GitSessionFailureCode.RESOURCE_COLLISION,
                    GitSessionOperationKind.LIST,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            values.add(relative.name)
        identities = tuple(
            GitSessionIdentity(self.main_root, value) for value in values
        )
        paths = [
            os.path.normcase(os.fspath(value.worktree_root)) for value in identities
        ]
        if len(paths) != len(set(paths)):
            raise _resource_error(
                GitSessionFailureCode.RESOURCE_COLLISION,
                GitSessionOperationKind.LIST,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return tuple(sorted(values, key=lambda value: value.encode("ascii")))

    def _require_exhaustive(
        self,
        facts: _Facts,
        sessions: tuple[GitSessionResourceInspection, ...],
    ) -> None:
        allowed_branches = {
            GIT_BASELINE_BRANCH,
            *(value.identity.branch for value in sessions),
        }
        allowed_refs = {value.identity.internal_ref for value in sessions}
        allowed_worktrees = {
            self.main_root,
            *(value.identity.worktree_root for value in sessions),
        }
        allowed_journals = {
            value.identity.session_id
            for value in sessions
            if value.state is GitSessionResourceState.PROVISIONING
        }
        if (
            GIT_BASELINE_BRANCH not in facts.branches
            or self.main_root not in facts.worktrees
            or not set(facts.branches).issubset(allowed_branches)
            or not set(facts.internal_refs).issubset(allowed_refs)
            or not set(facts.worktrees).issubset(allowed_worktrees)
            or set(facts.journals) != allowed_journals
        ):
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.LIST,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )

    def _require_other_sessions_exact(
        self,
        requested: GitSessionIdentity,
        baseline: str,
        facts: _Facts,
    ) -> None:
        session_ids = self._inventory_session_ids(facts)
        inspections = tuple(
            self._classify(GitSessionIdentity(self.main_root, value), baseline, facts)
            for value in session_ids
            if value != requested.session_id
        )
        if any(
            value.state is not GitSessionResourceState.READY for value in inspections
        ):
            raise _resource_error(
                GitSessionFailureCode.RECOVERY_REQUIRED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.RECOVERY_REQUIRED,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )

    def _effective_identity(self) -> GitEffectiveIdentity:
        author = _identity(
            self._required(("var", "GIT_AUTHOR_IDENT"), read_only=True).stdout
        )
        committer = _identity(
            self._required(("var", "GIT_COMMITTER_IDENT"), read_only=True).stdout
        )
        return GitEffectiveIdentity(author=author, committer=committer)

    def _seed(self, journal: GitSessionProvisioningJournal) -> str:
        empty_tree = _object_id(
            self._required(
                ("hash-object", "-t", "tree", "--stdin"),
                stdin=b"",
                read_only=True,
            ).stdout
        )
        payload = _seed_payload(journal, empty_tree)
        constructor = (
            hashlib.sha1 if len(journal.baseline_commit_id) == 40 else hashlib.sha256
        )
        header = f"commit {len(payload)}\0".encode("ascii")
        return constructor(header + payload).hexdigest()

    def _create_seed(
        self, expected: str, journal: GitSessionProvisioningJournal
    ) -> None:
        empty_tree = _object_id(
            self._required(
                ("hash-object", "-w", "-t", "tree", "--stdin"),
                stdin=b"",
            ).stdout
        )
        payload = _seed_payload(journal, empty_tree)
        observed = _object_id(
            self._required(
                ("hash-object", "-w", "-t", "commit", "--stdin"), stdin=payload
            ).stdout
        )
        if observed != expected or not self._verify_seed(
            expected,
            GitSessionIdentity(self.main_root, journal.session_id),
            journal,
        ):
            raise _resource_error(
                GitSessionFailureCode.MUTATION_FAILED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )

    def _cas_ref(self, ref: str, target: str) -> None:
        self._required(("update-ref", ref, target, _ZERO_BY_LENGTH[len(target)]))

    def _root_anchor(self, tip: str, expected: str) -> bool:
        roots = _lines(
            self._required(("rev-list", "--max-parents=0", tip), read_only=True).stdout
        )
        return roots == (expected,)

    def _root_commit(self, tip: str) -> str | None:
        roots = _lines(
            self._required(("rev-list", "--max-parents=0", tip), read_only=True).stdout
        )
        return roots[0] if len(roots) == 1 and _OBJECT_ID.fullmatch(roots[0]) else None

    def _verify_seed(
        self,
        seed: str,
        identity: GitSessionIdentity,
        journal: GitSessionProvisioningJournal | None,
    ) -> bool:
        try:
            tree = _object_id(
                self._required(
                    ("show", "-s", "--format=%T", seed), read_only=True
                ).stdout
            )
            empty = _object_id(
                self._required(
                    ("hash-object", "-t", "tree", "--stdin"), read_only=True
                ).stdout
            )
            payload = self._required(
                ("cat-file", "commit", seed), read_only=True
            ).stdout
            if tree != empty or not _valid_seed_payload(
                payload,
                identity=identity,
                empty_tree=empty,
                journal=journal,
            ):
                return False
            if journal is not None:
                expected = self._seed(journal)
                return expected == seed
            return True
        except (GitSessionResourceError, GitProcessFailure):
            return False

    def _verify_worktree_indirection(self, identity: GitSessionIdentity) -> bool:
        linked = _linked_worktree_common_git(identity.worktree_root)
        if linked is None:
            return False
        try:
            opened = os.fstat(self.common_descriptor)
        except OSError:
            return False
        common, common_identity = linked
        return common == self.common_git and common_identity == (
            opened.st_dev,
            opened.st_ino,
        )

    def _session_index_is_clean(self, identity: GitSessionIdentity) -> bool:
        """Require the session index to match HEAD without scanning untracked paths."""

        try:
            session_identity = _bind_directory(identity.worktree_root)
            result = GitProcessRunner(identity.worktree_root).run(
                ("diff-index", "--cached", "--quiet", "HEAD", "--"),
                expected_identity=session_identity,
                read_only=True,
            )
        except (GitProcessFailure, GitSessionResourceError):
            return False
        return result.returncode == 0 and not result.stdout

    def _required(
        self,
        arguments: tuple[str, ...],
        *,
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
    ) -> GitProcessResult:
        try:
            result = self.runner.run(
                arguments,
                expected_identity=self.identity,
                stdin=stdin,
                read_only=read_only,
                identity_environment=identity_environment,
            )
        except GitProcessFailure:
            if read_only:
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                ) from None
            raise _resource_error(
                GitSessionFailureCode.OUTCOME_UNKNOWN,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        if result.returncode != 0:
            if read_only:
                raise _resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            raise _resource_error(
                GitSessionFailureCode.MUTATION_FAILED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return result


class _Facts:
    __slots__ = ("branches", "internal_refs", "journals", "worktrees")

    def __init__(
        self,
        branches: dict[str, str],
        internal_refs: dict[str, str],
        worktrees: dict[Path, tuple[str, str]],
        journals: dict[str, GitSessionProvisioningJournal],
    ) -> None:
        self.branches = branches
        self.internal_refs = internal_refs
        self.worktrees = worktrees
        self.journals = journals


@contextmanager
def _repository_lock(root: Path, *, create: bool) -> Iterator[_Repository]:
    if _fcntl is None:
        raise _resource_error(
            GitSessionFailureCode.LOCK_UNAVAILABLE,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    lock_descriptor = -1
    try:
        with lock_git_repository(root) as lease:
            repository = _repository_from_lease(lease)
            try:
                directory = repository._open_provisioning_directory(create=create)
                if directory is not None:
                    try:
                        flags = (
                            os.O_RDWR
                            | (os.O_CREAT if create else 0)
                            | getattr(os, "O_CLOEXEC", 0)
                        )
                        flags |= getattr(os, "O_NOFOLLOW", 0)
                        try:
                            lock_descriptor = os.open(
                                GIT_SESSION_PROVISIONING_LOCK_NAME,
                                flags,
                                0o600,
                                dir_fd=directory,
                            )
                        except FileNotFoundError:
                            if create:
                                raise
                        if lock_descriptor >= 0:
                            opened = os.fstat(lock_descriptor)
                            if not stat.S_ISREG(opened.st_mode):
                                raise OSError
                            _fcntl.flock(lock_descriptor, _fcntl.LOCK_EX)
                            if create:
                                os.fsync(lock_descriptor)
                                os.fsync(directory)
                    finally:
                        os.close(directory)
                yield repository
            finally:
                if lock_descriptor >= 0:
                    try:
                        _fcntl.flock(lock_descriptor, _fcntl.LOCK_UN)
                    except OSError:
                        pass
                    os.close(lock_descriptor)
                    lock_descriptor = -1
    except GitSessionResourceError:
        raise
    except GitRepositoryLockFailure as exc:
        code = (
            GitSessionFailureCode.UNSAFE_ROOT
            if exc.code is GitRepositoryLockFailureCode.UNSAFE_ROOT
            else GitSessionFailureCode.LOCK_UNAVAILABLE
        )
        raise _resource_error(
            code,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    except OSError:
        raise _resource_error(
            GitSessionFailureCode.LOCK_UNAVAILABLE,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    finally:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)


def _repository_from_lease(lease: GitRepositoryLease) -> _Repository:
    try:
        if type(lease) is not GitRepositoryLease:
            raise TypeError
        lease.require_active()
        return _Repository(
            lease.main_root,
            lease.common_git,
            lease.common_descriptor,
            lease.root_identity,
        )
    except (GitRepositoryLockFailure, TypeError):
        raise _resource_error(
            GitSessionFailureCode.LOCK_UNAVAILABLE,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None


def _locked_request(
    lease: GitRepositoryLease,
    manifest: GitBaselineManifest,
    session_id: str,
) -> tuple[_Repository, GitBaselineManifest, GitSessionIdentity]:
    repository = _repository_from_lease(lease)
    main_root, expected_manifest, identity = _request(
        repository.main_root,
        manifest,
        session_id,
    )
    if main_root != repository.main_root:
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return repository, expected_manifest, identity


def _request(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
    session_id: str,
) -> tuple[Path, GitBaselineManifest, GitSessionIdentity]:
    supplied = _path(root)
    main_root, expected_manifest = _root_and_manifest(root, manifest)
    try:
        identity = GitSessionIdentity(main_root, session_id)
    except GitSessionResourceError:
        raise _resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        ) from None
    if supplied not in {main_root, identity.worktree_root}:
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return main_root, expected_manifest, identity


def _root_and_manifest(
    root: str | os.PathLike[str],
    manifest: GitBaselineManifest,
) -> tuple[Path, GitBaselineManifest]:
    path = _path(root)
    _bind_directory(path)
    main_root = _candidate_main_root(path)
    try:
        if type(manifest) is not GitBaselineManifest:
            raise TypeError
        supplied_entries = manifest.entries
        supplied_digest = manifest.manifest_digest
        if (
            type(supplied_entries) is not tuple
            or not supplied_entries
            or any(type(entry) is not GitManifestEntry for entry in supplied_entries)
        ):
            raise TypeError
        expected = GitBaselineManifest(
            tuple(
                GitManifestEntry(entry.path, entry.content_digest)
                for entry in supplied_entries
            )
        )
        if expected.manifest_digest != supplied_digest:
            raise TypeError
    except (AttributeError, GitOperationError, TypeError):
        raise _resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        ) from None
    return main_root, expected


def _path(root: str | os.PathLike[str]) -> Path:
    try:
        raw = os.fspath(root)
        path = Path(raw)
    except (TypeError, ValueError):
        raise _resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        ) from None
    if not path.is_absolute() or path != Path(os.path.normpath(os.fspath(path))):
        raise _resource_error(
            GitSessionFailureCode.INVALID_REQUEST,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.NONE,
        )
    return path


def _candidate_main_root(path: Path) -> Path:
    administrative = path / ".git"
    try:
        value = os.lstat(administrative)
    except OSError:
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
        return path
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    linked = _linked_worktree_common_git(path)
    if linked is None:
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    common, _identity = linked
    return common.parent


def _capture_git_session_generation(
    lease: GitRepositoryLease,
    session: GitSessionIdentity,
) -> tuple[GitSessionGenerationIdentity, tuple[int, ...]]:
    if type(lease) is not GitRepositoryLease or type(session) is not GitSessionIdentity:
        raise TypeError("Git session generation request is invalid")
    descriptors: list[int] = []
    try:
        lease.require_active()
        if lease.main_root != session.main_root:
            raise _generation_error()
        control_identity = lease.root_identity
        common_identity = lease.common_identity
        control_descriptor = _open_generation_directory(
            session.main_root,
            control_identity,
        )
        descriptors.append(control_descriptor)
        common_descriptor = _duplicate_generation_directory(
            lease.common_descriptor,
            common_identity,
        )
        descriptors.append(common_descriptor)

        worktree_identity = _bind_directory(session.worktree_root)
        worktree_descriptor = _open_generation_directory(
            session.worktree_root,
            worktree_identity,
        )
        descriptors.append(worktree_descriptor)
        git_descriptor, git_payload, git_facts = _open_generation_file(
            worktree_descriptor,
            ".git",
        )
        descriptors.append(git_descriptor)
        administrative_root = _administrative_path(git_payload)
        if (
            administrative_root.parent != lease.common_git / "worktrees"
            or administrative_root.name in {"", ".", ".."}
        ):
            raise _generation_error()
        administrative_identity = _bind_directory(administrative_root)
        administrative_descriptor = _open_generation_directory(
            administrative_root,
            administrative_identity,
        )
        descriptors.append(administrative_descriptor)
        common_file_descriptor, common_payload, common_facts = (
            _open_generation_file(administrative_descriptor, "commondir")
        )
        descriptors.append(common_file_descriptor)
        common_relative = _common_git_relative_path(common_payload)
        rebound_common = Path(
            os.path.normpath(os.fspath(administrative_root / common_relative))
        )
        if rebound_common != lease.common_git:
            raise _generation_error()

        lease.require_active()
        if (
            _bind_directory(session.main_root) != control_identity
            or _bind_directory(lease.common_git) != common_identity
            or _bind_directory(administrative_root) != administrative_identity
            or _bind_directory(session.worktree_root) != worktree_identity
        ):
            raise _generation_error()
        _require_named_generation_file(
            worktree_descriptor,
            ".git",
            git_descriptor,
            git_facts,
        )
        _require_named_generation_file(
            administrative_descriptor,
            "commondir",
            common_file_descriptor,
            common_facts,
        )
        generation = GitSessionGenerationIdentity(
            session=session,
            administrative_root=administrative_root,
            control_root_identity=control_identity,
            common_git_identity=common_identity,
            administrative_identity=administrative_identity,
            worktree_identity=worktree_identity,
            git_indirection_facts=git_facts,
            common_indirection_facts=common_facts,
            git_indirection_sha256=hashlib.sha256(git_payload).hexdigest(),
            common_indirection_sha256=hashlib.sha256(common_payload).hexdigest(),
        )
        ordered = (
            control_descriptor,
            common_descriptor,
            administrative_descriptor,
            worktree_descriptor,
            git_descriptor,
            common_file_descriptor,
        )
        descriptors.clear()
        return generation, ordered
    except (GitRepositoryLockFailure, GitSessionResourceError):
        _close_generation_descriptors(tuple(descriptors))
        raise
    except (OSError, UnicodeError, ValueError):
        _close_generation_descriptors(tuple(descriptors))
        raise _generation_error() from None
    except BaseException:
        _close_generation_descriptors(tuple(descriptors))
        raise


def _open_generation_directory(path: Path, identity: tuple[int, int]) -> int:
    descriptor = _open_bound_directory(path, identity)
    try:
        os.set_inheritable(descriptor, False)
        if os.get_inheritable(descriptor):
            raise OSError
        return descriptor
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _duplicate_generation_directory(
    descriptor: int,
    identity: tuple[int, int],
) -> int:
    duplicate = -1
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        observed = os.fstat(duplicate)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or (observed.st_dev, observed.st_ino) != identity
            or os.get_inheritable(duplicate)
        ):
            raise OSError
        return duplicate
    except OSError:
        if duplicate >= 0:
            try:
                os.close(duplicate)
            except OSError:
                pass
        raise _generation_error() from None


def _open_generation_file(
    parent: int,
    name: str,
) -> tuple[int, bytes, _GenerationFileFacts]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        facts = _generation_file_facts(opened)
        if (
            facts != _generation_file_facts(named)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > _MAX_ADMIN_FILE_BYTES
            or os.get_inheritable(descriptor)
        ):
            raise OSError
        content = bytearray()
        while len(content) <= _MAX_ADMIN_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(8192, _MAX_ADMIN_FILE_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > _MAX_ADMIN_FILE_BYTES:
            raise OSError
        _require_named_generation_file(parent, name, descriptor, facts)
        if len(content) != opened.st_size:
            raise OSError
        return descriptor, bytes(content), facts
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _generation_error() from None


def _require_named_generation_file(
    parent: int,
    name: str,
    descriptor: int,
    facts: _GenerationFileFacts,
) -> None:
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        _generation_file_facts(opened) != facts
        or _generation_file_facts(named) != facts
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or os.get_inheritable(descriptor)
    ):
        raise OSError


def _generation_file_facts(value: os.stat_result) -> _GenerationFileFacts:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _valid_generation_directory_identity(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 2
        and all(type(part) is int and part > 0 for part in value)
    )


def _valid_generation_file_facts(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 7
        and all(type(part) is int for part in value)
        and value[0] > 0
        and value[1] > 0
        and stat.S_ISREG(value[2])
        and value[3] == 1
        and 0 <= value[4] <= _MAX_ADMIN_FILE_BYTES
    )


def _require_retained_generation_descriptors(
    identity: GitSessionGenerationIdentity,
    descriptors: tuple[int, ...],
) -> None:
    if len(descriptors) != _GENERATION_DESCRIPTOR_COUNT:
        raise _generation_error()
    directory_expectations = (
        (descriptors[0], identity.control_root_identity),
        (descriptors[1], identity.common_git_identity),
        (descriptors[2], identity.administrative_identity),
        (descriptors[3], identity.worktree_identity),
    )
    try:
        if any(
            not stat.S_ISDIR(observed.st_mode)
            or (observed.st_dev, observed.st_ino) != expected
            or os.get_inheritable(descriptor)
            for descriptor, expected in directory_expectations
            for observed in (os.fstat(descriptor),)
        ):
            raise OSError
        if (
            _generation_file_facts(os.fstat(descriptors[4]))
            != identity.git_indirection_facts
            or _generation_file_facts(os.fstat(descriptors[5]))
            != identity.common_indirection_facts
            or os.get_inheritable(descriptors[4])
            or os.get_inheritable(descriptors[5])
        ):
            raise OSError
    except OSError:
        raise _generation_error() from None


def _close_generation_descriptors(descriptors: tuple[int, ...]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _generation_error() -> GitSessionResourceError:
    return _resource_error(
        GitSessionFailureCode.UNSAFE_ROOT,
        GitSessionOperationKind.INSPECT,
        GitSessionResourceState.RECOVERY_REQUIRED,
        GitSessionRecoveryAction.OPERATOR_RECOVERY,
    )


def _linked_worktree_common_git(
    root: Path,
) -> tuple[Path, tuple[int, int]] | None:
    root_descriptor = -1
    administrative_descriptor = -1
    try:
        root_identity = _bind_directory(root)
        root_descriptor = _open_bound_directory(root, root_identity)
        git_payload = _read_file(root_descriptor, ".git")
        administrative = _administrative_path(git_payload)
        administrative_identity = _bind_directory(administrative)
        administrative_descriptor = _open_bound_directory(
            administrative,
            administrative_identity,
        )
        common_payload = _read_file(administrative_descriptor, "commondir")
        common_relative = _common_git_relative_path(common_payload)
        common = Path(os.path.normpath(os.fspath(administrative / common_relative)))
        if (
            common.name != ".git"
            or administrative.parent != common / "worktrees"
            or administrative.name in {"", ".", ".."}
        ):
            raise ValueError
        common_identity = _bind_directory(common)
        return common, common_identity
    except (GitSessionResourceError, OSError, UnicodeError, ValueError):
        return None
    finally:
        if administrative_descriptor >= 0:
            os.close(administrative_descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _administrative_path(payload: bytes) -> Path:
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


def _common_git_relative_path(payload: bytes) -> Path:
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


def _bind_directory(path: Path) -> tuple[int, int]:
    try:
        value = os.lstat(path)
        canonical = Path(os.path.realpath(path))
    except OSError:
        raise _resource_error(
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
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return value.st_dev, value.st_ino


def _open_bound_directory(path: Path, identity: tuple[int, int]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
    except OSError:
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    if (opened.st_dev, opened.st_ino) != identity or not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise _resource_error(
            GitSessionFailureCode.UNSAFE_ROOT,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return descriptor


def _open_directory(parent: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise OSError
    return descriptor


def _read_file(parent: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_ADMIN_FILE_BYTES:
            raise OSError
        result = bytearray()
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            result.extend(chunk)
            if len(result) > _MAX_ADMIN_FILE_BYTES:
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


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError
        offset += written


def _path_lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _object_id(value: bytes | str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii").strip()
        except UnicodeError:
            value = ""
    if type(value) is not str or _OBJECT_ID.fullmatch(value) is None:
        raise _resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return value


def _utf8(value: bytes) -> str:
    try:
        result = value.decode("utf-8", errors="strict")
    except UnicodeError:
        raise _resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    if not result or "\x00" in result or "\r" in result or "\n" in result:
        raise _resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    return result


def _line(value: bytes) -> str:
    return _utf8(value.strip())


def _identity(value: bytes) -> GitIdentity:
    try:
        text = value.decode("utf-8", errors="strict").strip()
        prefix, _timestamp, _zone = text.rsplit(" ", 2)
        name, email = prefix.rsplit(" <", 1)
        return GitIdentity(name=name, email=email.removesuffix(">"))
    except (UnicodeError, ValueError, GitOperationError):
        raise _resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.PROVISION,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None


def _seed_payload(journal: GitSessionProvisioningJournal, empty_tree: str) -> bytes:
    timestamp = journal.seed_timestamp_seconds
    author = journal.effective_identity.author
    committer = journal.effective_identity.committer
    return (
        f"tree {empty_tree}\n"
        f"author {author.name} <{author.email}> {timestamp} +0000\n"
        f"committer {committer.name} <{committer.email}> {timestamp} +0000\n"
        "\n"
        f"{GIT_SESSION_SEED_MESSAGE_PREFIX}{journal.session_id}\n"
    ).encode("utf-8", errors="strict")


def _valid_seed_payload(
    payload: bytes,
    *,
    identity: GitSessionIdentity,
    empty_tree: str,
    journal: GitSessionProvisioningJournal | None,
) -> bool:
    if journal is not None:
        return payload == _seed_payload(journal, empty_tree)
    if len(payload) > _MAX_ADMIN_FILE_BYTES or b"\x00" in payload or b"\r" in payload:
        return False
    parts = payload.split(b"\n")
    if (
        len(parts) != 6
        or parts[0] != f"tree {empty_tree}".encode("ascii")
        or parts[3] != b""
        or parts[4]
        != f"{GIT_SESSION_SEED_MESSAGE_PREFIX}{identity.session_id}".encode()
        or parts[5] != b""
    ):
        return False
    author = _seed_identity_line(parts[1], b"author ")
    committer = _seed_identity_line(parts[2], b"committer ")
    return author is not None and committer is not None and author[1] == committer[1]


def _seed_identity_line(value: bytes, prefix: bytes) -> tuple[GitIdentity, int] | None:
    if not value.startswith(prefix):
        return None
    try:
        identity, raw_time, zone = value[len(prefix) :].rsplit(b" ", 2)
        if zone != b"+0000":
            return None
        raw_name, raw_email = identity.rsplit(b" <", 1)
        if not raw_email.endswith(b">"):
            return None
        name = raw_name.decode("utf-8", errors="strict")
        email = raw_email[:-1].decode("utf-8", errors="strict")
        timestamp_text = raw_time.decode("ascii")
        timestamp = int(timestamp_text)
        if str(timestamp) != timestamp_text or not 0 <= timestamp <= 2**63 - 1:
            return None
        return GitIdentity(name=name, email=email), timestamp
    except (UnicodeError, ValueError, GitOperationError):
        return None


def _lines(value: bytes) -> tuple[str, ...]:
    try:
        return tuple(line.decode("ascii") for line in value.splitlines())
    except UnicodeError:
        raise _resource_error(
            GitSessionFailureCode.RECOVERY_REQUIRED,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None


def _absent(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.ABSENT,
        recovery_action=GitSessionRecoveryAction.CREATE,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def _provisioning(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.PROVISIONING,
        recovery_action=GitSessionRecoveryAction.RETRY_CREATE,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def _recovery(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.RECOVERY_REQUIRED,
        recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def _unknown(
    identity: GitSessionIdentity, baseline: str
) -> GitSessionResourceInspection:
    return GitSessionResourceInspection(
        identity=identity,
        state=GitSessionResourceState.OUTCOME_UNKNOWN,
        recovery_action=GitSessionRecoveryAction.OPERATOR_RECOVERY,
        baseline_commit_id=baseline,
        seed_commit_id=None,
        initial_branch_tip=None,
        current_branch_tip=None,
        initial_internal_tip=None,
        current_internal_tip=None,
    )


def _resource_error(
    code: GitSessionFailureCode,
    operation: GitSessionOperationKind,
    state: GitSessionResourceState,
    action: GitSessionRecoveryAction,
) -> GitSessionResourceError:
    error = GitSessionResourceError(
        code,
        operation=operation,
        state=state,
        recovery_action=action,
    )
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


def _clear_resource_error(error: GitSessionResourceError) -> GitSessionResourceError:
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


def _status_matches_allowed_worktree_changes(
    content: bytes,
    allowed_paths: tuple[str, ...],
) -> bool:
    """Accept only unstaged modifications of exact caller-validated paths."""

    if type(allowed_paths) is not tuple or any(
        not isinstance(path, str) or not path for path in allowed_paths
    ):
        return False
    allowed = set(allowed_paths)
    if not allowed <= _WORKSPACE_MUTABLE_CONFIGURATION_PATHS:
        return False
    observed: set[str] = set()
    for record in content.split(b"\x00"):
        if not record:
            continue
        if not record.startswith(b" M "):
            return False
        try:
            path = record[3:].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        if path not in allowed or path in observed:
            return False
        observed.add(path)
    return True


__all__ = [
    "GitSessionGenerationIdentity",
    "GitSessionGenerationPin",
    "acquire_git_session_generation_pin",
    "capture_locked_git_session_generation",
    "inspect_git_session_resources",
    "inspect_locked_git_session_resources",
    "list_git_session_resources",
    "provision_git_session_resources",
    "require_ready_git_session_resources",
    "require_ready_locked_git_session_resources",
]
