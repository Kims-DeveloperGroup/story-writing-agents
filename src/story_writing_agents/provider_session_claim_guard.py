"""Lock and descriptor lifetime for provider session access claims."""

from __future__ import annotations

import errno
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import BackendProtocolError, WorkspaceFormatError, WorkspaceSecurityError
from .workspace import Workspace


if os.name == "posix":
    try:
        import fcntl as _fcntl
    except ImportError:
        _fcntl = None
else:
    _fcntl = None

if os.name == "nt":
    try:
        import msvcrt as _msvcrt
    except ImportError:
        _msvcrt = None
else:
    _msvcrt = None


_WINDOWS_LOCK_RETRY_SECONDS = 0.05


@contextmanager
def access_claim_guard(
    workspace: Workspace, session_directory: str, guard_relative: str,
) -> Iterator[None]:
    """Serialize one complete provider-session access claim transaction."""

    descriptor = _open_access_claim_guard(workspace, session_directory, guard_relative)
    locked = False
    active_error: BaseException | None = None
    try:
        try:
            _lock_access_claim_guard(descriptor)
        except BackendProtocolError:
            raise
        except Exception as exc:
            raise BackendProtocolError(
                "provider session access claim guard could not be locked: "
                f"{type(exc).__name__}"
            ) from None
        locked = True
        _verify_access_claim_guard(
            descriptor, workspace, session_directory, guard_relative,
        )
        yield
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        unlock_error: BaseException | None = None
        if locked:
            try:
                _unlock_access_claim_guard(descriptor)
            except BaseException as exc:
                unlock_error = exc
        close_error: BaseException | None = None
        try:
            os.close(descriptor)
        except BaseException as exc:
            close_error = exc
        cleanup_error: BaseException | None = None
        if unlock_error is not None:
            cleanup_error = (
                unlock_error
                if not isinstance(unlock_error, Exception)
                else BackendProtocolError(
                    "provider session access claim guard could not be unlocked: "
                    f"{type(unlock_error).__name__}"
                )
            )
        if close_error is not None:
            translated_close_error = (
                close_error
                if not isinstance(close_error, Exception)
                else BackendProtocolError(
                    "provider session access claim guard could not be closed: "
                    f"{type(close_error).__name__}"
                )
            )
            if cleanup_error is None:
                cleanup_error = translated_close_error
            else:
                cleanup_error.add_note(str(translated_close_error))
        # A normal body exit has committed the claim. Never turn that into
        # a false claim failure: successful close releases the advisory lock,
        # while an ambiguous close is not retried and the token returns so the
        # caller can track and release access. Exceptional work keeps its
        # original error and receives only bounded cleanup notes.
        if cleanup_error is not None and active_error is not None:
            active_error.add_note(str(cleanup_error))


def _open_access_claim_guard(
    workspace: Workspace, session_directory: str, guard_relative: str,
) -> int:
    """Open, secure, verify, and platform-initialize the claim guard.

    The caller owns the returned descriptor. Setup failures close it before
    propagating a bounded backend or workspace error.
    """

    _, target = _access_claim_guard_paths(
        workspace, session_directory, guard_relative,
    )
    flags = os.O_CREAT | os.O_RDWR
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
    ):
        flags |= getattr(os, flag_name, 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise BackendProtocolError(
            "provider session access claim guard could not be opened: "
            f"{type(exc).__name__}"
        ) from None

    try:
        try:
            os.set_inheritable(descriptor, False)
        except Exception as exc:
            raise BackendProtocolError(
                "provider session access claim guard could not be secured: "
                f"{type(exc).__name__}"
            ) from None
        _verify_access_claim_guard(
            descriptor, workspace, session_directory, guard_relative,
        )
        if _fcntl is None and _msvcrt is not None:
            _initialize_windows_access_claim_guard(descriptor)
        return descriptor
    except BaseException as exc:
        try:
            os.close(descriptor)
        except OSError as close_error:
            exc.add_note(
                "provider session access claim guard close failed: "
                f"{type(close_error).__name__}"
            )
        raise


def _access_claim_guard_paths(
    workspace: Workspace, session_directory: str, guard_relative: str,
) -> tuple[Path, Path]:
    """Resolve safe guard parent and file paths without creating either.

    Unsafe paths or a missing/non-directory parent raise workspace errors.
    """

    try:
        parent = workspace.path(session_directory)
        target = workspace.path(guard_relative)
    except WorkspaceSecurityError:
        raise WorkspaceFormatError(
            "provider session access claim guard path is unsafe"
        ) from None
    if not parent.is_dir():
        raise WorkspaceFormatError(
            "provider session access claim guard parent is not a directory"
        )
    return parent, target


def _verify_access_claim_guard(
    descriptor: int,
    workspace: Workspace,
    session_directory: str,
    guard_relative: str,
) -> None:
    """Verify both guard references are regular and, when meaningful, identical.

    Inspection failures raise ``BackendProtocolError``; unsafe type or
    identity changes raise ``WorkspaceFormatError``.
    """

    _, target = _access_claim_guard_paths(
        workspace, session_directory, guard_relative,
    )
    try:
        opened = os.fstat(descriptor)
        pathname = os.stat(target, follow_symlinks=False)
    except OSError as exc:
        raise BackendProtocolError(
            "provider session access claim guard could not be inspected: "
            f"{type(exc).__name__}"
        ) from None
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(pathname.st_mode):
        raise WorkspaceFormatError(
            "provider session access claim guard is not a regular file"
        )
    opened_identity = (opened.st_dev, opened.st_ino)
    pathname_identity = (pathname.st_dev, pathname.st_ino)
    identity_is_meaningful = any((*opened_identity, *pathname_identity))
    if identity_is_meaningful and opened_identity != pathname_identity:
        raise WorkspaceFormatError(
            "provider session access claim guard identity changed"
        )


def _initialize_windows_access_claim_guard(descriptor: int) -> None:
    """Ensure a Windows one-byte lock region exists and rewind the file.

    The guard is written only when empty; seek or write failures become
    ``BackendProtocolError``.
    """

    try:
        if os.fstat(descriptor).st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b'\x01') != 1:
                raise OSError("short guard initialization write")
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise BackendProtocolError(
            "provider session access claim guard could not be initialized: "
            f"{type(exc).__name__}"
        ) from None


def _lock_access_claim_guard(descriptor: int) -> None:
    """Acquire the platform advisory lock for a guard descriptor.

    POSIX acquisition blocks; Windows contention is retried. Unsupported
    platforms raise ``BackendProtocolError`` and other OS errors propagate.
    """

    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_EX)
        return
    if _msvcrt is None:
        raise BackendProtocolError(
            "provider session access claim guard is unsupported on this platform"
        )
    contention_errors = {
        errno.EACCES,
        getattr(errno, "EDEADLK", errno.EACCES),
    }
    while True:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            _msvcrt.locking(descriptor, _msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in contention_errors:
                raise
            time.sleep(_WINDOWS_LOCK_RETRY_SECONDS)


def _unlock_access_claim_guard(descriptor: int) -> None:
    """Release the platform advisory lock for a guard descriptor.

    Unsupported platforms raise ``BackendProtocolError``; OS failures
    propagate to the claim-guard cleanup policy.
    """

    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        return
    if _msvcrt is None:
        raise BackendProtocolError(
            "provider session access claim guard is unsupported on this platform"
        )
    os.lseek(descriptor, 0, os.SEEK_SET)
    _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)


__all__ = ("access_claim_guard",)
