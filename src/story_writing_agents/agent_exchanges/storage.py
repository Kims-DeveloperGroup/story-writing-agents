"""Descriptor-safe bounded JSONL storage for agent request/response exchanges."""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import re
import stat
import threading
import time
import unicodedata
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType, TracebackType
from typing import Iterator

from ..agents import ROSTER
from ..agent_exchange_paths import (
    AGENT_EXCHANGE_GLOBAL_PENDING_FILENAME,
    AGENT_EXCHANGE_GLOBAL_STATE_FILENAME,
    AGENT_EXCHANGE_GUARD_FILENAME,
    AGENT_EXCHANGE_SEGMENT_BACKUP_COUNT,
    is_agent_exchange_session_filename,
)
from ..workspace import (
    Workspace,
    open_workspace_root_directory,
    verify_workspace_root_directory,
)
from .contracts import (
    AGENT_EXCHANGE_CONTENT_MAX_BYTES,
    AGENT_EXCHANGE_SCHEMA_VERSION,
    AGENT_EXCHANGE_TOTAL_MAX_BYTES,
    AgentExchangeAppendReceipt,
    AgentExchangeFailure,
    AgentExchangeInventoryItem,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
    AgentExchangeSelection,
    AgentExchangeSnapshot,
    AgentExchangeStorageError,
)


_DEBUG_DIRECTORY_NAME = "debug_logs"
_STORE_DIRECTORY_NAME = "agent_exchanges"
_GUARD_FILENAME = AGENT_EXCHANGE_GUARD_FILENAME
_GLOBAL_STATE_FILENAME = AGENT_EXCHANGE_GLOBAL_STATE_FILENAME
_GLOBAL_PENDING_FILENAME = AGENT_EXCHANGE_GLOBAL_PENDING_FILENAME
_SEGMENT_MAX_BYTES = 2_097_152
_SEGMENT_BACKUP_COUNT = AGENT_EXCHANGE_SEGMENT_BACKUP_COUNT
_LOCK_TIMEOUT_SECONDS = 0.1
_LOCK_RETRY_SECONDS = 0.01
_STATE_MAX_BYTES = 512
_RECORD_MAX_BYTES = AGENT_EXCHANGE_CONTENT_MAX_BYTES + 4_096
_MAX_DIRECTORY_ENTRIES = 4_096
_MAX_SEQUENCE = 9_223_372_036_854_775_807
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_RFC3339_MILLISECONDS = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"\.[0-9]{3}\+00:00"
)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_GLOBAL_STATE_FIELDS = frozenset({"schema_version", "workspace_sequence"})
_STREAM_STATE_FIELDS = frozenset({"schema_version", "sequence"})
_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "workspace_sequence",
        "sequence",
        "timestamp",
        "request_type",
        "session_id",
        "scope_kind",
        "scope_id",
        "agent_id",
        "provider",
        "exchange_id",
        "content",
        "content_full_utf8_bytes",
        "content_retained_utf8_bytes",
        "content_full_characters",
        "content_retained_characters",
        "content_truncated",
        "content_sha256",
    }
)


def _optional_platform_module(name: str) -> ModuleType | None:
    """Import one platform lock module without making it a dependency."""

    try:
        return importlib.import_module(name)
    except ImportError:  # pragma: no cover - platform-specific availability.
        return None


_fcntl = _optional_platform_module("fcntl")
_msvcrt = _optional_platform_module("msvcrt")


class _WorkspaceProcessLock:
    """Weakly retained process-wide serialization for one exchange store."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


class _StorageFault(Exception):
    """Private safe classification retained until the public boundary."""

    __slots__ = ("failure",)

    def __init__(self, failure: AgentExchangeFailure) -> None:
        self.failure = failure
        super().__init__(failure.value)


@dataclass(frozen=True, slots=True)
class _LeafSnapshot:
    """Stable content-relevant facts for one managed regular file."""

    identity: tuple[int, int]
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _StoredRecord:
    """One validated canonical source record and its physical segment."""

    payload: bytes
    session_id: str
    agent_id: str
    filename: str
    workspace_sequence: int
    sequence: int
    scope_kind: AgentExchangeScopeKind
    scope_id: str


@dataclass(frozen=True, slots=True)
class _StreamSnapshot:
    """Validated high-water state and retained records for one agent stream."""

    session_id: str
    agent_id: str
    high_water: int
    records: tuple[_StoredRecord, ...]
    segment_sizes: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class _StoreSnapshot:
    """Bounded validated state beneath one acquired store guard."""

    workspace_high_water: int
    streams: tuple[_StreamSnapshot, ...]
    total_jsonl_bytes: int

    @property
    def records(self) -> tuple[_StoredRecord, ...]:
        return tuple(record for stream in self.streams for record in stream.records)


class _StoreDirectories:
    """Held identity-bound root, debug, and exchange directory descriptors."""

    __slots__ = (
        "debug_descriptor",
        "root_descriptor",
        "store_descriptor",
        "workspace",
    )

    def __init__(
        self,
        workspace: Workspace,
        root_descriptor: int,
        debug_descriptor: int,
        store_descriptor: int,
    ) -> None:
        self.workspace = workspace
        self.root_descriptor = root_descriptor
        self.debug_descriptor = debug_descriptor
        self.store_descriptor = store_descriptor


_PROCESS_LOCK_REGISTRY_GUARD = threading.Lock()
_PROCESS_LOCKS: weakref.WeakValueDictionary[str, _WorkspaceProcessLock] = (
    weakref.WeakValueDictionary()
)


class WorkspaceAgentExchangeStore:
    """Own one workspace's secure bounded agent-exchange JSONL area.

    Import and construction perform no filesystem work. Callers decide whether
    a fixed, classifiable storage failure is fail-open for their workflow.
    """

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Workspace) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        self._workspace = workspace

    def append(
        self,
        record: AgentExchangeRecordInput,
    ) -> AgentExchangeAppendReceipt:
        """Durably append one complete bounded record and return its receipt."""

        if type(record) is not AgentExchangeRecordInput:
            raise TypeError("record must be an AgentExchangeRecordInput")
        failure: AgentExchangeFailure | None = None
        result: AgentExchangeAppendReceipt | None = None
        try:
            with _locked_store(self._workspace, create=True) as directories:
                assert directories is not None
                result = _append_under_guard(directories, record)
        except Exception as exc:
            failure = _classify_failure(exc)
        if failure is not None:
            raise AgentExchangeStorageError(failure) from None
        assert result is not None
        return result

    def inventory(
        self,
        session_id: str,
    ) -> tuple[AgentExchangeInventoryItem, ...]:
        """Return content-free selectable summaries for one session."""

        try:
            _require_identifier(session_id, label="session_id")
        except (TypeError, ValueError):
            raise AgentExchangeStorageError(
                AgentExchangeFailure.UNSAFE_STORAGE
            ) from None
        failure: AgentExchangeFailure | None = None
        result: tuple[AgentExchangeInventoryItem, ...] | None = None
        try:
            with _locked_store(self._workspace, create=False) as directories:
                if directories is None:
                    result = ()
                else:
                    snapshot = _scan_store(directories)
                    result = _project_inventory(snapshot, session_id=session_id)
        except Exception as exc:
            failure = _classify_failure(exc)
        if failure is not None:
            raise AgentExchangeStorageError(failure) from None
        assert result is not None
        return result

    def snapshot(self, selection: AgentExchangeSelection) -> AgentExchangeSnapshot:
        """Return immutable canonical JSONL bytes for one exact selection."""

        if type(selection) is not AgentExchangeSelection:
            raise TypeError("selection must be an AgentExchangeSelection")
        failure: AgentExchangeFailure | None = None
        result: AgentExchangeSnapshot | None = None
        try:
            with _locked_store(self._workspace, create=False) as directories:
                if directories is None:
                    raise _StorageFault(AgentExchangeFailure.NOT_FOUND)
                store_snapshot = _scan_store(directories)
                result = _project_snapshot(store_snapshot, selection=selection)
        except Exception as exc:
            failure = _classify_failure(exc)
        if failure is not None:
            raise AgentExchangeStorageError(failure) from None
        assert result is not None
        return result


def _classify_failure(failure: Exception) -> AgentExchangeFailure:
    """Map all ordinary implementation failures to a fixed safe code."""

    if isinstance(failure, _StorageFault):
        return failure.failure
    if isinstance(failure, OSError):
        if failure.errno in {
            errno.ENOSYS,
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        }:
            return AgentExchangeFailure.UNSUPPORTED_PLATFORM
        if failure.errno in {errno.ENOSPC, errno.EDQUOT, errno.EFBIG}:
            return AgentExchangeFailure.CAPACITY_EXCEEDED
        if failure.errno in {
            errno.ELOOP,
            errno.ENOTDIR,
            getattr(errno, "EMLINK", errno.ELOOP),
        }:
            return AgentExchangeFailure.UNSAFE_STORAGE
        return AgentExchangeFailure.IO_FAILED
    if isinstance(failure, (UnicodeError, json.JSONDecodeError, ValueError, TypeError)):
        return AgentExchangeFailure.CORRUPT_STORAGE
    return AgentExchangeFailure.IO_FAILED


def _require_identifier(value: object, *, label: str) -> str:
    """Validate a caller-supplied path identity before any filesystem access."""

    if type(value) is not str:
        raise TypeError(f"{label} must be text")
    if _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def _process_lock_for(workspace: Workspace) -> _WorkspaceProcessLock:
    """Return this module's independent lock for one resolved workspace."""

    key = str(workspace.root)
    with _PROCESS_LOCK_REGISTRY_GUARD:
        retained = _PROCESS_LOCKS.get(key)
        if retained is None:
            retained = _WorkspaceProcessLock()
            _PROCESS_LOCKS[key] = retained
        return retained


