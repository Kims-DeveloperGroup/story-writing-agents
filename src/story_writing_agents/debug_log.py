"""Content-safe, bounded workspace debug-log infrastructure."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import threading
import time
import weakref
from collections import deque
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType

from .agents import ROSTER
from .workspace import Workspace

try:  # pragma: no cover - availability is platform-specific.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows path.
    _fcntl = None

try:  # pragma: no cover - availability is platform-specific.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX path.
    _msvcrt = None


DEBUG_LOG_DIRECTORY = "debug_logs"
DEBUG_LOG_RELATIVE_PATH = f"{DEBUG_LOG_DIRECTORY}/runtime.jsonl"
DEBUG_LOG_GUARD_RELATIVE_PATH = f"{DEBUG_LOG_DIRECTORY}/.runtime.lock"
DEBUG_LOG_MAX_BYTES = 1_048_576
DEBUG_LOG_BACKUP_COUNT = 5
DEBUG_LOG_MAX_FRAMES = 32
DEBUG_LOG_LOCK_TIMEOUT_SECONDS = 0.1

_SCHEMA_VERSION = 1
_LOCK_RETRY_SECONDS = 0.01
_DEBUG_LOG_FILENAME = Path(DEBUG_LOG_RELATIVE_PATH).name
_DEBUG_LOG_GUARD_FILENAME = Path(DEBUG_LOG_GUARD_RELATIVE_PATH).name
_SAFE_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,159}")
_SAFE_SYMBOL = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_.<>]{0,159}|<[A-Za-z_][A-Za-z0-9_]*>)"
)


class DebugLevel(StrEnum):
    """Severity admitted by the safe persisted schema."""

    TRACE = "trace"
    ERROR = "error"


class DebugComponent(StrEnum):
    """Runtime boundaries permitted to emit diagnostic records."""

    CLI = "cli"
    BACKEND = "backend"
    RUNTIME = "runtime"
    INTERACTIVE_CONTROLLER = "interactive_controller"
    SMOKE = "smoke"


class DebugOperation(StrEnum):
    """Content-free runtime operations admitted by diagnostic records."""

    WORKSPACE_DISCOVERY = "workspace_discovery"
    WORKSPACE_INITIALIZATION = "workspace_initialization"
    CLI_CONTEXT = "cli_context"
    CLI_COMMAND = "cli_command"
    INTERACTIVE_LOBBY = "interactive_lobby"
    INTERACTIVE_COMMAND = "interactive_command"
    CONFIGURATION = "configuration"
    BACKEND_LIFECYCLE = "backend_lifecycle"
    SESSION_LIFECYCLE = "session_lifecycle"
    PROVIDER_INVOCATION = "provider_invocation"
    ATTACHMENT = "attachment"
    CONVERSATION_FOCUS = "conversation_focus"
    ADVISORY_TURN = "advisory_turn"
    RETRIEVAL_TURN = "retrieval_turn"
    CONSENSUS_RUN = "consensus_run"
    DIRECTIVE = "directive"
    INSPECTION = "inspection"
    CLEANUP = "cleanup"


class DebugPhase(StrEnum):
    """Lifecycle positions admitted by diagnostic records."""

    STARTED = "started"
    OBSERVED = "observed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DebugOutcome(StrEnum):
    """Fixed content-free outcomes that add useful operational correlation."""

    COMPLETED = "completed"
    CONSENSUS = "consensus"
    DEADLOCK = "deadlock"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"
    READY = "ready"
    PAUSED = "paused"
    CLAIMABLE = "claimable"
    HELD_HERE = "held_here"


class _WorkspaceProcessLock:
    """Weakly retained in-process serialization for one workspace log."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


class _DebugDirectory:
    """One verified named debug directory held open for a write attempt."""

    __slots__ = ("descriptor", "path")

    def __init__(self, path: Path, descriptor: int) -> None:
        self.path = path
        self.descriptor = descriptor


_PROCESS_LOCK_REGISTRY_GUARD = threading.Lock()
_PROCESS_LOCKS: weakref.WeakValueDictionary[str, _WorkspaceProcessLock] = (
    weakref.WeakValueDictionary()
)


