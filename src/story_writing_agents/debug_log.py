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
from .provider_adapter import AgentExecutionProfile
from .workspace import (
    Workspace,
    open_workspace_root_directory,
    verify_workspace_root_directory,
)

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
_SAFE_CORRELATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
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
    AGENT_CONTEXT = "agent_context"
    ATTACHMENT = "attachment"
    CONVERSATION_FOCUS = "conversation_focus"
    ADVISORY_TURN = "advisory_turn"
    RETRIEVAL_TURN = "retrieval_turn"
    CONSENSUS_RUN = "consensus_run"
    DIRECTIVE = "directive"
    INSPECTION = "inspection"
    CLEANUP = "cleanup"
    EXECUTION_SETTING_CHANGE = "execution_setting_change"
    EXECUTION_PROFILE_APPLIED = "execution_profile_applied"
    EXECUTION_PROFILE_CONFIRMED = "execution_profile_confirmed"
    EXECUTION_PROFILE_RECOVERY_REQUIRED = "execution_profile_recovery_required"


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
    CONTEXT_BOOTSTRAP = "context_bootstrap"
    CONTEXT_DELTA = "context_delta"
    CONTEXT_OMISSION = "context_omission"
    CONTEXT_SEARCH_STARTED = "context_search_started"
    CONTEXT_SEARCH_COMPLETED = "context_search_completed"
    CONTEXT_READ_STARTED = "context_read_started"
    CONTEXT_READ_COMPLETED = "context_read_completed"
    CONTEXT_ANSWER_AUTHORIZED = "context_answer_authorized"
    CONTEXT_INVALIDATED = "context_invalidated"
    CONTEXT_BUDGET_EXHAUSTED = "context_budget_exhausted"
    CONTEXT_ACTION_EXHAUSTED = "context_action_exhausted"
    CONTEXT_TAINTED = "context_tainted"
    CONTEXT_CHECKPOINT_ADVANCED = "context_checkpoint_advanced"
    CONTEXT_CHECKPOINT_NOT_ADVANCED = "context_checkpoint_not_advanced"