@contextmanager
def _locked_store(
    workspace: Workspace,
    *,
    create: bool,
) -> Iterator[_StoreDirectories | None]:
    """Hold the independent process and advisory guards for one operation."""

    process_lock = _process_lock_for(workspace)
    if not process_lock.lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
        raise _StorageFault(AgentExchangeFailure.CONTENTION)
    directories: _StoreDirectories | None = None
    guard_descriptor: int | None = None
    locked = False
    primary_failure: tuple[BaseException, TracebackType | None] | None = None
    try:
        directories = _prepare_directories(workspace, create=create)
        if directories is None:
            yield None
        else:
            guard_descriptor = _open_guard(directories, create=create)
            if guard_descriptor is None:
                raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
            locked = _acquire_advisory_lock(
                guard_descriptor,
                timeout_seconds=_LOCK_TIMEOUT_SECONDS,
            )
            if not locked:
                raise _StorageFault(AgentExchangeFailure.CONTENTION)
            _verify_guard(directories, guard_descriptor)
            yield directories
    except BaseException as exc:
        primary_failure = (exc, exc.__traceback__)

    cleanup_failure: tuple[BaseException, TracebackType | None] | None = None
    if locked and guard_descriptor is not None:
        try:
            _release_advisory_lock(guard_descriptor)
        except BaseException as exc:
            cleanup_failure = (exc, exc.__traceback__)
    for descriptor in (
        guard_descriptor,
        None if directories is None else directories.store_descriptor,
        None if directories is None else directories.debug_descriptor,
        None if directories is None else directories.root_descriptor,
    ):
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except BaseException as exc:
            if cleanup_failure is None:
                cleanup_failure = (exc, exc.__traceback__)
    try:
        process_lock.lock.release()
    except BaseException as exc:
        if cleanup_failure is None:
            cleanup_failure = (exc, exc.__traceback__)

    retained = primary_failure or cleanup_failure
    if retained is not None:
        failure, traceback = retained
        raise failure.with_traceback(traceback)


def _require_safe_primitives() -> None:
    """Reject hosts lacking descriptor-relative no-follow storage primitives."""

    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    supports_fd = getattr(os, "supports_fd", ())
    required_dir_fd = (os.mkdir, os.open, os.stat, os.unlink, os.rename)
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or any(function not in supports_dir_fd for function in required_dir_fd)
        or os.stat not in supports_follow_symlinks
        or os.listdir not in supports_fd
        or (_fcntl is None and _msvcrt is None)
    ):
        raise _StorageFault(AgentExchangeFailure.UNSUPPORTED_PLATFORM)


def _prepare_directories(
    workspace: Workspace,
    *,
    create: bool,
) -> _StoreDirectories | None:
    """Open the fixed exchange namespace beneath an identity-bound root."""

    _require_safe_primitives()
    try:
        root_descriptor = open_workspace_root_directory(workspace)
    except Exception:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE) from None
    debug_descriptor: int | None = None
    store_descriptor: int | None = None
    try:
        debug_descriptor = _open_child_directory(
            root_descriptor,
            _DEBUG_DIRECTORY_NAME,
            create=create,
            require_private=False,
        )
        if debug_descriptor is None:
            os.close(root_descriptor)
            return None
        store_descriptor = _open_child_directory(
            debug_descriptor,
            _STORE_DIRECTORY_NAME,
            create=create,
            require_private=True,
        )
        if store_descriptor is None:
            os.close(debug_descriptor)
            os.close(root_descriptor)
            return None
        directories = _StoreDirectories(
            workspace,
            root_descriptor,
            debug_descriptor,
            store_descriptor,
        )
        _verify_directories(directories)
        return directories
    except BaseException:
        for descriptor in (store_descriptor, debug_descriptor, root_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except Exception:
                    pass
        raise


def _open_child_directory(
    parent_descriptor: int,
    name: str,
    *,
    create: bool,
    require_private: bool,
) -> int | None:
    """Open one exact directory child without following a filesystem alias."""

    created = False
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            created = True
        except FileExistsError:
            pass
    elif not _name_exists(parent_descriptor, name):
        return None
    flags = os.O_RDONLY
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, flag_name, 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if not create:
            return None
        raise
    try:
        _set_not_inheritable(descriptor)
        if created:
            os.fchmod(descriptor, 0o700)
        _require_exact_child_name(parent_descriptor, name)
        _verify_named_directory(
            parent_descriptor=parent_descriptor,
            descriptor=descriptor,
            name=name,
            require_private=require_private,
        )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _name_exists(parent_descriptor: int, name: str) -> bool:
    """Report exact lexical presence without following the named entry."""

    return name in os.listdir(parent_descriptor)


def _require_exact_child_name(parent_descriptor: int, expected: str) -> None:
    """Reject case-folded or normalized aliases of a requested child."""

    target = unicodedata.normalize("NFKC", expected).casefold()
    matching = [
        name
        for name in _bounded_listdir(parent_descriptor)
        if unicodedata.normalize("NFKC", name).casefold() == target
    ]
    if matching != [expected]:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)


