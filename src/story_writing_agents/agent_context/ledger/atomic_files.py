"""Atomic files."""

from __future__ import annotations

import errno
import os
import secrets
import stat
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from .record_validation import (
    CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS,
    ContextLedgerErrorCode,
    MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES,
)
from .record_values import ContextLedgerKey, ContextLedgerRecord, context_ledger_scope_key
from .record_wire import ledger_record_bytes
from ...runtime_state_paths import CONTEXT_LEDGER_DIRECTORY
from ...workspace import Workspace, open_workspace_root_directory, verify_workspace_root_directory


try:  # pragma: no cover - exercised by fail-closed platform tests.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - platform dependent.
    _fcntl = None

_LEDGER_PATH = tuple(CONTEXT_LEDGER_DIRECTORY.split("/"))

_PRIVATE_PATH_START = 2

_LOCK_RETRY_SECONDS = 0.01

_MAX_LOCK_FREE_READ_ATTEMPTS = 8

class LedgerStorageFault(Exception):
    """Carry only a fixed storage failure code to the public boundary."""

    def __init__(self, code: ContextLedgerErrorCode) -> None:
        self.code = code

def _storage_fault(code: ContextLedgerErrorCode) -> None:
    raise LedgerStorageFault(code)

def _storage_error_for(error: OSError) -> ContextLedgerErrorCode:
    if error.errno in {
        errno.EISDIR,
        errno.ELOOP,
        errno.ENOTDIR,
        getattr(errno, "EMLINK", errno.ELOOP),
    }:
        return ContextLedgerErrorCode.STORAGE_UNSAFE
    return ContextLedgerErrorCode.STORAGE_FAILURE

def _require_storage_features() -> None:
    required_constants = ("O_DIRECTORY", "O_NOFOLLOW")
    required_dir_fd = (os.open, os.mkdir, os.stat, os.unlink)
    if (
        _fcntl is None
        or any(not getattr(os, name, 0) for name in required_constants)
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSUPPORTED)

def _identity(value: os.stat_result) -> tuple[int, int]:
    identity = (value.st_dev, value.st_ino)
    if identity[0] <= 0 or identity[1] <= 0:
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSUPPORTED)
    return identity

def _directory_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags

def _file_flags(*, write: bool) -> int:
    flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags

def _set_noninheritable(descriptor: int) -> None:
    try:
        os.set_inheritable(descriptor, False)
    except (AttributeError, OSError):
        pass

def _verify_directory(
    parent: int,
    name: str,
    descriptor: int,
    *,
    private: bool,
) -> None:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or _identity(opened) != _identity(named)
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    if private and (
        stat.S_IMODE(opened.st_mode) != 0o700
        or stat.S_IMODE(named.st_mode) != 0o700
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)

def _open_child_directory(
    parent: int,
    name: str,
    *,
    create: bool,
    private: bool,
) -> int | None:
    created = False
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        try:
            descriptor = os.open(name, _directory_flags(), dir_fd=parent)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    assert descriptor is not None
    _set_noninheritable(descriptor)
    if created:
        try:
            os.fchmod(descriptor, 0o700)
            os.fsync(parent)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _storage_fault(_storage_error_for(error))
    try:
        _verify_directory(parent, name, descriptor, private=private)
        return descriptor
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

@contextmanager
def ledger_directory(
    workspace: Workspace,
    *,
    create: bool,
) -> Iterator[int | None]:
    _require_storage_features()
    descriptors: list[int] = []
    body_failure: BaseException | None = None
    try:
        try:
            root = open_workspace_root_directory(workspace)
        except Exception:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        descriptors.append(root)
        current = root
        missing = False
        for index, name in enumerate(_LEDGER_PATH):
            child = _open_child_directory(
                current,
                name,
                create=create,
                private=index >= _PRIVATE_PATH_START,
            )
            if child is None:
                missing = True
                break
            descriptors.append(child)
            current = child
        try:
            verify_workspace_root_directory(workspace, root)
        except Exception:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        try:
            yield None if missing else current
        except BaseException as error:
            body_failure = error
            raise
        finally:
            try:
                verify_workspace_root_directory(workspace, root)
            except Exception:
                if body_failure is None:
                    _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    finally:
        cleanup_failed = False
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if cleanup_failed and body_failure is None:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)

def _verify_regular(
    parent: int,
    name: str,
    descriptor: int,
) -> os.stat_result:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) != 0o600
        or stat.S_IMODE(named.st_mode) != 0o600
        or _identity(opened) != _identity(named)
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    return opened