class _WorkspaceProcessLock:
    """Weakly retained in-process serialization for one workspace log."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()


class _DebugDirectory:
    """One verified named debug directory held open for a write attempt."""

    __slots__ = ("descriptor", "root_descriptor", "workspace")

    def __init__(
        self,
        workspace: Workspace,
        root_descriptor: int,
        descriptor: int,
    ) -> None:
        self.workspace = workspace
        self.root_descriptor = root_descriptor
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

    def execution_profile_event(
        self,
        operation: DebugOperation,
        *,
        correlation_id: str,
        agent_id: str,
        route_generation: int,
        old_profile: AgentExecutionProfile | None = None,
        new_profile: AgentExecutionProfile | None = None,
        active_profile: AgentExecutionProfile | None = None,
        activation_state: str,
        thread_disposition: str | None = None,
        context_state: str | None = None,
        recovery_required: bool = False,
    ) -> None:
        """Best-effort append one exact, content-free schema-v2 profile event."""

        try:
            if operation not in {
                DebugOperation.EXECUTION_SETTING_CHANGE,
                DebugOperation.EXECUTION_PROFILE_APPLIED,
                DebugOperation.EXECUTION_PROFILE_CONFIRMED,
                DebugOperation.EXECUTION_PROFILE_RECOVERY_REQUIRED,
            }:
                raise ValueError("operation is not an execution-profile event")
            if (
                not isinstance(correlation_id, str)
                or _SAFE_CORRELATION.fullmatch(correlation_id) is None
            ):
                raise ValueError("correlation_id is invalid")
            validated_agent = self._workspace.validate_identifier(
                agent_id,
                label="agent ID",
            )
            if validated_agent not in ROSTER:
                raise ValueError("agent_id must be canonical")
            if type(route_generation) is not int or route_generation < 0:
                raise ValueError("route_generation must be a non-negative integer")
            active_snapshot = (
                operation is DebugOperation.EXECUTION_PROFILE_CONFIRMED
                or (
                    operation is DebugOperation.EXECUTION_PROFILE_APPLIED
                    and active_profile is not None
                )
            )
            if active_snapshot:
                if (
                    old_profile is not None
                    or new_profile is not None
                    or type(active_profile) is not AgentExecutionProfile
                ):
                    raise TypeError(
                        "active profile events require only an active profile"
                    )
                profiles = (active_profile,)
            else:
                if (
                    type(old_profile) is not AgentExecutionProfile
                    or type(new_profile) is not AgentExecutionProfile
                    or active_profile is not None
                ):
                    raise TypeError(
                        "profile transition events require old and new profiles"
                    )
                profiles = (old_profile, new_profile)
            if any(profile.agent_id != validated_agent for profile in profiles):
                raise ValueError("execution profile agent identity mismatch")
            for name, value in (
                ("activation_state", activation_state),
                ("thread_disposition", thread_disposition),
                ("context_state", context_state),
            ):
                if value is not None and (
                    not isinstance(value, str)
                    or not value
                    or _SAFE_SYMBOL.fullmatch(value) is None
                ):
                    raise ValueError(f"{name} is invalid")
            if type(recovery_required) is not bool:
                raise TypeError("recovery_required must be a boolean")
            record: dict[str, object] = {
                "schema_version": 2,
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "level": (
                    DebugLevel.ERROR.value
                    if recovery_required
                    else DebugLevel.TRACE.value
                ),
                "component": DebugComponent.BACKEND.value,
                "operation": operation.value,
                "phase": (
                    DebugPhase.FAILED.value
                    if recovery_required
                    else DebugPhase.SUCCEEDED.value
                ),
                "correlation_id": correlation_id,
                "agent_id": validated_agent,
                "route_generation": route_generation,
                "activation_state": activation_state,
            }
            if active_profile is not None:
                record["active_profile"] = _execution_profile_projection(
                    active_profile
                )
            else:
                assert old_profile is not None and new_profile is not None
                record["old_profile"] = _execution_profile_projection(old_profile)
                record["new_profile"] = _execution_profile_projection(new_profile)
            if thread_disposition is not None:
                record["thread_disposition"] = thread_disposition
            if context_state is not None:
                record["context_state"] = context_state
            payload = (
                json.dumps(
                    record,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8", errors="strict")
                + b"\n"
            )
            if len(payload) <= DEBUG_LOG_MAX_BYTES:
                self._append(payload)
        except Exception:
            return

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
        process_lock_acquired = False
        directory: _DebugDirectory | None = None
        descriptor: int | None = None
        locked = False
        body_failure: _ExceptionState | None = None
        try:
            process_lock_acquired = process_lock.lock.acquire(
                timeout=DEBUG_LOG_LOCK_TIMEOUT_SECONDS
            )
            if not process_lock_acquired:
                return
            directory = _prepare_debug_directory(self._workspace)
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
            locked = _acquire_advisory_lock(
                descriptor,
                timeout_seconds=max(0.0, deadline - time.monotonic()),
            )
            if locked:
                _verify_open_file(
                    descriptor,
                    directory,
                    _DEBUG_LOG_GUARD_FILENAME,
                )
                _append_under_guard(directory, payload)
        except BaseException as failure:
            body_failure = (failure, failure.__traceback__)

        cleanup_control: _ExceptionState | None = None
        if locked and descriptor is not None:
            cleanup_control = _cleanup_advisory_lock(
                descriptor,
                cleanup_control,
            )
        if descriptor is not None:
            cleanup_control = _cleanup_descriptor(
                descriptor,
                cleanup_control,
            )
        if directory is not None:
            cleanup_control = _cleanup_descriptor(
                directory.descriptor,
                cleanup_control,
            )
            cleanup_control = _cleanup_descriptor(
                directory.root_descriptor,
                cleanup_control,
            )
        if process_lock_acquired:
            cleanup_control = _cleanup_process_lock(
                process_lock,
                cleanup_control,
            )
        _raise_after_cleanup(body_failure, cleanup_control)


def _process_lock_for(workspace: Workspace) -> _WorkspaceProcessLock:
    """Return the weak process lock keyed by one resolved workspace root."""

    key = str(workspace.root)
    with _PROCESS_LOCK_REGISTRY_GUARD:
        retained = _PROCESS_LOCKS.get(key)
        if retained is None:
            retained = _WorkspaceProcessLock()
            _PROCESS_LOCKS[key] = retained
        return retained


_ExceptionState = tuple[BaseException, TracebackType | None]


def _remember_cleanup_control(
    retained: _ExceptionState | None,
    failure: BaseException,
) -> _ExceptionState | None:
    """Retain only the first non-ordinary cleanup failure."""

    if isinstance(failure, Exception) or retained is not None:
        return retained
    return failure, failure.__traceback__


def _cleanup_descriptor(
    descriptor: int,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Close one descriptor once while retaining a process-control failure."""

    try:
        os.close(descriptor)
    except Exception:
        pass
    except BaseException as failure:
        retained = _remember_cleanup_control(retained, failure)
    return retained


