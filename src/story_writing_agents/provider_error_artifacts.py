"""Bounded workspace storage for rejected provider response artifacts."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import importlib
import json
import os
import re
import stat
import sys
import threading
import time
import unicodedata
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, Protocol, cast
from uuid import uuid4

from .agents import ROSTER
from .workspace import (
    Workspace,
    open_workspace_root_directory,
    verify_workspace_root_directory,
)


def _optional_platform_module(name: str) -> ModuleType | None:
    """Import one platform lock module when the current host provides it."""

    try:
        return importlib.import_module(name)
    except ImportError:  # pragma: no cover - availability is platform-specific.
        return None


_fcntl = _optional_platform_module("fcntl")
_msvcrt = _optional_platform_module("msvcrt")


PROVIDER_ERROR_ARTIFACT_DIRECTORY = "debug_logs/provider_error_responses"
PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH = (
    f"{PROVIDER_ERROR_ARTIFACT_DIRECTORY}/.lock"
)
PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH = (
    f"{PROVIDER_ERROR_ARTIFACT_DIRECTORY}/.pending"
)
PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES = 262_144
PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES = 2_097_152
PROVIDER_ERROR_ARTIFACT_MAX_FILES = 32
PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES = 16_777_216
PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS = 0.1
PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY = "structured_response_rejected"

__all__ = [
    "PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES",
    "PROVIDER_ERROR_ARTIFACT_DIRECTORY",
    "PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY",
    "PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES",
    "PROVIDER_ERROR_ARTIFACT_GUARD_RELATIVE_PATH",
    "PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS",
    "PROVIDER_ERROR_ARTIFACT_MAX_FILES",
    "PROVIDER_ERROR_ARTIFACT_PENDING_RELATIVE_PATH",
    "PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES",
    "WorkspaceProviderErrorArtifacts",
]

_SCHEMA_VERSION = 1
_DEBUG_DIRECTORY_NAME = "debug_logs"
_ARTIFACT_DIRECTORY_NAME = "provider_error_responses"
_GUARD_FILENAME = ".lock"
_PENDING_FILENAME = ".pending"
_LOCK_RETRY_SECONDS = 0.01
_ARTIFACT_FILENAME = re.compile(r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\.json")
_ARTIFACT_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"\.[0-9]{3}\+00:00"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROVIDERS = frozenset({"codex", "antigravity"})
_FINAL_NAME_ATTEMPTS = 16
_BASE_SCHEMA_FIELDS = frozenset(
    {
        "schema_version",
        "timestamp",
        "provider",
        "agent_id",
        "session_id",
        "failure_category",
        "response_utf8_bytes",
        "retained_utf8_bytes",
        "response_sha256",
        "truncated",
        "body",
    }
)


class _NativeRenameFunction(Protocol):
    """Typed surface of the selected libc no-replace rename function."""

    def __call__(
        self,
        source_directory: int,
        source: bytes,
        destination_directory: int,
        destination: bytes,
        flags: int,
        /,
    ) -> int: ...


class _WorkspaceProcessLock:
    """Weakly retained in-process serialization for one workspace store."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class _ArtifactFile:
    """One validated complete artifact snapshot."""

    name: str
    identity: tuple[int, int]
    size: int
    modified_ns: int
    changed_ns: int


class _ArtifactDirectories:
    """Held root, debug, and artifact directory descriptors."""

    __slots__ = (
        "artifact_descriptor",
        "debug_descriptor",
        "root_descriptor",
        "workspace",
    )

    def __init__(
        self,
        workspace: Workspace,
        root_descriptor: int,
        debug_descriptor: int,
        artifact_descriptor: int,
    ) -> None:
        self.workspace = workspace
        self.root_descriptor = root_descriptor
        self.debug_descriptor = debug_descriptor
        self.artifact_descriptor = artifact_descriptor


_PROCESS_LOCK_REGISTRY_GUARD = threading.Lock()
_PROCESS_LOCKS: weakref.WeakValueDictionary[str, _WorkspaceProcessLock] = (
    weakref.WeakValueDictionary()
)