def _verify_named_directory(
    *,
    parent_descriptor: int,
    descriptor: int,
    name: str,
    require_private: bool,
) -> None:
    """Require one held directory and its exact name to retain identity."""

    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if _meaningful_identity(opened) != _meaningful_identity(named):
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if require_private and stat.S_IMODE(opened.st_mode) != 0o700:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)


def _verify_directories(directories: _StoreDirectories) -> None:
    """Reverify the root and only this store's fixed namespace."""

    try:
        verify_workspace_root_directory(
            directories.workspace,
            directories.root_descriptor,
        )
    except Exception:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE) from None
    _require_exact_child_name(directories.root_descriptor, _DEBUG_DIRECTORY_NAME)
    _verify_named_directory(
        parent_descriptor=directories.root_descriptor,
        descriptor=directories.debug_descriptor,
        name=_DEBUG_DIRECTORY_NAME,
        require_private=False,
    )
    _require_exact_child_name(directories.debug_descriptor, _STORE_DIRECTORY_NAME)
    _verify_named_directory(
        parent_descriptor=directories.debug_descriptor,
        descriptor=directories.store_descriptor,
        name=_STORE_DIRECTORY_NAME,
        require_private=True,
    )


def _open_guard(
    directories: _StoreDirectories,
    *,
    create: bool,
) -> int | None:
    """Open or exclusively create this store's independent advisory guard."""

    _verify_directories(directories)
    existing = _stat_leaf(
        directories.store_descriptor,
        _GUARD_FILENAME,
        maximum_size=1,
        required_mode=0o600,
    )
    if existing is None and not create:
        return None
    flags = os.O_RDWR
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, flag_name, 0)
    if existing is None:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(
            _GUARD_FILENAME,
            flags,
            0o600,
            dir_fd=directories.store_descriptor,
        )
    except FileExistsError:
        descriptor = os.open(
            _GUARD_FILENAME,
            flags & ~(os.O_CREAT | os.O_EXCL),
            dir_fd=directories.store_descriptor,
        )
    try:
        _set_not_inheritable(descriptor)
        if existing is None:
            os.fchmod(descriptor, 0o600)
        if _msvcrt is not None and _fcntl is None and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        _verify_open_leaf(
            directories.store_descriptor,
            descriptor,
            _GUARD_FILENAME,
            maximum_size=1,
            required_mode=0o600,
        )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_guard(directories: _StoreDirectories, descriptor: int) -> None:
    """Require the acquired advisory guard and directory identities to persist."""

    _verify_directories(directories)
    _verify_open_leaf(
        directories.store_descriptor,
        descriptor,
        _GUARD_FILENAME,
        maximum_size=1,
        required_mode=0o600,
    )


def _acquire_advisory_lock(descriptor: int, *, timeout_seconds: float) -> bool:
    """Acquire the cross-process guard within one bounded interval."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            if _fcntl is not None:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            elif _msvcrt is not None:  # pragma: no cover - Windows path.
                os.lseek(descriptor, 0, os.SEEK_SET)
                _msvcrt.locking(descriptor, _msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - rejected by primitive preflight.
                return False
            return True
        except OSError as exc:
            if exc.errno not in {
                errno.EACCES,
                errno.EAGAIN,
                getattr(errno, "EDEADLK", errno.EACCES),
            }:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(_LOCK_RETRY_SECONDS, remaining))


def _release_advisory_lock(descriptor: int) -> None:
    """Release this module's cross-process guard."""

    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:  # pragma: no cover - Windows path.
        os.lseek(descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)


def _bounded_listdir(descriptor: int) -> list[str]:
    """Return one bounded namespace listing."""

    names = os.listdir(descriptor)
    if len(names) > _MAX_DIRECTORY_ENTRIES:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
    if any(type(name) is not str for name in names):
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    return names


def _meaningful_identity(value: os.stat_result) -> tuple[int, int]:
    """Return a stable device/inode identity or fail closed."""

    if (
        type(value.st_dev) is not int
        or value.st_dev <= 0
        or type(value.st_ino) is not int
        or value.st_ino <= 0
    ):
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    return value.st_dev, value.st_ino


def _set_not_inheritable(descriptor: int) -> None:
    """Best-effort clear inheritance while retaining platform compatibility."""

    try:
        os.set_inheritable(descriptor, False)
    except (AttributeError, OSError):
        pass


def _stat_leaf(
    parent_descriptor: int,
    filename: str,
    *,
    maximum_size: int,
    required_mode: int,
) -> _LeafSnapshot | None:
    """Return a no-follow snapshot of one safe managed regular file."""

    try:
        current = os.stat(
            filename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if stat.S_IMODE(current.st_mode) != required_mode:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if current.st_size < 0 or current.st_size > maximum_size:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
    return _LeafSnapshot(
        identity=_meaningful_identity(current),
        size=current.st_size,
        modified_ns=current.st_mtime_ns,
        changed_ns=current.st_ctime_ns,
    )


def _verify_open_leaf(
    parent_descriptor: int,
    descriptor: int,
    filename: str,
    *,
    maximum_size: int,
    required_mode: int,
) -> _LeafSnapshot:
    """Require an open descriptor and its managed name to retain identity."""

    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if stat.S_IMODE(opened.st_mode) != required_mode:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if opened.st_size < 0 or opened.st_size > maximum_size:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
    named = _stat_leaf(
        parent_descriptor,
        filename,
        maximum_size=maximum_size,
        required_mode=required_mode,
    )
    if named is None or _meaningful_identity(opened) != named.identity:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    return _LeafSnapshot(
        identity=named.identity,
        size=opened.st_size,
        modified_ns=opened.st_mtime_ns,
        changed_ns=opened.st_ctime_ns,
    )


def _require_same_snapshot(
    current: _LeafSnapshot,
    expected: _LeafSnapshot,
) -> None:
    """Reject a managed leaf changed during one guarded inspection."""

    if current != expected:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)