def _open_regular(
    parent: int,
    name: str,
    *,
    write: bool,
    create: bool,
) -> tuple[int, bool]:
    flags = _file_flags(write=write)
    created = False
    if create:
        try:
            descriptor = os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            created = True
        except FileExistsError:
            try:
                descriptor = os.open(name, flags, dir_fd=parent)
            except OSError as error:
                _storage_fault(_storage_error_for(error))
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    else:
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    _set_noninheritable(descriptor)
    if created:
        try:
            os.fchmod(descriptor, 0o600)
        except OSError as error:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _storage_fault(_storage_error_for(error))
    try:
        _verify_regular(parent, name, descriptor)
        return descriptor, created
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

def _open_existing_regular(
    parent: int,
    name: str,
    *,
    write: bool,
) -> int | None:
    """Open one existing safe regular file or return None if absent."""

    try:
        descriptor = os.open(name, _file_flags(write=write), dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    _set_noninheritable(descriptor)
    try:
        _verify_regular(parent, name, descriptor)
        return descriptor
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

def _read_descriptor_metadata(descriptor: int) -> os.stat_result:
    """Return safe metadata for one reader-owned regular descriptor."""

    try:
        opened = os.fstat(descriptor)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink not in {0, 1}
        or stat.S_IMODE(opened.st_mode) != 0o600
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    return opened

def _read_named_metadata(parent: int, name: str) -> os.stat_result:
    """Return safe metadata for the currently named reader target."""

    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError as error:
        _storage_fault(_storage_error_for(error))
    if (
        not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or stat.S_IMODE(named.st_mode) != 0o600
    ):
        _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
    return named

def _open_read_candidate(
    parent: int,
    name: str,
) -> tuple[int, os.stat_result, os.stat_result] | None:
    """Open one safe read candidate while permitting atomic replacement."""

    try:
        descriptor = os.open(name, _file_flags(write=False), dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        # A stable nonregular entry can fail before a descriptor exists. Probe
        # it without following links so that it remains a security failure.
        _read_named_metadata(parent, name)
        _storage_fault(_storage_error_for(error))
    _set_noninheritable(descriptor)
    try:
        opened = _read_descriptor_metadata(descriptor)
        named = _read_named_metadata(parent, name)
        return descriptor, opened, named
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

def _same_read_content(
    before: os.stat_result,
    after: os.stat_result,
) -> bool:
    """Return whether one opened inode retained the same exact content facts."""

    return (
        _identity(before) == _identity(after)
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )

def _read_payload_once(
    parent: int,
    name: str,
) -> tuple[bool, bytes | None, bool]:
    """Read one complete inode and report whether its name was replaced."""

    candidate = _open_read_candidate(parent, name)
    if candidate is None:
        return False, None, False
    descriptor, initial, initial_named = candidate
    try:
        oversized = initial.st_size > MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES
        chunks: list[bytes] = []
        total = 0
        while not oversized:
            try:
                chunk = os.read(
                    descriptor,
                    min(
                        65_536,
                        MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES + 1 - total,
                    ),
                )
            except OSError as error:
                _storage_fault(_storage_error_for(error))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES:
                oversized = True

        final = _read_descriptor_metadata(descriptor)
        final_named = _read_named_metadata(parent, name)
        if not _same_read_content(initial, final):
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        if not oversized and total != final.st_size:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)

        replaced = (
            _identity(initial) != _identity(initial_named)
            or _identity(initial_named) != _identity(final_named)
            or _identity(final) != _identity(final_named)
        )
        if replaced:
            # Re-stat after observing the new name. Atomic replacement leaves
            # the safely opened predecessor unlinked but content-stable.
            settled = _read_descriptor_metadata(descriptor)
            if settled.st_nlink != 0 or not _same_read_content(initial, settled):
                _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        elif (
            initial.st_nlink != 1
            or final.st_nlink != 1
            or initial.st_ctime_ns != final.st_ctime_ns
        ):
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        return True, None if oversized else b"".join(chunks), replaced
    finally:
        try:
            os.close(descriptor)
        except OSError:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)

def ledger_record_name(key: ContextLedgerKey) -> str:
    return f"{context_ledger_scope_key(key)[len('sha256:'): ]}.json"

def _lock_name(key: ContextLedgerKey) -> str:
    return f"{context_ledger_scope_key(key)[len('sha256:'): ]}.lock"

def ledger_read_payload(parent: int, name: str) -> tuple[bool, bytes | None]:
    predecessor: tuple[bool, bytes | None] | None = None
    for _attempt in range(_MAX_LOCK_FREE_READ_ATTEMPTS):
        found, payload, replaced = _read_payload_once(parent, name)
        if not found:
            return False, None
        predecessor = (True, payload)
        if not replaced:
            return predecessor
    assert predecessor is not None
    return predecessor