class WorkspaceProviderErrorArtifacts:
    """Persist rejected provider responses beneath one verified workspace.

    Import and construction perform no filesystem work. Invalid inputs and
    unsafe or unsupported storage raise; bounded lock contention drops only the
    attempted artifact. The caller owns any fail-open policy.
    """

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Workspace) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        self._workspace = workspace

    @property
    def directory(self) -> Path:
        """Return the fixed artifact directory without filesystem access."""

        return self._workspace.root / PROVIDER_ERROR_ARTIFACT_DIRECTORY

    def record_rejected_response(
        self,
        *,
        provider: str,
        agent_id: str,
        session_id: str,
        response_text: str,
        failure_category: str,
        debate_id: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """Store one rejected response under the fixed bounded schema.

        The body is provider-controlled plaintext. It is retained exactly when
        it fits the body bound, or as the longest complete UTF-8 prefix when it
        does not. The digest and original byte count always describe the full
        response.
        """

        record, timestamp = self._build_record(
            provider=provider,
            agent_id=agent_id,
            session_id=session_id,
            response_text=response_text,
            failure_category=failure_category,
            debate_id=debate_id,
            conversation_id=conversation_id,
        )
        payload = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8", errors="strict")
            + b"\n"
        )
        if len(payload) > PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES:
            raise ValueError("provider error artifact exceeds its serialized bound")
        self._store(payload, timestamp=timestamp)

    def _build_record(
        self,
        *,
        provider: str,
        agent_id: str,
        session_id: str,
        response_text: str,
        failure_category: str,
        debate_id: str | None,
        conversation_id: str | None,
    ) -> tuple[dict[str, object], datetime]:
        """Validate inputs and return one complete schema-v1 record."""

        if type(provider) is not str or provider not in _PROVIDERS:
            raise ValueError("provider must be codex or antigravity")
        if type(agent_id) is not str:
            raise TypeError("agent_id must be text")
        validated_agent = self._workspace.validate_identifier(
            agent_id,
            label="agent ID",
        )
        if validated_agent not in ROSTER:
            raise ValueError("agent_id must be canonical")
        if type(session_id) is not str:
            raise TypeError("session_id must be text")
        validated_session = self._workspace.validate_identifier(session_id)
        if debate_id is not None and conversation_id is not None:
            raise ValueError(
                "provider error artifact cannot identify both a debate and conversation"
            )
        correlations: dict[str, str] = {}
        for field, value, label in (
            ("debate_id", debate_id, "debate ID"),
            ("conversation_id", conversation_id, "conversation ID"),
        ):
            if value is not None:
                if type(value) is not str:
                    raise TypeError(f"{field} must be text or None")
                correlations[field] = self._workspace.validate_identifier(
                    value,
                    label=label,
                )
        if (
            type(failure_category) is not str
            or failure_category != PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY
        ):
            raise ValueError("failure_category is unsupported")
        if type(response_text) is not str:
            raise TypeError("response_text must be text")

        complete = response_text.encode("utf-8", errors="strict")
        retained = _complete_utf8_prefix(
            complete,
            maximum=PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES,
        )
        timestamp = datetime.now(UTC)
        record: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "timestamp": timestamp.isoformat(timespec="milliseconds"),
            "provider": provider,
            "agent_id": validated_agent,
            "session_id": validated_session,
            "failure_category": PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY,
            "response_utf8_bytes": len(complete),
            "retained_utf8_bytes": len(retained),
            "response_sha256": hashlib.sha256(complete).hexdigest(),
            "truncated": len(retained) != len(complete),
            "body": retained.decode("utf-8", errors="strict"),
        }
        record.update(correlations)
        return record, timestamp

    def _store(self, payload: bytes, *, timestamp: datetime) -> None:
        """Publish one artifact while holding bounded process and OS guards."""

        process_lock = _process_lock_for(self._workspace)
        deadline = time.monotonic() + PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS
        process_lock_acquired = False
        directories: _ArtifactDirectories | None = None
        guard_descriptor: int | None = None
        locked = False
        body_failure: _ExceptionState | None = None
        try:
            process_lock_acquired = process_lock.lock.acquire(
                timeout=PROVIDER_ERROR_ARTIFACT_LOCK_TIMEOUT_SECONDS
            )
            if not process_lock_acquired:
                return
            directories = _prepare_directories(self._workspace)
            _scan_artifact_directory(directories)
            guard_descriptor = _open_guard(directories)
            locked = _acquire_advisory_lock(
                guard_descriptor,
                timeout_seconds=max(0.0, deadline - time.monotonic()),
            )
            if locked:
                _verify_open_file(
                    guard_descriptor,
                    directories,
                    _GUARD_FILENAME,
                    maximum_size=1,
                )
                _publish_under_guard(
                    directories,
                    guard_descriptor,
                    payload,
                    timestamp=timestamp,
                )
        except BaseException as failure:
            body_failure = (failure, failure.__traceback__)

        cleanup_control: _ExceptionState | None = None
        if locked and guard_descriptor is not None:
            cleanup_control = _cleanup_advisory_lock(
                guard_descriptor,
                cleanup_control,
            )
        if guard_descriptor is not None:
            cleanup_control = _cleanup_descriptor(
                guard_descriptor,
                cleanup_control,
            )
        if directories is not None:
            cleanup_control = _cleanup_descriptor(
                directories.artifact_descriptor,
                cleanup_control,
            )
            cleanup_control = _cleanup_descriptor(
                directories.debug_descriptor,
                cleanup_control,
            )
            cleanup_control = _cleanup_descriptor(
                directories.root_descriptor,
                cleanup_control,
            )
        if process_lock_acquired:
            cleanup_control = _cleanup_process_lock(
                process_lock,
                cleanup_control,
            )
        _raise_after_cleanup(body_failure, cleanup_control)


