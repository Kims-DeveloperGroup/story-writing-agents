"""Identity-bound repository serialization for package-internal Git owners."""

from __future__ import annotations

import os
import stat
import threading
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Iterator

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on unsupported platforms
    _fcntl = None  # type: ignore[assignment]


_THREAD_STATE = threading.local()


def current_thread_token() -> object:
    """Return one process-bound token for the current Python thread state."""

    pid = os.getpid()
    token = getattr(_THREAD_STATE, "token", None)
    if getattr(_THREAD_STATE, "pid", None) != pid or token is None:
        _THREAD_STATE.pid = pid
        token = object()
        _THREAD_STATE.token = token
    return token


class GitRepositoryLockFailureCode(StrEnum):
    """Closed failure facts emitted by the shared repository-lock owner."""

    UNSAFE_ROOT = "unsafe_root"
    LOCK_UNAVAILABLE = "lock_unavailable"


class GitRepositoryLockFailure(RuntimeError):
    """Content-free failure while binding or locking one common Git directory."""

    __slots__ = ("code",)

    def __init__(self, code: GitRepositoryLockFailureCode) -> None:
        if type(code) is not GitRepositoryLockFailureCode:
            raise TypeError("repository lock failure code is invalid")
        self.code = code
        super().__init__(code.value)


class GitRepositoryLease:
    """One live exclusive lease over an identity-bound common Git directory."""

    __slots__ = (
        "_active",
        "_common_descriptor",
        "_common_git",
        "_common_identity",
        "_main_root",
        "_pid",
        "_root_identity",
        "_thread_token",
    )

    def __init__(
        self,
        *,
        main_root: Path,
        common_git: Path,
        root_identity: tuple[int, int],
        common_identity: tuple[int, int],
        common_descriptor: int,
    ) -> None:
        self._main_root = main_root
        self._common_git = common_git
        self._root_identity = root_identity
        self._common_identity = common_identity
        self._common_descriptor = common_descriptor
        self._pid = os.getpid()
        self._thread_token = current_thread_token()
        self._active = True

    @property
    def main_root(self) -> Path:
        self.require_active()
        return self._main_root

    @property
    def common_git(self) -> Path:
        self.require_active()
        return self._common_git

    @property
    def root_identity(self) -> tuple[int, int]:
        self.require_active()
        return self._root_identity

    @property
    def common_identity(self) -> tuple[int, int]:
        self.require_active()
        return self._common_identity

    @property
    def common_descriptor(self) -> int:
        self.require_active()
        return self._common_descriptor

    def require_active(self) -> None:
        """Revalidate both named directories and the live leased descriptor."""

        if (
            self._pid != os.getpid()
            or not self._active
            or current_thread_token() is not self._thread_token
        ):
            raise _failure(GitRepositoryLockFailureCode.LOCK_UNAVAILABLE)
        failure: GitRepositoryLockFailure | None = None
        try:
            root = os.lstat(self._main_root)
            common = os.lstat(self._common_git)
            opened = os.fstat(self._common_descriptor)
            root_real = Path(os.path.realpath(self._main_root))
            common_real = Path(os.path.realpath(self._common_git))
        except OSError:
            failure = _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)
        if failure is not None:
            raise failure
        if (
            not stat.S_ISDIR(root.st_mode)
            or stat.S_ISLNK(root.st_mode)
            or not stat.S_ISDIR(common.st_mode)
            or stat.S_ISLNK(common.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or root_real != self._main_root
            or common_real != self._common_git
            or (root.st_dev, root.st_ino) != self._root_identity
            or (common.st_dev, common.st_ino) != self._common_identity
            or (opened.st_dev, opened.st_ino) != self._common_identity
        ):
            raise _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)

    def _close(self) -> None:
        self._active = False