class WorkspaceDebugLog:
    """Best-effort writer for one workspace's safe rotating debug log.

    Construction and import perform no filesystem work. Ordinary failures from
    invalid diagnostic inputs, contention, unsafe paths, or storage drop only
    that record; process-control exceptions continue to propagate.
    """

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Workspace) -> None:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        self._workspace = workspace

    @property
    def path(self) -> Path:
        """Return the canonical active-log path without filesystem access."""

        return self._workspace.root / DEBUG_LOG_RELATIVE_PATH

    def trace(
        self,
        component: DebugComponent,
        operation: DebugOperation,
        phase: DebugPhase,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None:
        """Best-effort append one content-free ordinary runtime event."""

        self._record(
            level=DebugLevel.TRACE,
            component=component,
            operation=operation,
            phase=phase,
            session_id=session_id,
            conversation_id=conversation_id,
            debate_id=debate_id,
            agent_id=agent_id,
            outcome=outcome,
            exception=None,
        )

    def error(
        self,
        component: DebugComponent,
        operation: DebugOperation,
        exception: BaseException,
        *,
        phase: DebugPhase = DebugPhase.FAILED,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None:
        """Best-effort append one failure without exception prose or values."""

        self._record(
            level=DebugLevel.ERROR,
            component=component,
            operation=operation,
            phase=phase,
            session_id=session_id,
            conversation_id=conversation_id,
            debate_id=debate_id,
            agent_id=agent_id,
            outcome=outcome,
            exception=exception,
        )

    def _record(
        self,
        *,
        level: DebugLevel,
        component: DebugComponent,
        operation: DebugOperation,
        phase: DebugPhase,
        session_id: str | None,
        conversation_id: str | None,
        debate_id: str | None,
        agent_id: str | None,
        outcome: DebugOutcome | None,
        exception: BaseException | None,
    ) -> None:
        """Build and append one exact record while suppressing ordinary failures."""

        try:
            record = self._build_record(
                level=level,
                component=component,
                operation=operation,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
                exception=exception,
            )
            payload = (
                json.dumps(
                    record,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8", errors="strict")
                + b"\n"
            )
            if len(payload) > DEBUG_LOG_MAX_BYTES:
                return
            self._append(payload)
        except Exception:
            return

    def _build_record(
        self,
        *,
        level: DebugLevel,
        component: DebugComponent,
        operation: DebugOperation,
        phase: DebugPhase,
        session_id: str | None,
        conversation_id: str | None,
        debate_id: str | None,
        agent_id: str | None,
        outcome: DebugOutcome | None,
        exception: BaseException | None,
    ) -> dict[str, object]:
        """Return one validated schema-v1 record without performing I/O."""

        if not isinstance(level, DebugLevel):
            raise TypeError("level must be a DebugLevel")
        if not isinstance(component, DebugComponent):
            raise TypeError("component must be a DebugComponent")
        if not isinstance(operation, DebugOperation):
            raise TypeError("operation must be a DebugOperation")
        if not isinstance(phase, DebugPhase):
            raise TypeError("phase must be a DebugPhase")
        if outcome is not None and not isinstance(outcome, DebugOutcome):
            raise TypeError("outcome must be a DebugOutcome or None")
        if exception is not None and not isinstance(exception, BaseException):
            raise TypeError("exception must be a BaseException or None")
        if level is DebugLevel.TRACE and exception is not None:
            raise ValueError("trace records cannot contain an exception")
        if level is DebugLevel.ERROR and exception is None:
            raise ValueError("error records require an exception")
        if level is DebugLevel.ERROR and phase not in {
            DebugPhase.FAILED,
            DebugPhase.CANCELLED,
        }:
            raise ValueError("error records require a failed or cancelled phase")

        record: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": level.value,
            "component": component.value,
            "operation": operation.value,
            "phase": phase.value,
        }
        for field, value, label in (
            ("session_id", session_id, "session ID"),
            ("conversation_id", conversation_id, "conversation ID"),
            ("debate_id", debate_id, "debate ID"),
        ):
            if value is not None:
                record[field] = self._workspace.validate_identifier(
                    value,
                    label=label,
                )
        if agent_id is not None:
            validated_agent = self._workspace.validate_identifier(
                agent_id,
                label="agent ID",
            )
            if validated_agent not in ROSTER:
                raise ValueError("agent_id must be canonical")
            record["agent_id"] = validated_agent
        if outcome is not None:
            record["outcome"] = outcome.value
        if exception is not None:
            record["exception"] = _project_exception(exception)
        return record

    def _append(self, payload: bytes) -> None:
        """Serialize one bounded append across cooperating threads/processes."""

        process_lock = _process_lock_for(self._workspace)
        deadline = time.monotonic() + DEBUG_LOG_LOCK_TIMEOUT_SECONDS
        if not process_lock.lock.acquire(timeout=DEBUG_LOG_LOCK_TIMEOUT_SECONDS):
            return
        try:
            directory = _prepare_debug_directory(self._workspace)
            try:
                _validate_existing_regular(directory, _DEBUG_LOG_FILENAME)
                for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
                    _validate_existing_regular(
                        directory,
                        f"{_DEBUG_LOG_FILENAME}.{index}",
                    )
                _validate_existing_guard(directory, _DEBUG_LOG_GUARD_FILENAME)
                _verify_debug_directory(directory)
                descriptor = _open_verified_regular_file(
                    directory,
                    _DEBUG_LOG_GUARD_FILENAME,
                    read_write=True,
                )
                locked = False
                try:
                    locked = _acquire_advisory_lock(
                        descriptor,
                        timeout_seconds=max(0.0, deadline - time.monotonic()),
                    )
                    if not locked:
                        return
                    _verify_open_file(
                        descriptor,
                        directory,
                        _DEBUG_LOG_GUARD_FILENAME,
                    )
                    _append_under_guard(directory, payload)
                finally:
                    if locked:
                        try:
                            _release_advisory_lock(descriptor)
                        except BaseException:
                            pass
                    try:
                        os.close(descriptor)
                    except BaseException:
                        pass
            finally:
                try:
                    os.close(directory.descriptor)
                except BaseException:
                    pass
        finally:
            process_lock.lock.release()


def _process_lock_for(workspace: Workspace) -> _WorkspaceProcessLock:
    """Return the weak process lock keyed by one resolved workspace root."""

    key = str(workspace.root)
    with _PROCESS_LOCK_REGISTRY_GUARD:
        retained = _PROCESS_LOCKS.get(key)
        if retained is None:
            retained = _WorkspaceProcessLock()
            _PROCESS_LOCKS[key] = retained
        return retained


def _prepare_debug_directory(workspace: Workspace) -> _DebugDirectory:
    """Create and hold the fixed directory without following its final name."""

    _require_directory_relative_operations()

    directory = workspace.path(DEBUG_LOG_DIRECTORY)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    directory = workspace.path(DEBUG_LOG_DIRECTORY)
    flags = os.O_RDONLY
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, name, 0)
    descriptor = os.open(directory, flags)
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        held = _DebugDirectory(directory, descriptor)
        _verify_debug_directory(held)
        return held
    except BaseException:
        os.close(descriptor)
        raise


def _require_directory_relative_operations() -> None:
    """Fail closed unless every required descriptor-relative primitive exists."""

    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    required_dir_fd = (os.open, os.stat, os.unlink, os.rename)
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or any(function not in supports_dir_fd for function in required_dir_fd)
        or os.stat not in supports_follow_symlinks
    ):
        raise OSError(
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            "safe descriptor-relative debug logging is unsupported",
        )