def _cleanup_advisory_lock(
    descriptor: int,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Release one advisory lock while retaining process-control failure."""

    try:
        _release_advisory_lock(descriptor)
    except Exception:
        pass
    except BaseException as failure:
        retained = _remember_cleanup_control(retained, failure)
    return retained


def _cleanup_process_lock(
    process_lock: _WorkspaceProcessLock,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Release the process lock and make one state-checked recovery attempt."""

    release_failed = False
    try:
        process_lock.lock.release()
    except Exception:
        release_failed = True
    except BaseException as failure:
        release_failed = True
        retained = _remember_cleanup_control(retained, failure)
    if not release_failed:
        return retained

    try:
        still_locked = process_lock.lock.locked()
    except Exception:
        return retained
    except BaseException as failure:
        return _remember_cleanup_control(retained, failure)
    if not still_locked:
        return retained
    try:
        process_lock.lock.release()
    except Exception:
        pass
    except BaseException as failure:
        retained = _remember_cleanup_control(retained, failure)
    return retained


def _raise_after_cleanup(
    body_failure: _ExceptionState | None,
    cleanup_control: _ExceptionState | None,
) -> None:
    """Raise the body control, first cleanup control, or ordinary body failure."""

    retained = body_failure
    if body_failure is None or isinstance(body_failure[0], Exception):
        retained = cleanup_control or body_failure
    if retained is not None:
        failure, traceback = retained
        raise failure.with_traceback(traceback)


def _prepare_debug_directory(workspace: Workspace) -> _DebugDirectory:
    """Create and hold the fixed directory beneath a verified workspace root."""

    _require_directory_relative_operations()
    root_descriptor = open_workspace_root_directory(workspace)
    descriptor: int | None = None
    try:
        verify_workspace_root_directory(workspace, root_descriptor)
        try:
            os.mkdir(
                DEBUG_LOG_DIRECTORY,
                mode=0o700,
                dir_fd=root_descriptor,
            )
        except FileExistsError:
            pass
        verify_workspace_root_directory(workspace, root_descriptor)
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
        descriptor = os.open(
            DEBUG_LOG_DIRECTORY,
            flags,
            dir_fd=root_descriptor,
        )
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, OSError):
            pass
        held = _DebugDirectory(workspace, root_descriptor, descriptor)
        _verify_debug_directory(held)
        return held
    except BaseException as failure:
        body_failure: _ExceptionState = (failure, failure.__traceback__)
        cleanup_control: _ExceptionState | None = None
        if descriptor is not None:
            cleanup_control = _cleanup_descriptor(
                descriptor,
                cleanup_control,
            )
        cleanup_control = _cleanup_descriptor(
            root_descriptor,
            cleanup_control,
        )
        _raise_after_cleanup(body_failure, cleanup_control)
        raise AssertionError("unreachable")


def _require_directory_relative_operations() -> None:
    """Fail closed unless every required descriptor-relative primitive exists."""

    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    supports_follow_symlinks = getattr(os, "supports_follow_symlinks", ())
    required_dir_fd = (os.mkdir, os.open, os.stat, os.unlink, os.rename)
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
    """Require the held root and child to retain their canonical identities."""

    verify_workspace_root_directory(
        directory.workspace,
        directory.root_descriptor,
    )
    opened = os.fstat(directory.descriptor)
    named = os.stat(
        DEBUG_LOG_DIRECTORY,
        dir_fd=directory.root_descriptor,
        follow_symlinks=False,
    )
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(named.st_mode):
        raise OSError("debug log path is not a regular directory")
    opened_identity = _meaningful_identity(opened, label="debug log directory")
    named_identity = _meaningful_identity(named, label="debug log directory")
    if opened_identity != named_identity:
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
    except BaseException as failure:
        body_failure: _ExceptionState = (failure, failure.__traceback__)
        cleanup_control = _cleanup_descriptor(descriptor, None)
        _raise_after_cleanup(body_failure, cleanup_control)
        raise AssertionError("unreachable")


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
    opened_identity = _meaningful_identity(opened, label="debug log file")
    named_identity = _meaningful_identity(named, label="debug log file")
    if opened_identity != named_identity:
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
    opened_identity = _meaningful_identity(opened, label="debug log rollback")
    if opened_identity != expected_identity:
        raise OSError("debug log rollback identity changed")