def _complete_utf8_prefix(value: bytes, *, maximum: int) -> bytes:
    """Return the longest strict-UTF-8 prefix within ``maximum`` bytes."""

    if len(value) <= maximum:
        return value
    prefix = value[:maximum]
    try:
        prefix.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        prefix = prefix[: exc.start]
    prefix.decode("utf-8", errors="strict")
    return prefix


def _process_lock_for(workspace: Workspace) -> _WorkspaceProcessLock:
    """Return the process lock for one resolved workspace root."""

    key = str(workspace.root)
    with _PROCESS_LOCK_REGISTRY_GUARD:
        retained = _PROCESS_LOCKS.get(key)
        if retained is None:
            retained = _WorkspaceProcessLock()
            _PROCESS_LOCKS[key] = retained
        return retained


_ExceptionState = tuple[BaseException, TracebackType | None]


def _remember_cleanup_failure(
    retained: _ExceptionState | None,
    failure: BaseException,
) -> _ExceptionState | None:
    """Retain the first cleanup failure without replacing an earlier one."""

    if retained is not None:
        return retained
    return failure, failure.__traceback__


def _cleanup_descriptor(
    descriptor: int,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Close one descriptor and retain the first cleanup failure."""

    try:
        os.close(descriptor)
    except BaseException as failure:
        retained = _remember_cleanup_failure(retained, failure)
    return retained


def _cleanup_advisory_lock(
    descriptor: int,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Release one advisory lock and retain the first cleanup failure."""

    try:
        _release_advisory_lock(descriptor)
    except BaseException as failure:
        retained = _remember_cleanup_failure(retained, failure)
    return retained


def _cleanup_process_lock(
    process_lock: _WorkspaceProcessLock,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Release the process lock and attempt state-checked recovery once."""

    release_failed = False
    try:
        process_lock.lock.release()
    except BaseException as failure:
        release_failed = True
        retained = _remember_cleanup_failure(retained, failure)
    if not release_failed:
        return retained
    try:
        still_locked = process_lock.lock.locked()
    except BaseException as failure:
        return _remember_cleanup_failure(retained, failure)
    if not still_locked:
        return retained
    try:
        process_lock.lock.release()
    except BaseException as failure:
        retained = _remember_cleanup_failure(retained, failure)
    return retained


def _raise_after_cleanup(
    body_failure: _ExceptionState | None,
    cleanup_control: _ExceptionState | None,
) -> None:
    """Raise the original body failure or the first cleanup failure."""

    retained = body_failure or cleanup_control
    if retained is not None:
        failure, traceback = retained
        raise failure.with_traceback(traceback)


def _prepare_directories(workspace: Workspace) -> _ArtifactDirectories:
    """Create and hold the fixed directories beneath a verified root."""

    _require_safe_primitives()
    root_descriptor = open_workspace_root_directory(workspace)
    debug_descriptor: int | None = None
    artifact_descriptor: int | None = None
    try:
        verify_workspace_root_directory(workspace, root_descriptor)
        _mkdir_if_missing(root_descriptor, _DEBUG_DIRECTORY_NAME)
        debug_descriptor = _open_directory(root_descriptor, _DEBUG_DIRECTORY_NAME)
        _require_exact_child_name(root_descriptor, _DEBUG_DIRECTORY_NAME)
        _verify_named_directory(
            parent_descriptor=root_descriptor,
            descriptor=debug_descriptor,
            name=_DEBUG_DIRECTORY_NAME,
            label="debug directory",
        )
        _mkdir_if_missing(debug_descriptor, _ARTIFACT_DIRECTORY_NAME)
        artifact_descriptor = _open_directory(
            debug_descriptor,
            _ARTIFACT_DIRECTORY_NAME,
        )
        _require_exact_child_name(debug_descriptor, _ARTIFACT_DIRECTORY_NAME)
        held = _ArtifactDirectories(
            workspace,
            root_descriptor,
            debug_descriptor,
            artifact_descriptor,
        )
        _verify_directories(held)
        return held
    except BaseException as failure:
        body_failure: _ExceptionState = (failure, failure.__traceback__)
        cleanup_control: _ExceptionState | None = None
        if artifact_descriptor is not None:
            cleanup_control = _cleanup_descriptor(
                artifact_descriptor,
                cleanup_control,
            )
        if debug_descriptor is not None:
            cleanup_control = _cleanup_descriptor(
                debug_descriptor,
                cleanup_control,
            )
        cleanup_control = _cleanup_descriptor(
            root_descriptor,
            cleanup_control,
        )
        _raise_after_cleanup(body_failure, cleanup_control)
        raise AssertionError("unreachable")


def _require_safe_primitives() -> None:
    """Fail closed when descriptor-relative storage cannot be enforced."""

    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    supports_fd = getattr(os, "supports_fd", ())
    required_dir_fd = (os.mkdir, os.open, os.stat, os.unlink)
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or any(function not in supports_dir_fd for function in required_dir_fd)
        or os.stat not in supports_follow_symlinks
        or os.listdir not in supports_fd
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe provider error artifact storage is unsupported",
        )
    _native_rename_function()


def _native_rename_function() -> tuple[_NativeRenameFunction, int]:
    """Return one supported atomic no-replace rename function and flag."""

    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        function = cast(_NativeRenameFunction, library.renameatx_np)
        flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = cast(_NativeRenameFunction, library.renameat2)
        flag = 0x00000001  # RENAME_NOREPLACE
    else:
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "atomic no-replace provider error artifact rename is unavailable",
        )
    native_attributes = cast(Any, function)
    native_attributes.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    native_attributes.restype = ctypes.c_int
    ctypes.set_errno(0)
    probe = function(-1, b".", -1, b".", flag)
    probe_error = ctypes.get_errno()
    unsupported_errors = {
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.ENOSYS),
        getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
    }
    if probe != -1 or probe_error in unsupported_errors:
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "atomic no-replace provider error artifact rename is unavailable",
        )
    return function, flag


def _rename_noreplace(
    source_directory: int,
    source: str,
    destination_directory: int,
    destination: str,
) -> None:
    """Atomically move one name only when the destination is absent."""

    function, flag = _native_rename_function()
    ctypes.set_errno(0)
    result = function(
        source_directory,
        os.fsencode(source),
        destination_directory,
        os.fsencode(destination),
        flag,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            os.strerror(error_number),
            destination,
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        destination,
    )


def _mkdir_if_missing(parent_descriptor: int, name: str) -> None:
    """Create one fixed child directory without weakening an existing name."""

    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass


def _open_directory(parent_descriptor: int, name: str) -> int:
    """Open one fixed child directory without following its name."""

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
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        return descriptor
    except BaseException as failure:
        body_failure: _ExceptionState = (failure, failure.__traceback__)
        cleanup_control = _cleanup_descriptor(descriptor, None)
        _raise_after_cleanup(body_failure, cleanup_control)
        raise AssertionError("unreachable")


def _require_exact_child_name(parent_descriptor: int, expected: str) -> None:
    """Reject normalized or case-folded aliases of one fixed child name."""

    target = unicodedata.normalize("NFKC", expected).casefold()
    matching = [
        name
        for name in os.listdir(parent_descriptor)
        if unicodedata.normalize("NFKC", name).casefold() == target
    ]
    if matching != [expected]:
        raise OSError(f"{expected} directory name is unsafe")


def _verify_named_directory(
    *,
    parent_descriptor: int,
    descriptor: int,
    name: str,
    label: str,
) -> None:
    """Require one held directory and its name to retain an identity."""

    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise OSError(f"{label} is not a directory")
    if _meaningful_identity(opened, label=label) != _meaningful_identity(
        named,
        label=label,
    ):
        raise OSError(f"{label} identity changed")


def _verify_directories(directories: _ArtifactDirectories) -> None:
    """Reverify root, debug, artifact, and their exact named identities."""

    verify_workspace_root_directory(
        directories.workspace,
        directories.root_descriptor,
    )
    _require_exact_child_name(
        directories.root_descriptor,
        _DEBUG_DIRECTORY_NAME,
    )
    _verify_named_directory(
        parent_descriptor=directories.root_descriptor,
        descriptor=directories.debug_descriptor,
        name=_DEBUG_DIRECTORY_NAME,
        label="debug directory",
    )
    _require_exact_child_name(
        directories.debug_descriptor,
        _ARTIFACT_DIRECTORY_NAME,
    )
    _verify_named_directory(
        parent_descriptor=directories.debug_descriptor,
        descriptor=directories.artifact_descriptor,
        name=_ARTIFACT_DIRECTORY_NAME,
        label="provider error artifact directory",
    )


def _open_guard(directories: _ArtifactDirectories) -> int:
    """Open or exclusively create the verified advisory guard."""

    _verify_directories(directories)
    existing = _stat_optional(
        directories,
        _GUARD_FILENAME,
        maximum_size=1,
    )
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
    descriptor = os.open(
        _GUARD_FILENAME,
        flags,
        0o600,
        dir_fd=directories.artifact_descriptor,
    )
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        _verify_open_file(
            descriptor,
            directories,
            _GUARD_FILENAME,
            maximum_size=1,
        )
        if _fcntl is None and _msvcrt is not None:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException as failure:
        body_failure: _ExceptionState = (failure, failure.__traceback__)
        cleanup_control = _cleanup_descriptor(descriptor, None)
        _raise_after_cleanup(body_failure, cleanup_control)
        raise AssertionError("unreachable")


def _scan_artifact_directory(
    directories: _ArtifactDirectories,
) -> tuple[list[_ArtifactFile], os.stat_result | None]:
    """Validate the exact namespace and return complete and pending state."""

    _verify_directories(directories)
    artifacts: list[_ArtifactFile] = []
    pending: os.stat_result | None = None
    for name in os.listdir(directories.artifact_descriptor):
        if name == _GUARD_FILENAME:
            _stat_optional(directories, name, maximum_size=1)
            continue
        if name == _PENDING_FILENAME:
            pending = _stat_optional(
                directories,
                name,
                maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
            )
            continue
        if _ARTIFACT_FILENAME.fullmatch(name) is None:
            raise OSError("provider error artifact directory contains an unsafe name")
        current = _stat_optional(
            directories,
            name,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        assert current is not None
        _read_validated_artifact(directories, name, expected=current)
        artifacts.append(
            _ArtifactFile(
                name=name,
                identity=_meaningful_identity(
                    current,
                    label="provider error artifact",
                ),
                size=current.st_size,
                modified_ns=current.st_mtime_ns,
                changed_ns=current.st_ctime_ns,
            )
        )
    artifacts.sort(key=lambda item: item.name)
    return artifacts, pending


def _stat_optional(
    directories: _ArtifactDirectories,
    filename: str,
    *,
    maximum_size: int,
) -> os.stat_result | None:
    """Return one no-follow regular single-link leaf snapshot when present."""

    _verify_directories(directories)
    try:
        current = os.stat(
            filename,
            dir_fd=directories.artifact_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(current.st_mode):
        raise OSError("provider error artifact leaf is not regular")
    if current.st_nlink != 1:
        raise OSError("provider error artifact leaf link count is unsafe")
    if current.st_size < 0 or current.st_size > maximum_size:
        raise OSError("provider error artifact leaf size is unsafe")
    _meaningful_identity(current, label="provider error artifact leaf")
    return current


def _verify_open_file(
    descriptor: int,
    directories: _ArtifactDirectories,
    filename: str,
    *,
    maximum_size: int,
) -> os.stat_result:
    """Require an opened leaf and its name to retain a safe identity."""

    _verify_directories(directories)
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise OSError("opened provider error artifact leaf is unsafe")
    if opened.st_size < 0 or opened.st_size > maximum_size:
        raise OSError("opened provider error artifact leaf size is unsafe")
    named = _stat_optional(
        directories,
        filename,
        maximum_size=maximum_size,
    )
    if named is None:
        raise OSError("opened provider error artifact leaf name disappeared")
    opened_identity = _meaningful_identity(
        opened,
        label="opened provider error artifact leaf",
    )
    named_identity = _meaningful_identity(
        named,
        label="named provider error artifact leaf",
    )
    if opened_identity != named_identity:
        raise OSError("provider error artifact leaf identity changed")
    return opened


def _read_validated_artifact(
    directories: _ArtifactDirectories,
    filename: str,
    *,
    expected: os.stat_result,
) -> bytes:
    """Read one stable complete artifact and validate its exact schema."""

    flags = os.O_RDONLY
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, flag_name, 0)
    _verify_directories(directories)
    descriptor = os.open(
        filename,
        flags,
        dir_fd=directories.artifact_descriptor,
    )
    body_failure: _ExceptionState | None = None
    payload = b""
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        opened = _verify_open_file(
            descriptor,
            directories,
            filename,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        _require_stat_snapshot(opened, expected)
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise OSError("provider error artifact ended before its stated size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("provider error artifact grew while it was read")
        payload = b"".join(chunks)
        final = _verify_open_file(
            descriptor,
            directories,
            filename,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        _require_stat_snapshot(final, opened)
    except BaseException as failure:
        body_failure = (failure, failure.__traceback__)
    cleanup_failure = _cleanup_descriptor(descriptor, None)
    _raise_after_cleanup(body_failure, cleanup_failure)
    _validate_artifact_payload(
        directories.workspace,
        filename=filename,
        payload=payload,
    )
    return payload


def _require_stat_snapshot(
    current: os.stat_result,
    expected: os.stat_result,
) -> None:
    """Require content-relevant file facts to remain exact."""

    if (
        _meaningful_identity(current, label="provider error artifact")
        != _meaningful_identity(expected, label="expected provider error artifact")
        or current.st_size != expected.st_size
        or current.st_mtime_ns != expected.st_mtime_ns
        or current.st_ctime_ns != expected.st_ctime_ns
    ):
        raise OSError("provider error artifact changed while inspected")


def _validate_artifact_payload(
    workspace: Workspace,
    *,
    filename: str,
    payload: bytes,
) -> None:
    """Require canonical strict-UTF-8 JSON and every schema-v1 invariant."""

    text = payload.decode("utf-8", errors="strict")
    value = json.loads(text, object_pairs_hook=_reject_duplicate_fields)
    if type(value) is not dict:
        raise ValueError("provider error artifact must contain one JSON object")
    fields = frozenset(value)
    correlation_fields = fields.difference(_BASE_SCHEMA_FIELDS)
    if correlation_fields not in {
        frozenset(),
        frozenset({"debate_id"}),
        frozenset({"conversation_id"}),
    }:
        raise ValueError("provider error artifact fields are invalid")
    if fields != _BASE_SCHEMA_FIELDS.union(correlation_fields):
        raise ValueError("provider error artifact fields are invalid")

    if type(value["schema_version"]) is not int or (
        value["schema_version"] != _SCHEMA_VERSION
    ):
        raise ValueError("provider error artifact schema version is invalid")
    timestamp = value["timestamp"]
    if type(timestamp) is not str or _ARTIFACT_TIMESTAMP.fullmatch(timestamp) is None:
        raise ValueError("provider error artifact timestamp is invalid")
    if datetime.fromisoformat(timestamp).utcoffset() != UTC.utcoffset(None):
        raise ValueError("provider error artifact timestamp is not UTC")
    filename_timestamp = datetime.strptime(
        filename.split("-", 1)[0],
        "%Y%m%dT%H%M%S%fZ",
    ).replace(tzinfo=UTC)
    if filename_timestamp.isoformat(timespec="milliseconds") != timestamp:
        raise ValueError("provider error artifact timestamp does not match its name")
    provider = value["provider"]
    if type(provider) is not str or provider not in _PROVIDERS:
        raise ValueError("provider error artifact provider is invalid")
    agent_id = value["agent_id"]
    if type(agent_id) is not str:
        raise TypeError("provider error artifact agent_id must be text")
    if workspace.validate_identifier(agent_id, label="agent ID") not in ROSTER:
        raise ValueError("provider error artifact agent_id is not canonical")
    session_id = value["session_id"]
    if type(session_id) is not str:
        raise TypeError("provider error artifact session_id must be text")
    workspace.validate_identifier(session_id)
    for field in correlation_fields:
        correlation = value[field]
        if type(correlation) is not str:
            raise TypeError(f"provider error artifact {field} must be text")
        workspace.validate_identifier(
            correlation,
            label="debate ID" if field == "debate_id" else "conversation ID",
        )
    if value["failure_category"] != PROVIDER_ERROR_ARTIFACT_FAILURE_CATEGORY:
        raise ValueError("provider error artifact failure category is invalid")

    response_size = value["response_utf8_bytes"]
    retained_size = value["retained_utf8_bytes"]
    if (
        type(response_size) is not int
        or response_size < 0
        or type(retained_size) is not int
        or retained_size < 0
    ):
        raise ValueError("provider error artifact byte counts are invalid")
    digest = value["response_sha256"]
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ValueError("provider error artifact digest is invalid")
    if type(value["truncated"]) is not bool:
        raise TypeError("provider error artifact truncated flag is invalid")
    body = value["body"]
    if type(body) is not str:
        raise TypeError("provider error artifact body must be text")
    body_bytes = body.encode("utf-8", errors="strict")
    if len(body_bytes) != retained_size or (
        retained_size > PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES
    ):
        raise ValueError("provider error artifact retained byte count is invalid")
    if value["truncated"]:
        if (
            response_size <= PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES
            or response_size <= retained_size
            or retained_size < PROVIDER_ERROR_ARTIFACT_BODY_MAX_BYTES - 3
        ):
            raise ValueError("provider error artifact truncation facts are invalid")
    elif (
        response_size != retained_size
        or hashlib.sha256(body_bytes).hexdigest() != digest
    ):
        raise ValueError("provider error artifact complete-body facts are invalid")

    canonical = (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        + b"\n"
    )
    if canonical != payload:
        raise ValueError("provider error artifact serialization is not canonical")


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object or reject a duplicate member."""

    value: dict[str, object] = {}
    for key, member in pairs:
        if key in value:
            raise ValueError("provider error artifact duplicates a field")
        value[key] = member
    return value


def _meaningful_identity(
    value: os.stat_result,
    *,
    label: str,
) -> tuple[int, int]:
    """Return a meaningful filesystem identity or fail closed."""

    device = value.st_dev
    inode = value.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        raise OSError(f"{label} identity is unavailable")
    return device, inode


def _acquire_advisory_lock(descriptor: int, *, timeout_seconds: float) -> bool:
    """Acquire the independent advisory guard within one bounded interval."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            if _fcntl is not None:
                _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            elif _msvcrt is not None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                _msvcrt.locking(descriptor, _msvcrt.LK_NBLCK, 1)
            else:
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
    """Release this module's advisory guard."""

    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)


def _publish_under_guard(
    directories: _ArtifactDirectories,
    guard_descriptor: int,
    payload: bytes,
    *,
    timestamp: datetime,
) -> None:
    """Stage, prune, and atomically publish one complete artifact."""

    artifacts, pending = _scan_artifact_directory(directories)
    _verify_guard(guard_descriptor, directories)
    if pending is not None:
        _verify_expected_leaf(
            directories,
            _PENDING_FILENAME,
            pending,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        _verify_guard(guard_descriptor, directories)
        _verify_directories(directories)
        os.unlink(_PENDING_FILENAME, dir_fd=directories.artifact_descriptor)

    final_name = _choose_final_name(directories, timestamp=timestamp)
    pending_identity = _stage_pending(
        directories,
        guard_descriptor,
        payload,
    )
    artifacts, _pending = _scan_artifact_directory(directories)
    assert _pending is not None
    _prune_for_payload(
        directories,
        guard_descriptor,
        artifacts,
        incoming_size=len(payload),
    )

    artifacts, pending = _scan_artifact_directory(directories)
    if pending is None:
        raise OSError("provider error artifact pending file disappeared")
    if _meaningful_identity(pending, label="pending artifact") != pending_identity:
        raise OSError("provider error artifact pending identity changed")
    if (
        len(artifacts) >= PROVIDER_ERROR_ARTIFACT_MAX_FILES
        or sum(item.size for item in artifacts) + len(payload)
        > PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES
    ):
        raise OSError("provider error artifact retention state changed")
    if (
        _stat_optional(
            directories,
            final_name,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        is not None
    ):
        raise FileExistsError(final_name)
    _verify_guard(guard_descriptor, directories)
    _verify_directories(directories)
    _rename_noreplace(
        directories.artifact_descriptor,
        _PENDING_FILENAME,
        directories.artifact_descriptor,
        final_name,
    )
    published = _stat_optional(
        directories,
        final_name,
        maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
    )
    if published is None or (
        _meaningful_identity(published, label="published artifact") != pending_identity
    ):
        raise OSError("provider error artifact publication identity changed")
    os.fsync(directories.artifact_descriptor)
    complete, remaining_pending = _scan_artifact_directory(directories)
    if remaining_pending is not None:
        raise OSError("provider error artifact pending file remains after publication")
    if (
        len(complete) > PROVIDER_ERROR_ARTIFACT_MAX_FILES
        or sum(item.size for item in complete) > PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES
    ):
        raise OSError("provider error artifact retention bound was exceeded")


def _verify_guard(
    descriptor: int,
    directories: _ArtifactDirectories,
) -> None:
    """Reverify the exact locked guard descriptor and name."""

    _verify_open_file(
        descriptor,
        directories,
        _GUARD_FILENAME,
        maximum_size=1,
    )


def _choose_final_name(
    directories: _ArtifactDirectories,
    *,
    timestamp: datetime,
) -> str:
    """Choose one absent timestamp-and-UUID artifact name."""

    prefix = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    for _attempt in range(_FINAL_NAME_ATTEMPTS):
        candidate = f"{prefix}-{uuid4().hex}.json"
        if _ARTIFACT_FILENAME.fullmatch(candidate) is None:
            raise AssertionError("generated provider error artifact name is invalid")
        if (
            _stat_optional(
                directories,
                candidate,
                maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
            )
            is None
        ):
            return candidate
    raise FileExistsError("could not allocate a provider error artifact name")


def _stage_pending(
    directories: _ArtifactDirectories,
    guard_descriptor: int,
    payload: bytes,
) -> tuple[int, int]:
    """Create, completely write, synchronize, and close ``.pending``."""

    _verify_guard(guard_descriptor, directories)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for flag_name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, flag_name, 0)
    descriptor = os.open(
        _PENDING_FILENAME,
        flags,
        0o600,
        dir_fd=directories.artifact_descriptor,
    )
    body_failure: _ExceptionState | None = None
    identity: tuple[int, int] | None = None
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        opened = _verify_open_file(
            descriptor,
            directories,
            _PENDING_FILENAME,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        identity = _meaningful_identity(opened, label="pending artifact")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        opened = _verify_open_file(
            descriptor,
            directories,
            _PENDING_FILENAME,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        if opened.st_size != len(payload):
            raise OSError("provider error artifact pending size is incomplete")
    except BaseException as failure:
        body_failure = (failure, failure.__traceback__)
    cleanup_control = _cleanup_descriptor(descriptor, None)
    _raise_after_cleanup(body_failure, cleanup_control)
    assert identity is not None
    _verify_expected_identity(
        directories,
        _PENDING_FILENAME,
        identity,
        expected_size=len(payload),
    )
    return identity


def _write_all(descriptor: int, payload: bytes) -> None:
    """Write every payload byte or fail without treating a short write as success."""

    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise OSError("provider error artifact write made no progress")
        offset += written


def _prune_for_payload(
    directories: _ArtifactDirectories,
    guard_descriptor: int,
    artifacts: list[_ArtifactFile],
    *,
    incoming_size: int,
) -> None:
    """Remove the oldest verified complete artifacts until the new one fits."""

    total = sum(item.size for item in artifacts)
    retained = list(artifacts)
    while (
        len(retained) >= PROVIDER_ERROR_ARTIFACT_MAX_FILES
        or total + incoming_size > PROVIDER_ERROR_ARTIFACT_TOTAL_MAX_BYTES
    ):
        if not retained:
            raise OSError("provider error artifact payload cannot fit retention")
        oldest = retained.pop(0)
        current = _stat_optional(
            directories,
            oldest.name,
            maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
        )
        if current is None:
            raise OSError("provider error artifact disappeared before pruning")
        if (
            _meaningful_identity(current, label="provider error artifact")
            != oldest.identity
            or current.st_size != oldest.size
            or current.st_mtime_ns != oldest.modified_ns
            or current.st_ctime_ns != oldest.changed_ns
        ):
            raise OSError("provider error artifact changed before pruning")
        _read_validated_artifact(directories, oldest.name, expected=current)
        _verify_guard(guard_descriptor, directories)
        _verify_directories(directories)
        os.unlink(oldest.name, dir_fd=directories.artifact_descriptor)
        total -= oldest.size


def _verify_expected_leaf(
    directories: _ArtifactDirectories,
    filename: str,
    expected: os.stat_result,
    *,
    maximum_size: int,
) -> None:
    """Require a named leaf to match one complete stat snapshot."""

    current = _stat_optional(
        directories,
        filename,
        maximum_size=maximum_size,
    )
    if current is None or (
        _meaningful_identity(current, label="provider error artifact leaf")
        != _meaningful_identity(expected, label="expected provider error artifact leaf")
        or current.st_size != expected.st_size
    ):
        raise OSError("provider error artifact leaf changed")


def _verify_expected_identity(
    directories: _ArtifactDirectories,
    filename: str,
    expected_identity: tuple[int, int],
    *,
    expected_size: int,
) -> None:
    """Require a named leaf to retain one expected identity and size."""

    current = _stat_optional(
        directories,
        filename,
        maximum_size=PROVIDER_ERROR_ARTIFACT_FILE_MAX_BYTES,
    )
    if current is None or (
        _meaningful_identity(current, label="provider error artifact leaf")
        != expected_identity
        or current.st_size != expected_size
    ):
        raise OSError("provider error artifact leaf identity changed")