def _read_leaf(
    parent_descriptor: int,
    filename: str,
    *,
    maximum_size: int,
) -> bytes:
    """Read one stable strict-size managed leaf through its directory."""

    expected = _stat_leaf(
        parent_descriptor,
        filename,
        maximum_size=maximum_size,
        required_mode=0o600,
    )
    if expected is None:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    flags = os.O_RDONLY
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, flag_name, 0)
    descriptor = os.open(filename, flags, dir_fd=parent_descriptor)
    try:
        _set_not_inheritable(descriptor)
        opened = _verify_open_leaf(
            parent_descriptor,
            descriptor,
            filename,
            maximum_size=maximum_size,
            required_mode=0o600,
        )
        _require_same_snapshot(opened, expected)
        chunks: list[bytes] = []
        remaining = opened.size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        final = _verify_open_leaf(
            parent_descriptor,
            descriptor,
            filename,
            maximum_size=maximum_size,
            required_mode=0o600,
        )
        _require_same_snapshot(final, opened)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _open_session_directory(
    directories: _StoreDirectories,
    session_id: str,
    *,
    create: bool,
) -> int | None:
    """Open one exact private session directory beneath this store only."""

    _verify_directories(directories)
    descriptor = _open_child_directory(
        directories.store_descriptor,
        session_id,
        create=create,
        require_private=True,
    )
    if descriptor is not None:
        _verify_session_directory(directories, session_id, descriptor)
    return descriptor


def _verify_session_directory(
    directories: _StoreDirectories,
    session_id: str,
    descriptor: int,
) -> None:
    """Reverify one held session directory and its store ancestry."""

    _verify_directories(directories)
    _require_exact_child_name(directories.store_descriptor, session_id)
    _verify_named_directory(
        parent_descriptor=directories.store_descriptor,
        descriptor=descriptor,
        name=session_id,
        require_private=True,
    )


def _global_state_payload(workspace_sequence: int) -> bytes:
    """Serialize one canonical global high-water reservation."""

    return _canonical_json_line(
        {
            "schema_version": AGENT_EXCHANGE_SCHEMA_VERSION,
            "workspace_sequence": workspace_sequence,
        }
    )


def _stream_state_payload(sequence: int) -> bytes:
    """Serialize one canonical per-session/per-agent high-water reservation."""

    return _canonical_json_line(
        {
            "schema_version": AGENT_EXCHANGE_SCHEMA_VERSION,
            "sequence": sequence,
        }
    )