@contextmanager
def lock_git_repository(main_root: Path) -> Iterator[GitRepositoryLease]:
    """Exclusively lock one verified standalone repository common directory."""

    if _fcntl is None:
        raise _failure(GitRepositoryLockFailureCode.LOCK_UNAVAILABLE)
    root = _lexical_absolute(main_root)
    root_identity = _bind_directory(root)
    common_git = root / ".git"
    common_identity = _bind_directory(common_git)
    common_descriptor = _open_bound_directory(common_git, common_identity)
    creator_pid = os.getpid()
    try:
        _fcntl.flock(common_descriptor, _fcntl.LOCK_EX)
    except OSError:
        failure = _failure(GitRepositoryLockFailureCode.LOCK_UNAVAILABLE)
        _close_failed_repository_lock_descriptor(common_descriptor, failure)
        raise failure
    except BaseException as failure:
        _close_failed_repository_lock_descriptor(common_descriptor, failure)
        raise

    lease: GitRepositoryLease | None = None
    body_failure: BaseException | None = None
    try:
        lease = GitRepositoryLease(
            main_root=root,
            common_git=common_git,
            root_identity=root_identity,
            common_identity=common_identity,
            common_descriptor=common_descriptor,
        )
        lease.require_active()
        yield lease
        lease.require_active()
    except BaseException as failure:
        body_failure = failure
        raise
    finally:
        if lease is not None:
            lease._close()
        _close_repository_lock_descriptor(
            common_descriptor,
            failure=body_failure,
            unlock=creator_pid == os.getpid(),
        )


def _close_failed_repository_lock_descriptor(
    descriptor: int,
    failure: BaseException,
) -> None:
    """Exhaust failed flock cleanup without replacing its primary failure."""

    _close_repository_lock_descriptor(descriptor, failure=failure, unlock=True)


def _close_repository_lock_descriptor(
    descriptor: int,
    *,
    failure: BaseException | None,
    unlock: bool,
) -> None:
    assert _fcntl is not None
    cleanup_failure: BaseException | None = None
    cleanups = [lambda: os.close(descriptor)]
    if unlock:
        cleanups.insert(0, lambda: _fcntl.flock(descriptor, _fcntl.LOCK_UN))
    for cleanup in cleanups:
        try:
            cleanup()
        except BaseException as exc:
            primary = failure if failure is not None else cleanup_failure
            if primary is None:
                if not isinstance(exc, OSError):
                    cleanup_failure = exc
                continue
            try:
                primary.add_note(
                    "Git repository lock cleanup failed: "
                    f"{type(exc).__name__}"
                )
            except BaseException:
                pass
    if cleanup_failure is not None:
        raise cleanup_failure


def _lexical_absolute(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)
    normalized = Path(os.path.normpath(os.fspath(value)))
    if normalized != value:
        raise _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)
    return value


def _bind_directory(value: Path) -> tuple[int, int]:
    failure: GitRepositoryLockFailure | None = None
    try:
        observed = os.lstat(value)
        canonical = Path(os.path.realpath(value))
    except OSError:
        failure = _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)
    if failure is not None:
        raise failure
    identity = (observed.st_dev, observed.st_ino)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or stat.S_ISLNK(observed.st_mode)
        or canonical != value
        or any(part <= 0 for part in identity)
    ):
        raise _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)
    return identity


def _open_bound_directory(value: Path, identity: tuple[int, int]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    failure: GitRepositoryLockFailure | None = None
    try:
        descriptor = os.open(value, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (
                opened.st_dev,
                opened.st_ino,
            )
            != identity
        ):
            raise OSError
        return descriptor
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        failure = _failure(GitRepositoryLockFailureCode.UNSAFE_ROOT)
    if failure is not None:
        raise failure
    raise AssertionError("bound directory open completed without a descriptor")


def _failure(code: GitRepositoryLockFailureCode) -> GitRepositoryLockFailure:
    error = GitRepositoryLockFailure(code)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    return error


__all__ = [
    "GitRepositoryLease",
    "GitRepositoryLockFailure",
    "GitRepositoryLockFailureCode",
    "lock_git_repository",
]