def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        if written <= 0:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)
        offset += written

def _existing_target_is_safe(parent: int, name: str) -> None:
    descriptor = _open_existing_regular(parent, name, write=False)
    if descriptor is None:
        return
    try:
        _verify_regular(parent, name, descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)

def ledger_replace_record(parent: int, record: ContextLedgerRecord) -> None:
    payload = ledger_record_bytes(record)
    target = ledger_record_name(record.key)
    _existing_target_is_safe(parent, target)
    temporary = f".tmp-{secrets.token_hex(16)}"
    descriptor: int | None = None
    replaced = False
    try:
        descriptor, created = _open_regular(
            parent,
            temporary,
            write=True,
            create=True,
        )
        if not created:
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        _write_all(descriptor, payload)
        try:
            os.fsync(descriptor)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        final = _verify_regular(parent, temporary, descriptor)
        if final.st_size != len(payload):
            _storage_fault(ContextLedgerErrorCode.STORAGE_UNSAFE)
        try:
            os.close(descriptor)
        except OSError as error:
            descriptor = None
            _storage_fault(_storage_error_for(error))
        descriptor = None
        try:
            os.replace(
                temporary,
                target,
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
        except OSError as error:
            _storage_fault(_storage_error_for(error))
        replaced = True
        target_descriptor, _created = _open_regular(
            parent,
            target,
            write=False,
            create=False,
        )
        try:
            _verify_regular(parent, target, target_descriptor)
        finally:
            try:
                os.close(target_descriptor)
            except OSError:
                _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)
        try:
            os.fsync(parent)
        except OSError as error:
            _storage_fault(_storage_error_for(error))
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError:
                pass

class _WriterState:
    """Weakly retained in-process serialization for one ledger key."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()

_WRITER_REGISTRY_GUARD = threading.Lock()

_WRITER_STATES: weakref.WeakValueDictionary[str, _WriterState] = (
    weakref.WeakValueDictionary()
)

def _writer_state(workspace: Workspace, key: ContextLedgerKey) -> _WriterState:
    registry_key = f"{os.fspath(workspace.root)}\0{context_ledger_scope_key(key)}"
    with _WRITER_REGISTRY_GUARD:
        state = _WRITER_STATES.get(registry_key)
        if state is None:
            state = _WriterState()
            _WRITER_STATES[registry_key] = state
        return state

def _acquire_advisory_lock(descriptor: int, *, deadline: float) -> None:
    assert _fcntl is not None
    contention = {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", errno.EAGAIN),
    }
    while True:
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return
        except OSError as error:
            if error.errno not in contention:
                _storage_fault(_storage_error_for(error))
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                _storage_fault(ContextLedgerErrorCode.LOCK_TIMEOUT)
            time.sleep(min(_LOCK_RETRY_SECONDS, remaining))

@contextmanager
def ledger_writer_directory(
    workspace: Workspace,
    key: ContextLedgerKey,
) -> Iterator[int]:
    started = time.monotonic()
    state = _writer_state(workspace, key)
    if not state.lock.acquire(timeout=CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS):
        _storage_fault(ContextLedgerErrorCode.LOCK_TIMEOUT)
    lock_descriptor: int | None = None
    advisory_acquired = False
    body_failure: BaseException | None = None
    try:
        with ledger_directory(workspace, create=True) as directory:
            assert directory is not None
            lock_descriptor, created = _open_regular(
                directory,
                _lock_name(key),
                write=True,
                create=True,
            )
            if created:
                try:
                    os.fsync(directory)
                except OSError as error:
                    _storage_fault(_storage_error_for(error))
            deadline = started + CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS
            _acquire_advisory_lock(lock_descriptor, deadline=deadline)
            advisory_acquired = True
            _verify_regular(directory, _lock_name(key), lock_descriptor)
            yield directory
    except BaseException as error:
        body_failure = error
        raise
    finally:
        cleanup_failed = False
        if lock_descriptor is not None:
            if advisory_acquired and _fcntl is not None:
                try:
                    _fcntl.flock(lock_descriptor, _fcntl.LOCK_UN)
                except OSError:
                    cleanup_failed = True
            try:
                os.close(lock_descriptor)
            except OSError:
                cleanup_failed = True
        try:
            state.lock.release()
        except RuntimeError:
            cleanup_failed = True
        if cleanup_failed and body_failure is None:
            _storage_fault(ContextLedgerErrorCode.STORAGE_FAILURE)