def _canonical_json_line(value: dict[str, object]) -> bytes:
    """Return deterministic strict-UTF-8 compact JSON terminated once."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        + b"\n"
    )


def _reject_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build one JSON object while rejecting duplicate member names."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
        result[key] = value
    return result


def _parse_json_object(payload: bytes) -> dict[str, object]:
    """Decode one strict UTF-8 duplicate-free JSON object."""

    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_fields)
    except _StorageFault:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    if type(value) is not dict:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return value


def _parse_state(
    payload: bytes,
    *,
    field: str,
    expected_fields: frozenset[str],
) -> int:
    """Validate one canonical schema-v1 high-water state document."""

    value = _parse_json_object(payload)
    if frozenset(value) != expected_fields:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if type(value.get("schema_version")) is not int or (
        value["schema_version"] != AGENT_EXCHANGE_SCHEMA_VERSION
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    high_water = value.get(field)
    if type(high_water) is not int or not 0 <= high_water <= _MAX_SEQUENCE:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    expected = (
        _global_state_payload(high_water)
        if field == "workspace_sequence"
        else _stream_state_payload(high_water)
    )
    if payload != expected:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return high_water


def _stream_state_filename(agent_id: str) -> str:
    return f"{agent_id}.sequence.json"


def _stream_state_pending_filename(agent_id: str) -> str:
    return f".{agent_id}.sequence.pending"


def _segment_pending_filename(agent_id: str) -> str:
    return f".{agent_id}.segment.pending"


def _active_segment_filename(agent_id: str) -> str:
    return f"{agent_id}.jsonl"


def _backup_segment_filename(agent_id: str, index: int) -> str:
    return f"{agent_id}.jsonl.{index}"


def _recognized_session_filename(name: str) -> bool:
    """Report whether a leaf name is exactly owned by this storage schema."""

    return is_agent_exchange_session_filename(name)


def _scan_store(
    directories: _StoreDirectories,
    *,
    allow_over_capacity: bool = False,
) -> _StoreSnapshot:
    """Validate and snapshot the complete bounded exchange namespace."""

    _verify_directories(directories)
    names = _bounded_listdir(directories.store_descriptor)
    allowed_fixed = {
        _GUARD_FILENAME,
        _GLOBAL_STATE_FILENAME,
        _GLOBAL_PENDING_FILENAME,
    }
    session_names: list[str] = []
    for name in names:
        if name in allowed_fixed:
            continue
        if _SAFE_IDENTIFIER.fullmatch(name) is None:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        session_names.append(name)
    if _GLOBAL_PENDING_FILENAME in names:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    _verify_casefold_unique(session_names)

    global_payload = (
        _read_leaf(
            directories.store_descriptor,
            _GLOBAL_STATE_FILENAME,
            maximum_size=_STATE_MAX_BYTES,
        )
        if _GLOBAL_STATE_FILENAME in names
        else None
    )
    workspace_high_water = (
        0
        if global_payload is None
        else _parse_state(
            global_payload,
            field="workspace_sequence",
            expected_fields=_GLOBAL_STATE_FIELDS,
        )
    )

    streams: list[_StreamSnapshot] = []
    total_jsonl_bytes = 0
    all_workspace_sequences: set[int] = set()
    maximum_workspace_sequence = 0
    for session_id in sorted(session_names):
        session_descriptor = _open_session_directory(
            directories,
            session_id,
            create=False,
        )
        if session_descriptor is None:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        try:
            session_streams = _scan_session(
                directories,
                session_descriptor,
                session_id=session_id,
            )
        finally:
            os.close(session_descriptor)
        for stream in session_streams:
            streams.append(stream)
            total_jsonl_bytes += sum(size for _, size in stream.segment_sizes)
            for record in stream.records:
                if record.workspace_sequence in all_workspace_sequences:
                    raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
                all_workspace_sequences.add(record.workspace_sequence)
                maximum_workspace_sequence = max(
                    maximum_workspace_sequence,
                    record.workspace_sequence,
                )

    if (streams or workspace_high_water) and global_payload is None:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if workspace_high_water < maximum_workspace_sequence:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if total_jsonl_bytes > AGENT_EXCHANGE_TOTAL_MAX_BYTES and not allow_over_capacity:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
    if total_jsonl_bytes > AGENT_EXCHANGE_TOTAL_MAX_BYTES + _SEGMENT_MAX_BYTES:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
    streams.sort(key=lambda item: (item.session_id, item.agent_id))
    return _StoreSnapshot(
        workspace_high_water=workspace_high_water,
        streams=tuple(streams),
        total_jsonl_bytes=total_jsonl_bytes,
    )


def _scan_session(
    directories: _StoreDirectories,
    session_descriptor: int,
    *,
    session_id: str,
) -> tuple[_StreamSnapshot, ...]:
    """Validate every managed agent stream in one held session directory."""

    _verify_session_directory(directories, session_id, session_descriptor)
    names = _bounded_listdir(session_descriptor)
    for name in names:
        if not _recognized_session_filename(name):
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        if name.startswith("."):
            raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)

    streams: list[_StreamSnapshot] = []
    for agent_id in ROSTER:
        state_name = _stream_state_filename(agent_id)
        state_payload = (
            _read_leaf(
                session_descriptor,
                state_name,
                maximum_size=_STATE_MAX_BYTES,
            )
            if state_name in names
            else None
        )
        high_water = (
            0
            if state_payload is None
            else _parse_state(
                state_payload,
                field="sequence",
                expected_fields=_STREAM_STATE_FIELDS,
            )
        )
        segment_names = [
            _backup_segment_filename(agent_id, index)
            for index in range(_SEGMENT_BACKUP_COUNT, 0, -1)
            if _backup_segment_filename(agent_id, index) in names
        ]
        active_name = _active_segment_filename(agent_id)
        if active_name in names:
            segment_names.append(active_name)
        if segment_names and state_payload is None:
            raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
        if state_payload is None and not segment_names:
            continue

        records: list[_StoredRecord] = []
        sizes: list[tuple[str, int]] = []
        previous_sequence = 0
        previous_workspace_sequence = 0
        for filename in segment_names:
            payload = _read_leaf(
                session_descriptor,
                filename,
                maximum_size=_SEGMENT_MAX_BYTES,
            )
            if not payload:
                raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
            sizes.append((filename, len(payload)))
            for record in _parse_segment(
                payload,
                session_id=session_id,
                agent_id=agent_id,
                filename=filename,
            ):
                if (
                    record.sequence <= previous_sequence
                    or record.workspace_sequence <= previous_workspace_sequence
                ):
                    raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
                previous_sequence = record.sequence
                previous_workspace_sequence = record.workspace_sequence
                records.append(record)
        if high_water < previous_sequence:
            raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
        streams.append(
            _StreamSnapshot(
                session_id=session_id,
                agent_id=agent_id,
                high_water=high_water,
                records=tuple(records),
                segment_sizes=tuple(sizes),
            )
        )
    return tuple(streams)


def _parse_segment(
    payload: bytes,
    *,
    session_id: str,
    agent_id: str,
    filename: str,
) -> tuple[_StoredRecord, ...]:
    """Parse canonical complete records from one size-bounded segment."""

    if not payload.endswith(b"\n"):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    lines = payload.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    records: list[_StoredRecord] = []
    for line in lines:
        if len(line) > _RECORD_MAX_BYTES:
            raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
        records.append(
            _parse_record(
                line,
                session_id=session_id,
                agent_id=agent_id,
                filename=filename,
            )
        )
    return tuple(records)


def _parse_record(
    payload: bytes,
    *,
    session_id: str,
    agent_id: str,
    filename: str,
) -> _StoredRecord:
    """Validate every durable schema and content-retention invariant."""

    value = _parse_json_object(payload)
    if frozenset(value) != _RECORD_FIELDS or _canonical_json_line(value) != payload:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if type(value["schema_version"]) is not int or (
        value["schema_version"] != AGENT_EXCHANGE_SCHEMA_VERSION
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    workspace_sequence = value["workspace_sequence"]
    sequence = value["sequence"]
    if (
        type(workspace_sequence) is not int
        or workspace_sequence <= 0
        or type(sequence) is not int
        or sequence <= 0
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    timestamp = value["timestamp"]
    if type(timestamp) is not str or _RFC3339_MILLISECONDS.fullmatch(timestamp) is None:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp)
    except ValueError:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    if parsed_timestamp.utcoffset() != UTC.utcoffset(None):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    request_type_value = value["request_type"]
    scope_kind_value = value["scope_kind"]
    provider_value = value["provider"]
    session_value = value["session_id"]
    scope_value = value["scope_id"]
    agent_value = value["agent_id"]
    exchange_value = value["exchange_id"]
    content_value = value["content"]
    if any(
        type(item) is not str
        for item in (
            request_type_value,
            scope_kind_value,
            provider_value,
            session_value,
            scope_value,
            agent_value,
            exchange_value,
            content_value,
        )
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    assert isinstance(request_type_value, str)
    assert isinstance(scope_kind_value, str)
    assert isinstance(provider_value, str)
    assert isinstance(session_value, str)
    assert isinstance(scope_value, str)
    assert isinstance(agent_value, str)
    assert isinstance(exchange_value, str)
    assert isinstance(content_value, str)
    try:
        request_type = AgentExchangeRequestType(request_type_value)
        scope_kind = AgentExchangeScopeKind(scope_kind_value)
        provider = AgentExchangeProvider(provider_value)
    except (TypeError, ValueError):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    try:
        input_record = AgentExchangeRecordInput(
            session_id=session_value,
            scope_kind=scope_kind,
            scope_id=scope_value,
            agent_id=agent_value,
            provider=provider,
            exchange_id=exchange_value,
            request_type=request_type,
            content=content_value,
        )
    except (TypeError, ValueError):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    if input_record.session_id != session_id or input_record.agent_id != agent_id:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    retained = input_record.content.encode("utf-8", errors="strict")
    full_size = value["content_full_utf8_bytes"]
    retained_size = value["content_retained_utf8_bytes"]
    full_characters = value["content_full_characters"]
    retained_characters = value["content_retained_characters"]
    truncated = value["content_truncated"]
    digest = value["content_sha256"]
    if (
        type(full_size) is not int
        or full_size < 0
        or type(retained_size) is not int
        or retained_size < 0
        or retained_size != len(retained)
        or retained_size > AGENT_EXCHANGE_CONTENT_MAX_BYTES
        or type(full_characters) is not int
        or full_characters < 0
        or type(retained_characters) is not int
        or retained_characters < 0
        or retained_characters != len(input_record.content)
        or retained_characters > full_characters
        or type(truncated) is not bool
        or type(digest) is not str
        or _SHA256.fullmatch(digest) is None
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if truncated:
        if (
            full_size <= AGENT_EXCHANGE_CONTENT_MAX_BYTES
            or full_size <= retained_size
            or retained_size < AGENT_EXCHANGE_CONTENT_MAX_BYTES - 3
            or full_characters <= retained_characters
        ):
            raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    elif (
        full_size != retained_size
        or full_characters != retained_characters
        or digest != f"sha256:{hashlib.sha256(retained).hexdigest()}"
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return _StoredRecord(
        payload=payload,
        session_id=session_id,
        agent_id=agent_id,
        filename=filename,
        workspace_sequence=workspace_sequence,
        sequence=sequence,
        scope_kind=scope_kind,
        scope_id=input_record.scope_id,
    )


def _verify_casefold_unique(names: list[str]) -> None:
    """Reject ambiguous normalized session directory aliases."""

    seen: set[str] = set()
    for name in names:
        normalized = unicodedata.normalize("NFKC", name).casefold()
        if normalized in seen:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        seen.add(normalized)


def _cleanup_pending_files(directories: _StoreDirectories) -> None:
    """Remove only this store's verified fixed transaction remnants."""

    _verify_directories(directories)
    root_names = _bounded_listdir(directories.store_descriptor)
    allowed_fixed = {
        _GUARD_FILENAME,
        _GLOBAL_STATE_FILENAME,
        _GLOBAL_PENDING_FILENAME,
    }
    session_names: list[str] = []
    for name in root_names:
        if name in allowed_fixed:
            continue
        if _SAFE_IDENTIFIER.fullmatch(name) is None:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        session_names.append(name)
    _verify_casefold_unique(session_names)
    if _GLOBAL_PENDING_FILENAME in root_names:
        _unlink_managed_leaf(
            directories.store_descriptor,
            _GLOBAL_PENDING_FILENAME,
            maximum_size=_STATE_MAX_BYTES,
        )
        os.fsync(directories.store_descriptor)

    for session_id in sorted(session_names):
        session_descriptor = _open_session_directory(
            directories,
            session_id,
            create=False,
        )
        if session_descriptor is None:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        try:
            names = _bounded_listdir(session_descriptor)
            pending_names: list[str] = []
            for name in names:
                if not _recognized_session_filename(name):
                    raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
                if name.startswith("."):
                    pending_names.append(name)
            for name in pending_names:
                maximum_size = (
                    _STATE_MAX_BYTES if ".sequence." in name else _SEGMENT_MAX_BYTES
                )
                _unlink_managed_leaf(
                    session_descriptor,
                    name,
                    maximum_size=maximum_size,
                )
            if pending_names:
                os.fsync(session_descriptor)
        finally:
            os.close(session_descriptor)


