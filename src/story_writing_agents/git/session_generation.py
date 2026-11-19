"""Creator-bound Git session generation identities, retained pins and index proof."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from .process import GitProcessFailure, GitProcessRunner
from .repository_lock import (
    GitRepositoryLease,
    GitRepositoryLockFailure,
    current_thread_token,
    lock_git_repository,
)
from .session_contracts import (
    GitSessionIdentity,
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from .session_resource_files import (
    GenerationFileFacts,
    add_generation_descriptor_cleanup_note,
    administrative_path,
    bind_directory,
    close_generation_descriptor,
    common_git_relative_path,
    generation_file_facts,
    open_bound_directory,
    require_named_generation_file,
)
from .session_resource_values import MAX_SESSION_ADMIN_FILE_BYTES, generation_error


_GENERATION_DESCRIPTOR_COUNT = 6


@dataclass(frozen=True, slots=True)
class GitSessionGenerationIdentity:
    """Content-free physical identity of one validated linked worktree."""

    session: GitSessionIdentity = dataclass_field(repr=False)
    administrative_root: Path = dataclass_field(repr=False)
    control_root_identity: tuple[int, int]
    common_git_identity: tuple[int, int]
    administrative_identity: tuple[int, int]
    worktree_identity: tuple[int, int]
    git_indirection_facts: GenerationFileFacts = dataclass_field(repr=False)
    common_indirection_facts: GenerationFileFacts = dataclass_field(repr=False)
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
            raise generation_error()


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
            raise generation_error()
        identity = self._identity
        with lock_git_repository(identity.session.main_root) as lease:
            current, descriptors = _capture_git_session_generation(
                lease,
                identity.session,
            )
            failure: BaseException | None = None
            try:
                if current != identity:
                    raise generation_error()
            except BaseException as exc:
                failure = exc
                raise
            finally:
                _close_generation_descriptors(descriptors, failure=failure)
            with self._lock:
                retained = self._descriptors
                if not retained:
                    raise generation_error()
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
    failure: BaseException | None = None
    try:
        if identity.worktree_identity != expected_worktree_identity:
            raise generation_error()
        return identity
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _close_generation_descriptors(descriptors, failure=failure)


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
                raise generation_error()
            pin = GitSessionGenerationPin(identity, descriptors)
            descriptors = ()
        return pin
    except BaseException as failure:
        _close_generation_descriptors(descriptors, failure=failure)
        if pin is not None:
            try:
                pin.close()
            except BaseException as cleanup_failure:
                add_generation_descriptor_cleanup_note(
                    failure,
                    cleanup_failure,
                )
        raise


def acquire_locked_git_session_generation_pin(
    lease: GitRepositoryLease,
    resources: GitSessionResourceInspection,
) -> GitSessionGenerationPin:
    """Acquire one opaque generation pin through an existing live lease."""

    if (
        type(lease) is not GitRepositoryLease
        or type(resources) is not GitSessionResourceInspection
        or resources.state is not GitSessionResourceState.READY
    ):
        raise TypeError("ready locked Git session resources are required")
    pin: GitSessionGenerationPin | None = None
    descriptors: tuple[int, ...] = ()
    try:
        lease.require_active()
        identity, descriptors = _capture_git_session_generation(
            lease,
            resources.identity,
        )
        pin = GitSessionGenerationPin(identity, descriptors)
        descriptors = ()
        lease.require_active()
        return pin
    except BaseException as failure:
        _close_generation_descriptors(descriptors, failure=failure)
        if pin is not None:
            try:
                pin.close()
            except BaseException as cleanup_failure:
                add_generation_descriptor_cleanup_note(failure, cleanup_failure)
        raise


def require_locked_git_session_untracked_prefix(
    lease: GitRepositoryLease,
    resources: GitSessionResourceInspection,
    expected_generation: GitSessionGenerationIdentity,
    *,
    prefix: str,
    verify_exclusion: bool = False,
) -> None:
    """Require an untracked prefix and, when requested, its effective exclusion."""

    if (
        type(lease) is not GitRepositoryLease
        or type(resources) is not GitSessionResourceInspection
        or resources.state is not GitSessionResourceState.READY
        or type(expected_generation) is not GitSessionGenerationIdentity
        or resources.identity != expected_generation.session
        or prefix != ".agents"
        or type(verify_exclusion) is not bool
    ):
        raise TypeError("ready locked Git session resources are required")
    expected_generation._require_creator()
    before_descriptors: tuple[int, ...] = ()
    after_descriptors: tuple[int, ...] = ()
    before_index = -1
    after_index = -1
    failure: BaseException | None = None
    try:
        lease.require_active()
        if lease.main_root != resources.identity.main_root:
            raise generation_error()
        observed_generation, before_descriptors = _capture_git_session_generation(
            lease,
            resources.identity,
        )
        if observed_generation != expected_generation:
            raise generation_error()
        before_index, index_facts = _open_session_index(
            before_descriptors[2]
        )
        _require_retained_generation_descriptors(
            expected_generation,
            before_descriptors,
        )
        lease.require_active()
        result = GitProcessRunner(resources.identity.worktree_root).run(
            (
                "ls-files",
                "--cached",
                "-z",
                "--",
                f":(top,literal,icase){prefix}",
            ),
            expected_identity=expected_generation.worktree_identity,
            read_only=True,
        )
        if result.returncode != 0 or result.stdout:
            raise generation_error()
        if verify_exclusion:
            ignored = GitProcessRunner(resources.identity.worktree_root).run(
                ("check-ignore", "--quiet", "--no-index", "--", ".agents/"),
                expected_identity=expected_generation.worktree_identity,
                read_only=True,
            )
            if ignored.returncode != 0 or ignored.stdout:
                raise generation_error()
        _require_session_index(
            before_descriptors[2],
            before_index,
            index_facts,
        )
        _require_retained_generation_descriptors(
            expected_generation,
            before_descriptors,
        )
        lease.require_active()
        observed_after, after_descriptors = _capture_git_session_generation(
            lease,
            resources.identity,
        )
        if observed_after != expected_generation:
            raise generation_error()
        after_index, after_index_facts = _open_session_index(
            after_descriptors[2]
        )
        if after_index_facts != index_facts:
            raise generation_error()
        _require_session_index(
            after_descriptors[2],
            after_index,
            index_facts,
        )
        lease.require_active()
    except (GitRepositoryLockFailure, GitSessionResourceError) as exc:
        failure = exc
        raise
    except (GitProcessFailure, OSError, UnicodeError, ValueError):
        failure = generation_error()
        raise failure from None
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            _close_generation_descriptors(
                (
                    *after_descriptors,
                    *((after_index,) if after_index >= 0 else ()),
                ),
                failure=failure,
            )
        finally:
            _close_generation_descriptors(
                (
                    *before_descriptors,
                    *((before_index,) if before_index >= 0 else ()),
                ),
                failure=failure,
            )


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
            raise generation_error()
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

        worktree_identity = bind_directory(session.worktree_root)
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
        administrative_root = administrative_path(git_payload)
        if (
            administrative_root.parent != lease.common_git / "worktrees"
            or administrative_root.name in {"", ".", ".."}
        ):
            raise generation_error()
        administrative_identity = bind_directory(administrative_root)
        administrative_descriptor = _open_generation_directory(
            administrative_root,
            administrative_identity,
        )
        descriptors.append(administrative_descriptor)
        common_file_descriptor, common_payload, common_facts = (
            _open_generation_file(administrative_descriptor, "commondir")
        )
        descriptors.append(common_file_descriptor)
        common_relative = common_git_relative_path(common_payload)
        rebound_common = Path(
            os.path.normpath(os.fspath(administrative_root / common_relative))
        )
        if rebound_common != lease.common_git:
            raise generation_error()

        lease.require_active()
        if (
            bind_directory(session.main_root) != control_identity
            or bind_directory(lease.common_git) != common_identity
            or bind_directory(administrative_root) != administrative_identity
            or bind_directory(session.worktree_root) != worktree_identity
        ):
            raise generation_error()
        require_named_generation_file(
            worktree_descriptor,
            ".git",
            git_descriptor,
            git_facts,
        )
        require_named_generation_file(
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
    except (GitRepositoryLockFailure, GitSessionResourceError) as failure:
        _close_generation_descriptors(tuple(descriptors), failure=failure)
        raise
    except (OSError, UnicodeError, ValueError):
        failure = generation_error()
        _close_generation_descriptors(tuple(descriptors), failure=failure)
        raise failure from None
    except BaseException as failure:
        _close_generation_descriptors(tuple(descriptors), failure=failure)
        raise


def _open_generation_directory(path: Path, identity: tuple[int, int]) -> int:
    descriptor = open_bound_directory(path, identity)
    try:
        os.set_inheritable(descriptor, False)
        if os.get_inheritable(descriptor):
            raise OSError
        return descriptor
    except OSError:
        failure = generation_error()
        close_generation_descriptor(descriptor, failure)
        raise failure from None
    except BaseException as failure:
        close_generation_descriptor(descriptor, failure)
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
        failure = generation_error()
        close_generation_descriptor(duplicate, failure)
        raise failure from None
    except BaseException as failure:
        close_generation_descriptor(duplicate, failure)
        raise


def _open_generation_file(
    parent: int,
    name: str,
) -> tuple[int, bytes, GenerationFileFacts]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        facts = generation_file_facts(opened)
        if (
            facts != generation_file_facts(named)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_SESSION_ADMIN_FILE_BYTES
            or os.get_inheritable(descriptor)
        ):
            raise OSError
        content = bytearray()
        while len(content) <= MAX_SESSION_ADMIN_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(8192, MAX_SESSION_ADMIN_FILE_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > MAX_SESSION_ADMIN_FILE_BYTES:
            raise OSError
        require_named_generation_file(parent, name, descriptor, facts)
        if len(content) != opened.st_size:
            raise OSError
        return descriptor, bytes(content), facts
    except OSError:
        failure = generation_error()
        close_generation_descriptor(descriptor, failure)
        raise failure from None
    except BaseException as failure:
        close_generation_descriptor(descriptor, failure)
        raise


def _open_session_index(
    administrative_descriptor: int,
) -> tuple[int, GenerationFileFacts]:
    """Bind the linked worktree index through its retained administrative root."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if (
        type(no_follow) is not int
        or no_follow <= 0
        or type(nonblocking) is not int
        or nonblocking <= 0
    ):
        raise OSError
    flags = os.O_RDONLY | no_follow | nonblocking
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("index", flags, dir_fd=administrative_descriptor)
        os.set_inheritable(descriptor, False)
        opened = os.fstat(descriptor)
        facts = generation_file_facts(opened)
        _require_session_index(
            administrative_descriptor,
            descriptor,
            facts,
        )
        return descriptor, facts
    except BaseException as failure:
        close_generation_descriptor(descriptor, failure)
        raise