def _meaningful_identity(
    value: os.stat_result,
    *,
    label: str,
) -> tuple[int, int]:
    """Return one nonzero filesystem identity or fail closed."""

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
    expected: dict[str, os.stat_result | None] = {
        active: _validate_existing_regular(directory, active),
    }
    expected.update(
        (backup, _validate_existing_regular(directory, backup))
        for backup in backups
    )

    active_stat = expected[active]
    active_size = active_stat.st_size if active_stat is not None else 0
    if active_size and active_size + len(payload) > DEBUG_LOG_MAX_BYTES:
        oldest = backups[-1]
        _validate_expected_rotation_state(
            directory,
            oldest,
            expected[oldest],
        )
        if expected[oldest] is not None:
            _verify_debug_directory(directory)
            os.unlink(oldest, dir_fd=directory.descriptor)
            expected[oldest] = None
        for source, destination in zip(
            reversed(backups[:-1]),
            reversed(backups[1:]),
        ):
            source_stat = expected[source]
            _validate_expected_rotation_state(
                directory,
                source,
                source_stat,
            )
            _validate_expected_rotation_state(
                directory,
                destination,
                expected[destination],
            )
            if source_stat is not None:
                _verify_debug_directory(directory)
                os.rename(
                    source,
                    destination,
                    src_dir_fd=directory.descriptor,
                    dst_dir_fd=directory.descriptor,
                )
                expected[destination] = source_stat
                expected[source] = None
        _validate_expected_rotation_state(
            directory,
            active,
            expected[active],
        )
        _validate_expected_rotation_state(
            directory,
            backups[0],
            expected[backups[0]],
        )
        _verify_debug_directory(directory)
        os.rename(
            active,
            backups[0],
            src_dir_fd=directory.descriptor,
            dst_dir_fd=directory.descriptor,
        )
        expected[backups[0]] = expected[active]
        expected[active] = None

    descriptor = _open_verified_regular_file(
        directory,
        active,
        read_write=False,
    )
    prior_size: int | None = None
    prior_identity: tuple[int, int] | None = None
    body_failure: _ExceptionState | None = None
    cleanup_control: _ExceptionState | None = None
    try:
        opened = _verify_open_descriptor(descriptor)
        prior_size = opened.st_size
        prior_identity = _meaningful_identity(opened, label="debug log append")
        remaining = memoryview(payload)
        while remaining:
            _verify_open_file(descriptor, directory, active)
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short debug log append")
            remaining = remaining[written:]
        _verify_open_file(descriptor, directory, active)
        os.fsync(descriptor)
    except BaseException as failure:
        body_failure = (failure, failure.__traceback__)
        if prior_size is not None and prior_identity is not None:
            try:
                _verify_rollback_descriptor(descriptor, prior_identity)
                os.ftruncate(descriptor, prior_size)
                os.fsync(descriptor)
            except Exception:
                pass
            except BaseException as cleanup_failure:
                cleanup_control = _remember_cleanup_control(
                    cleanup_control,
                    cleanup_failure,
                )
    cleanup_control = _cleanup_descriptor(descriptor, cleanup_control)
    _raise_after_cleanup(body_failure, cleanup_control)

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

    _verify_debug_directory(directory)
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
    _meaningful_identity(value, label="debug log rotation path")
    return value


def _validate_expected_rotation_state(
    directory: _DebugDirectory,
    filename: str,
    expected: os.stat_result | None,
) -> None:
    """Require one rotation name to retain its exact captured state."""

    current = _validate_existing_regular(directory, filename)
    if expected is None:
        if current is not None:
            raise OSError("debug log rotation path unexpectedly appeared")
        return
    if current is None:
        raise OSError("debug log rotation path unexpectedly disappeared")
    expected_identity = _meaningful_identity(
        expected,
        label="debug log rotation snapshot",
    )
    current_identity = _meaningful_identity(
        current,
        label="debug log rotation path",
    )
    if current_identity != expected_identity:
        raise OSError("debug log rotation path identity changed")


def _validate_existing_guard(
    directory: _DebugDirectory,
    filename: str,
) -> None:
    """Reject an unsafe existing guard in the held directory."""

    _verify_debug_directory(directory)
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
    _meaningful_identity(value, label="debug log guard")


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


def _execution_profile_projection(
    profile: AgentExecutionProfile,
) -> dict[str, object]:
    """Project exact provider/model/effort identities and their sources."""

    return {
        "provider": profile.provider,
        "provider_source": profile.provider_source.value,
        "model": profile.model,
        "model_source": profile.model_source.value,
        "effort": profile.effort,
        "effort_source": profile.effort_source.value,
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