def _unlink_managed_leaf(
    parent_descriptor: int,
    filename: str,
    *,
    maximum_size: int,
) -> None:
    """Unlink one exact verified managed leaf without following aliases."""

    expected = _stat_leaf(
        parent_descriptor,
        filename,
        maximum_size=maximum_size,
        required_mode=0o600,
    )
    if expected is None:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    current = _stat_leaf(
        parent_descriptor,
        filename,
        maximum_size=maximum_size,
        required_mode=0o600,
    )
    if current != expected:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    os.unlink(filename, dir_fd=parent_descriptor)


def _atomic_replace_leaf(
    parent_descriptor: int,
    *,
    filename: str,
    pending_filename: str,
    payload: bytes,
    maximum_size: int,
) -> None:
    """Synchronize and atomically replace one exact managed regular file."""

    if len(payload) > maximum_size:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
    existing_pending = _stat_leaf(
        parent_descriptor,
        pending_filename,
        maximum_size=maximum_size,
        required_mode=0o600,
    )
    if existing_pending is not None:
        _unlink_managed_leaf(
            parent_descriptor,
            pending_filename,
            maximum_size=maximum_size,
        )
    existing_final = _stat_leaf(
        parent_descriptor,
        filename,
        maximum_size=maximum_size,
        required_mode=0o600,
    )

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
    ):
        flags |= getattr(os, flag_name, 0)
    descriptor = os.open(
        pending_filename,
        flags,
        0o600,
        dir_fd=parent_descriptor,
    )
    pending_identity: tuple[int, int] | None = None
    body_failure: BaseException | None = None
    try:
        _set_not_inheritable(descriptor)
        os.fchmod(descriptor, 0o600)
        opened = _verify_open_leaf(
            parent_descriptor,
            descriptor,
            pending_filename,
            maximum_size=maximum_size,
            required_mode=0o600,
        )
        pending_identity = opened.identity
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        completed = _verify_open_leaf(
            parent_descriptor,
            descriptor,
            pending_filename,
            maximum_size=maximum_size,
            required_mode=0o600,
        )
        if completed.identity != opened.identity or completed.size != len(payload):
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    except BaseException as exc:
        body_failure = exc
    try:
        os.close(descriptor)
    except BaseException as exc:
        if body_failure is None:
            body_failure = exc
    if body_failure is not None:
        _discard_matching_pending(
            parent_descriptor,
            pending_filename,
            pending_identity,
            maximum_size=maximum_size,
        )
        raise body_failure

    assert pending_identity is not None
    if existing_final is not None:
        current_final = _stat_leaf(
            parent_descriptor,
            filename,
            maximum_size=maximum_size,
            required_mode=0o600,
        )
        if current_final != existing_final:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    current_pending = _stat_leaf(
        parent_descriptor,
        pending_filename,
        maximum_size=maximum_size,
        required_mode=0o600,
    )
    if current_pending is None or current_pending.identity != pending_identity:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    os.rename(
        pending_filename,
        filename,
        src_dir_fd=parent_descriptor,
        dst_dir_fd=parent_descriptor,
    )
    os.fsync(parent_descriptor)
    if _read_leaf(parent_descriptor, filename, maximum_size=maximum_size) != payload:
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)


def _discard_matching_pending(
    parent_descriptor: int,
    filename: str,
    expected_identity: tuple[int, int] | None,
    *,
    maximum_size: int,
) -> None:
    """Best-effort discard only the pending file created by this operation."""

    if expected_identity is None:
        return
    try:
        current = _stat_leaf(
            parent_descriptor,
            filename,
            maximum_size=maximum_size,
            required_mode=0o600,
        )
        if current is not None and current.identity == expected_identity:
            os.unlink(filename, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
    except Exception:
        pass


def _write_all(descriptor: int, payload: bytes) -> None:
    """Write every byte or fail without accepting a short-write suffix."""

    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "agent exchange write made no progress")
        offset += written


def _complete_utf8_prefix(value: bytes) -> bytes:
    """Return the longest strict-UTF-8 prefix within the content bound."""

    if len(value) <= AGENT_EXCHANGE_CONTENT_MAX_BYTES:
        return value
    prefix = value[:AGENT_EXCHANGE_CONTENT_MAX_BYTES]
    try:
        prefix.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        prefix = prefix[: exc.start]
    prefix.decode("utf-8", errors="strict")
    return prefix


def _find_stream(
    snapshot: _StoreSnapshot,
    *,
    session_id: str,
    agent_id: str,
) -> _StreamSnapshot | None:
    """Return one exact stream from a validated bounded snapshot."""

    return next(
        (
            stream
            for stream in snapshot.streams
            if stream.session_id == session_id and stream.agent_id == agent_id
        ),
        None,
    )