def _require_session_index(
    administrative_descriptor: int,
    descriptor: int,
    facts: GenerationFileFacts,
) -> None:
    """Revalidate one retained linked-worktree index descriptor and pathname."""

    opened = os.fstat(descriptor)
    named = os.stat(
        "index",
        dir_fd=administrative_descriptor,
        follow_symlinks=False,
    )
    if (
        generation_file_facts(opened) != facts
        or generation_file_facts(named) != facts
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_size < 0
        or os.get_inheritable(descriptor)
    ):
        raise OSError


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
        and 0 <= value[4] <= MAX_SESSION_ADMIN_FILE_BYTES
    )


def _require_retained_generation_descriptors(
    identity: GitSessionGenerationIdentity,
    descriptors: tuple[int, ...],
) -> None:
    if len(descriptors) != _GENERATION_DESCRIPTOR_COUNT:
        raise generation_error()
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
            generation_file_facts(os.fstat(descriptors[4]))
            != identity.git_indirection_facts
            or generation_file_facts(os.fstat(descriptors[5]))
            != identity.common_indirection_facts
            or os.get_inheritable(descriptors[4])
            or os.get_inheritable(descriptors[5])
        ):
            raise OSError
    except OSError:
        raise generation_error() from None


def _close_generation_descriptors(
    descriptors: tuple[int, ...],
    *,
    failure: BaseException | None = None,
) -> None:
    cleanup_failure: BaseException | None = None
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except BaseException as exc:
            primary = failure if failure is not None else cleanup_failure
            if primary is not None:
                add_generation_descriptor_cleanup_note(primary, exc)
            elif not isinstance(exc, OSError):
                cleanup_failure = exc
    if cleanup_failure is not None:
        raise cleanup_failure