def _verify_debug_directory(directory: _DebugDirectory) -> None:
    """Require the held directory to retain its canonical workspace name."""

    opened = os.fstat(directory.descriptor)
    named = os.stat(directory.path, follow_symlinks=False)
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise OSError("debug log path is not a regular directory")
    opened_identity = (opened.st_dev, opened.st_ino)
    named_identity = (named.st_dev, named.st_ino)
    if any((*opened_identity, *named_identity)) and opened_identity != named_identity:
        raise OSError("debug log directory identity changed")


def _open_verified_regular_file(
    directory: _DebugDirectory,
    filename: str,
    *,
    read_write: bool,
) -> int:
    """Open or exclusively create one verified file relative to the held directory."""

    flags = os.O_RDWR if read_write else os.O_WRONLY | os.O_APPEND
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, name, 0)
    _verify_debug_directory(directory)
    try:
        descriptor = os.open(
            filename,
            flags | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory.descriptor,
        )
    except FileExistsError:
        _verify_debug_directory(directory)
        descriptor = os.open(
            filename,
            flags,
            0o600,
            dir_fd=directory.descriptor,
        )
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        _verify_open_file(descriptor, directory, filename)
        if read_write and _fcntl is None and _msvcrt is not None:
            if os.fstat(descriptor).st_size == 0:
                _verify_open_file(descriptor, directory, filename)
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_open_file(
    descriptor: int,
    directory: _DebugDirectory,
    filename: str,
) -> None:
    """Require descriptor/name regularity and stable filesystem identity."""

    _verify_debug_directory(directory)
    opened = _verify_open_descriptor(descriptor)
    named = os.stat(
        filename,
        dir_fd=directory.descriptor,
        follow_symlinks=False,
    )
    if not stat.S_ISREG(named.st_mode):
        raise OSError("debug log file is not regular")
    if named.st_nlink != 1:
        raise OSError("debug log file link count is unsafe")
    opened_identity = (opened.st_dev, opened.st_ino)
    named_identity = (named.st_dev, named.st_ino)
    if any((*opened_identity, *named_identity)) and opened_identity != named_identity:
        raise OSError("debug log file identity changed")