def _append_under_guard(
    directories: _StoreDirectories,
    record: AgentExchangeRecordInput,
) -> AgentExchangeAppendReceipt:
    """Reserve high water, append once, and restore the global size bound."""

    _verify_directories(directories)
    _cleanup_pending_files(directories)
    snapshot = _scan_store(directories, allow_over_capacity=True)
    if snapshot.total_jsonl_bytes > AGENT_EXCHANGE_TOTAL_MAX_BYTES:
        _prune_to_total(directories, snapshot)
        snapshot = _scan_store(directories)
    if snapshot.workspace_high_water >= _MAX_SEQUENCE:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)

    workspace_sequence = snapshot.workspace_high_water + 1
    _atomic_replace_leaf(
        directories.store_descriptor,
        filename=_GLOBAL_STATE_FILENAME,
        pending_filename=_GLOBAL_PENDING_FILENAME,
        payload=_global_state_payload(workspace_sequence),
        maximum_size=_STATE_MAX_BYTES,
    )

    session_descriptor = _open_session_directory(
        directories,
        record.session_id,
        create=True,
    )
    if session_descriptor is None:
        raise _StorageFault(AgentExchangeFailure.IO_FAILED)
    try:
        prior_stream = _find_stream(
            snapshot,
            session_id=record.session_id,
            agent_id=record.agent_id,
        )
        prior_high_water = 0 if prior_stream is None else prior_stream.high_water
        if prior_high_water >= _MAX_SEQUENCE:
            raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
        sequence = prior_high_water + 1
        _atomic_replace_leaf(
            session_descriptor,
            filename=_stream_state_filename(record.agent_id),
            pending_filename=_stream_state_pending_filename(record.agent_id),
            payload=_stream_state_payload(sequence),
            maximum_size=_STATE_MAX_BYTES,
        )

        full_content = record.content.encode("utf-8", errors="strict")
        retained_content = _complete_utf8_prefix(full_content)
        retained_text = retained_content.decode("utf-8", errors="strict")
        content_truncated = len(retained_content) != len(full_content)
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        digest = f"sha256:{hashlib.sha256(full_content).hexdigest()}"
        durable_record: dict[str, object] = {
            "schema_version": AGENT_EXCHANGE_SCHEMA_VERSION,
            "workspace_sequence": workspace_sequence,
            "sequence": sequence,
            "timestamp": timestamp,
            "request_type": record.request_type.value,
            "session_id": record.session_id,
            "scope_kind": record.scope_kind.value,
            "scope_id": record.scope_id,
            "agent_id": record.agent_id,
            "provider": record.provider.value,
            "exchange_id": record.exchange_id,
            "content": retained_text,
            "content_full_utf8_bytes": len(full_content),
            "content_retained_utf8_bytes": len(retained_content),
            "content_full_characters": len(record.content),
            "content_retained_characters": len(retained_text),
            "content_truncated": content_truncated,
            "content_sha256": digest,
        }
        payload = _canonical_json_line(durable_record)
        if len(payload) > _RECORD_MAX_BYTES or len(payload) > _SEGMENT_MAX_BYTES:
            raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)

        active_name = _active_segment_filename(record.agent_id)
        active = _stat_leaf(
            session_descriptor,
            active_name,
            maximum_size=_SEGMENT_MAX_BYTES,
            required_mode=0o600,
        )
        if active is not None and active.size + len(payload) > _SEGMENT_MAX_BYTES:
            _rotate_stream(session_descriptor, agent_id=record.agent_id)
        _append_segment_line(
            session_descriptor,
            active_name,
            payload,
        )
    finally:
        os.close(session_descriptor)

    after_append = _scan_store(directories, allow_over_capacity=True)
    _prune_to_total(directories, after_append)
    final_snapshot = _scan_store(directories)
    if not any(
        item.workspace_sequence == workspace_sequence
        and item.sequence == sequence
        and item.session_id == record.session_id
        and item.agent_id == record.agent_id
        for item in final_snapshot.records
    ):
        raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return AgentExchangeAppendReceipt(
        session_id=record.session_id,
        agent_id=record.agent_id,
        exchange_id=record.exchange_id,
        workspace_sequence=workspace_sequence,
        sequence=sequence,
        timestamp=timestamp,
        content_full_utf8_bytes=len(full_content),
        content_retained_utf8_bytes=len(retained_content),
        content_full_characters=len(record.content),
        content_retained_characters=len(retained_text),
        content_truncated=content_truncated,
        content_sha256=digest,
    )


def _rotate_stream(parent_descriptor: int, *, agent_id: str) -> None:
    """Rotate one validated active stream into seven bounded backups."""

    oldest = _backup_segment_filename(agent_id, _SEGMENT_BACKUP_COUNT)
    oldest_snapshot = _stat_leaf(
        parent_descriptor,
        oldest,
        maximum_size=_SEGMENT_MAX_BYTES,
        required_mode=0o600,
    )
    if oldest_snapshot is not None:
        _read_leaf(parent_descriptor, oldest, maximum_size=_SEGMENT_MAX_BYTES)
        _unlink_managed_leaf(
            parent_descriptor,
            oldest,
            maximum_size=_SEGMENT_MAX_BYTES,
        )
    for index in range(_SEGMENT_BACKUP_COUNT - 1, 0, -1):
        source = _backup_segment_filename(agent_id, index)
        if (
            _stat_leaf(
                parent_descriptor,
                source,
                maximum_size=_SEGMENT_MAX_BYTES,
                required_mode=0o600,
            )
            is not None
        ):
            _rename_managed_leaf(
                parent_descriptor,
                source=source,
                destination=_backup_segment_filename(agent_id, index + 1),
            )
    active = _active_segment_filename(agent_id)
    if (
        _stat_leaf(
            parent_descriptor,
            active,
            maximum_size=_SEGMENT_MAX_BYTES,
            required_mode=0o600,
        )
        is not None
    ):
        _rename_managed_leaf(
            parent_descriptor,
            source=active,
            destination=_backup_segment_filename(agent_id, 1),
        )
    os.fsync(parent_descriptor)


def _rename_managed_leaf(
    parent_descriptor: int,
    *,
    source: str,
    destination: str,
) -> None:
    """Rename one verified segment only into a currently absent managed name."""

    expected = _stat_leaf(
        parent_descriptor,
        source,
        maximum_size=_SEGMENT_MAX_BYTES,
        required_mode=0o600,
    )
    if expected is None:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    if (
        _stat_leaf(
            parent_descriptor,
            destination,
            maximum_size=_SEGMENT_MAX_BYTES,
            required_mode=0o600,
        )
        is not None
    ):
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    os.rename(
        source,
        destination,
        src_dir_fd=parent_descriptor,
        dst_dir_fd=parent_descriptor,
    )
    current = _stat_leaf(
        parent_descriptor,
        destination,
        maximum_size=_SEGMENT_MAX_BYTES,
        required_mode=0o600,
    )
    if current is None or current.identity != expected.identity:
        raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)


def _append_segment_line(
    parent_descriptor: int,
    filename: str,
    payload: bytes,
) -> None:
    """Append one complete line and roll a partial suffix back on failure."""

    existing = _stat_leaf(
        parent_descriptor,
        filename,
        maximum_size=_SEGMENT_MAX_BYTES,
        required_mode=0o600,
    )
    flags = os.O_WRONLY | os.O_APPEND
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
    ):
        flags |= getattr(os, flag_name, 0)
    created = existing is None
    if created:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(filename, flags, 0o600, dir_fd=parent_descriptor)
    identity: tuple[int, int] | None = None
    start_size = 0
    body_failure: BaseException | None = None
    try:
        _set_not_inheritable(descriptor)
        if created:
            os.fchmod(descriptor, 0o600)
        opened = _verify_open_leaf(
            parent_descriptor,
            descriptor,
            filename,
            maximum_size=_SEGMENT_MAX_BYTES,
            required_mode=0o600,
        )
        if existing is not None:
            _require_same_snapshot(opened, existing)
        identity = opened.identity
        start_size = opened.size
        if start_size + len(payload) > _SEGMENT_MAX_BYTES:
            raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        completed = _verify_open_leaf(
            parent_descriptor,
            descriptor,
            filename,
            maximum_size=_SEGMENT_MAX_BYTES,
            required_mode=0o600,
        )
        if completed.identity != identity or completed.size != start_size + len(
            payload
        ):
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
    except BaseException as exc:
        body_failure = exc
        try:
            if identity is not None:
                current = os.fstat(descriptor)
                if _meaningful_identity(current) != identity:
                    raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
                os.ftruncate(descriptor, start_size)
                os.fsync(descriptor)
        except BaseException as rollback_failure:
            body_failure = rollback_failure
    try:
        os.close(descriptor)
    except BaseException as exc:
        if body_failure is None:
            body_failure = exc
    if body_failure is not None:
        if created and identity is not None:
            _discard_matching_pending(
                parent_descriptor,
                filename,
                identity,
                maximum_size=_SEGMENT_MAX_BYTES,
            )
        raise body_failure
    if created:
        os.fsync(parent_descriptor)


def _prune_to_total(
    directories: _StoreDirectories,
    snapshot: _StoreSnapshot,
) -> None:
    """Retain the newest complete records within the global JSONL byte cap."""

    if snapshot.total_jsonl_bytes <= AGENT_EXCHANGE_TOTAL_MAX_BYTES:
        return
    excess = snapshot.total_jsonl_bytes - AGENT_EXCHANGE_TOTAL_MAX_BYTES
    removed = 0
    removal_sequences: set[int] = set()
    for record in sorted(snapshot.records, key=lambda item: item.workspace_sequence):
        removal_sequences.add(record.workspace_sequence)
        removed += len(record.payload)
        if removed >= excess:
            break
    if not removal_sequences:
        raise _StorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)

    for stream in snapshot.streams:
        by_filename: dict[str, list[_StoredRecord]] = {}
        for record in stream.records:
            by_filename.setdefault(record.filename, []).append(record)
        affected = {
            filename: records
            for filename, records in by_filename.items()
            if any(item.workspace_sequence in removal_sequences for item in records)
        }
        if not affected:
            continue
        session_descriptor = _open_session_directory(
            directories,
            stream.session_id,
            create=False,
        )
        if session_descriptor is None:
            raise _StorageFault(AgentExchangeFailure.UNSAFE_STORAGE)
        try:
            for filename, records in affected.items():
                removed_prefix = [
                    item
                    for item in records
                    if item.workspace_sequence in removal_sequences
                ]
                if records[: len(removed_prefix)] != removed_prefix:
                    raise _StorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
                retained = records[len(removed_prefix) :]
                if retained:
                    _atomic_replace_leaf(
                        session_descriptor,
                        filename=filename,
                        pending_filename=_segment_pending_filename(stream.agent_id),
                        payload=b"".join(item.payload for item in retained),
                        maximum_size=_SEGMENT_MAX_BYTES,
                    )
                else:
                    _unlink_managed_leaf(
                        session_descriptor,
                        filename,
                        maximum_size=_SEGMENT_MAX_BYTES,
                    )
                    os.fsync(session_descriptor)
        finally:
            os.close(session_descriptor)


def _project_inventory(
    snapshot: _StoreSnapshot,
    *,
    session_id: str,
) -> tuple[AgentExchangeInventoryItem, ...]:
    """Group one session's records by exact scope and canonical agent."""

    grouped: dict[
        tuple[AgentExchangeScopeKind, str, str],
        list[_StoredRecord],
    ] = {}
    for record in snapshot.records:
        if record.session_id != session_id:
            continue
        key = (record.scope_kind, record.scope_id, record.agent_id)
        grouped.setdefault(key, []).append(record)
    items: list[AgentExchangeInventoryItem] = []
    for (scope_kind, scope_id, agent_id), records in grouped.items():
        ordered = sorted(records, key=lambda item: item.sequence)
        items.append(
            AgentExchangeInventoryItem(
                selection=AgentExchangeSelection(
                    session_id=session_id,
                    scope_kind=scope_kind,
                    scope_id=scope_id,
                    agent_id=agent_id,
                ),
                record_count=len(ordered),
                first_workspace_sequence=ordered[0].workspace_sequence,
                last_workspace_sequence=ordered[-1].workspace_sequence,
                first_sequence=ordered[0].sequence,
                last_sequence=ordered[-1].sequence,
            )
        )
    items.sort(key=lambda item: item.first_workspace_sequence)
    return tuple(items)


def _project_snapshot(
    snapshot: _StoreSnapshot,
    *,
    selection: AgentExchangeSelection,
) -> AgentExchangeSnapshot:
    """Project one exact selection without exposing writable storage handles."""

    records = sorted(
        (
            record
            for record in snapshot.records
            if record.session_id == selection.session_id
            and record.agent_id == selection.agent_id
            and record.scope_kind is selection.scope_kind
            and record.scope_id == selection.scope_id
        ),
        key=lambda item: item.sequence,
    )
    if not records:
        raise _StorageFault(AgentExchangeFailure.NOT_FOUND)
    return AgentExchangeSnapshot(
        selection=selection,
        jsonl_bytes=b"".join(item.payload for item in records),
        record_count=len(records),
        first_workspace_sequence=records[0].workspace_sequence,
        last_workspace_sequence=records[-1].workspace_sequence,
        first_sequence=records[0].sequence,
        last_sequence=records[-1].sequence,
    )


__all__ = ["WorkspaceAgentExchangeStore"]