def _verify_open_descriptor(descriptor: int) -> os.stat_result:
    """Require one opened file to remain regular and singly linked."""

    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        raise OSError("debug log file is not regular")
    if opened.st_nlink != 1:
        raise OSError("debug log file link count is unsafe")
    return opened


def _verify_rollback_descriptor(
    descriptor: int,
    expected_identity: tuple[int, int],
) -> None:
    """Require rollback to target the originally opened safe inode."""

    opened = _verify_open_descriptor(descriptor)
    opened_identity = (opened.st_dev, opened.st_ino)
    if (
        any((*opened_identity, *expected_identity))
        and opened_identity != expected_identity
    ):
        raise OSError("debug log rollback identity changed")


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
    """Release one advisory guard acquired by this module."""

    if _fcntl is not None:
        _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)


def _append_under_guard(directory: _DebugDirectory, payload: bytes) -> None:
    """Rotate when needed, then append with best-effort partial-write rollback."""

    active = _DEBUG_LOG_FILENAME
    backups = tuple(
        f"{_DEBUG_LOG_FILENAME}.{index}"
        for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1)
    )
    active_stat = _validate_existing_regular(directory, active)
    backup_stats = tuple(
        _validate_existing_regular(directory, backup) for backup in backups
    )

    active_size = active_stat.st_size if active_stat is not None else 0
    if active_size and active_size + len(payload) > DEBUG_LOG_MAX_BYTES:
        oldest = backups[-1]
        if backup_stats[-1] is not None:
            _validate_existing_regular(directory, oldest)
            _verify_debug_directory(directory)
            os.unlink(oldest, dir_fd=directory.descriptor)
        for source, destination, source_stat in zip(
            reversed(backups[:-1]),
            reversed(backups[1:]),
            reversed(backup_stats[:-1]),
        ):
            if source_stat is not None:
                _validate_existing_regular(directory, source)
                _validate_existing_regular(directory, destination)
                _verify_debug_directory(directory)
                os.rename(
                    source,
                    destination,
                    src_dir_fd=directory.descriptor,
                    dst_dir_fd=directory.descriptor,
                )
        _validate_existing_regular(directory, active)
        _validate_existing_regular(directory, backups[0])
        _verify_debug_directory(directory)
        os.rename(
            active,
            backups[0],
            src_dir_fd=directory.descriptor,
            dst_dir_fd=directory.descriptor,
        )

    descriptor = _open_verified_regular_file(
        directory,
        active,
        read_write=False,
    )
    prior_size: int | None = None
    prior_identity: tuple[int, int] | None = None
    try:
        opened = _verify_open_descriptor(descriptor)
        prior_size = opened.st_size
        prior_identity = (opened.st_dev, opened.st_ino)
        remaining = memoryview(payload)
        while remaining:
            _verify_open_file(descriptor, directory, active)
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short debug log append")
            remaining = remaining[written:]
        _verify_open_file(descriptor, directory, active)
        os.fsync(descriptor)
    except BaseException:
        if prior_size is not None and prior_identity is not None:
            try:
                _verify_rollback_descriptor(descriptor, prior_identity)
                os.ftruncate(descriptor, prior_size)
                os.fsync(descriptor)
            except BaseException:
                pass
        raise
    finally:
        os.close(descriptor)

    try:
        _verify_debug_directory(directory)
        os.fsync(directory.descriptor)
    except OSError:
        return


def _validate_existing_regular(
    directory: _DebugDirectory,
    filename: str,
) -> os.stat_result | None:
    """Return a safe existing rotation stat from the held directory."""

    try:
        value = os.stat(
            filename,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode):
        raise OSError("debug log rotation path is not regular")
    if value.st_nlink != 1:
        raise OSError("debug log rotation path link count is unsafe")
    if value.st_size > DEBUG_LOG_MAX_BYTES:
        raise OSError("debug log rotation path is oversized")
    return value


def _validate_existing_guard(
    directory: _DebugDirectory,
    filename: str,
) -> None:
    """Reject an unsafe existing guard in the held directory."""

    try:
        value = os.stat(
            filename,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if not stat.S_ISREG(value.st_mode):
        raise OSError("debug log guard is not regular")
    if value.st_nlink != 1:
        raise OSError("debug log guard link count is unsafe")


def _project_exception(exception: BaseException) -> dict[str, object]:
    """Project type and bounded stack frames without reading exception prose."""

    exception_type = type(exception)
    module = _safe_module_name(getattr(exception_type, "__module__", None))
    name = _safe_symbol_name(getattr(exception_type, "__qualname__", None))
    qualified = f"{module}.{name}" if module != "unknown" else name
    frames, truncated = _project_frames(exception.__traceback__)
    return {
        "type": qualified,
        "frames": frames,
        "frames_truncated": truncated,
    }


def _project_frames(
    traceback: TracebackType | None,
) -> tuple[list[dict[str, object]], bool]:
    """Keep the nearest 32 safe frame facts and detect malformed cycles."""

    retained: deque[dict[str, object]] = deque(maxlen=DEBUG_LOG_MAX_FRAMES)
    seen: set[int] = set()
    count = 0
    current = traceback
    while current is not None:
        identity = id(current)
        if identity in seen:
            return list(retained), True
        seen.add(identity)
        count += 1
        frame = current.tb_frame
        retained.append(
            {
                "module": _safe_module_name(frame.f_globals.get("__name__")),
                "function": _safe_symbol_name(frame.f_code.co_name),
                "line": max(1, int(current.tb_lineno)),
            }
        )
        current = current.tb_next
    return list(retained), count > DEBUG_LOG_MAX_FRAMES


def _safe_module_name(value: object) -> str:
    """Return one bounded ASCII module name or a fixed replacement."""

    if isinstance(value, str) and _SAFE_MODULE.fullmatch(value):
        return value
    return "unknown"


def _safe_symbol_name(value: object) -> str:
    """Return one bounded ASCII code/type symbol or a fixed replacement."""

    if isinstance(value, str) and _SAFE_SYMBOL.fullmatch(value):
        return value
    return "unknown"


__all__ = [
    "DEBUG_LOG_BACKUP_COUNT",
    "DEBUG_LOG_DIRECTORY",
    "DEBUG_LOG_GUARD_RELATIVE_PATH",
    "DEBUG_LOG_LOCK_TIMEOUT_SECONDS",
    "DEBUG_LOG_MAX_BYTES",
    "DEBUG_LOG_MAX_FRAMES",
    "DEBUG_LOG_RELATIVE_PATH",
    "DebugComponent",
    "DebugLevel",
    "DebugOperation",
    "DebugOutcome",
    "DebugPhase",
    "WorkspaceDebugLog",
]
