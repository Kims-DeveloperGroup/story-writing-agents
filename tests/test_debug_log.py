"""Content-safe bounded workspace debug-log contract tests."""

from __future__ import annotations

import ast
import contextlib
import errno
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import ROSTER
from story_writing_agents import debug_log as debug_module
from story_writing_agents.debug_log import (
    DEBUG_LOG_BACKUP_COUNT,
    DEBUG_LOG_DIRECTORY,
    DEBUG_LOG_GUARD_RELATIVE_PATH,
    DEBUG_LOG_LOCK_TIMEOUT_SECONDS,
    DEBUG_LOG_MAX_BYTES,
    DEBUG_LOG_MAX_FRAMES,
    DEBUG_LOG_RELATIVE_PATH,
    DebugComponent,
    DebugLevel,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from story_writing_agents.provider_adapter import (
    AgentExecutionProfile,
    ExecutionValueSource,
)
from story_writing_agents.workspace import Workspace

from tests.helpers import initialized_workspace


_FIXED_TIME = datetime(2026, 8, 20, 1, 2, 3, 456000, tzinfo=UTC)
_ACTIVE_NAME = Path(DEBUG_LOG_RELATIVE_PATH).name
_GUARD_NAME = Path(DEBUG_LOG_GUARD_RELATIVE_PATH).name


def _records(workspace: Workspace) -> list[dict[str, object]]:
    """Read every complete active-log record."""

    path = workspace.path(DEBUG_LOG_RELATIVE_PATH)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _trace(
    log: WorkspaceDebugLog,
    *,
    session_id: str | None = None,
) -> None:
    """Write one representative trace record."""

    log.trace(
        DebugComponent.CLI,
        DebugOperation.CLI_COMMAND,
        DebugPhase.OBSERVED,
        session_id=session_id,
    )


def _trace_payload(log: WorkspaceDebugLog) -> bytes:
    """Capture one serialized record without touching storage."""

    payloads: list[bytes] = []
    fixed_datetime = mock.Mock()
    fixed_datetime.now.return_value = _FIXED_TIME
    with (
        mock.patch.object(debug_module, "datetime", fixed_datetime),
        mock.patch.object(
            WorkspaceDebugLog,
            "_append",
            autospec=True,
            side_effect=lambda _self, payload: payloads.append(payload),
        ),
    ):
        _trace(log)
    if len(payloads) != 1:
        raise AssertionError(f"expected one payload, got {len(payloads)}")
    return payloads[0]


def _rotation_snapshot(workspace: Workspace) -> dict[str, bytes]:
    """Capture active and backup bytes, excluding the persistent guard."""

    directory = workspace.path(DEBUG_LOG_DIRECTORY)
    return {
        path.name: path.read_bytes()
        for path in sorted(directory.iterdir())
        if path.name.startswith("runtime.jsonl") and path.is_file()
    }


def _file_link_snapshot(path: Path) -> tuple[bytes, int, int, int]:
    """Capture exact bytes and filesystem identity for one regular file."""

    value = os.stat(path, follow_symlinks=False)
    return path.read_bytes(), value.st_dev, value.st_ino, value.st_nlink


def _directory_namespace_snapshot(
    directory: Path,
) -> dict[str, tuple[int, int, int, int, bytes | None]]:
    """Capture every directory name, identity, link count, and regular bytes."""

    snapshot: dict[str, tuple[int, int, int, int, bytes | None]] = {}
    for path in sorted(directory.iterdir()):
        value = os.stat(path, follow_symlinks=False)
        snapshot[path.name] = (
            value.st_mode,
            value.st_dev,
            value.st_ino,
            value.st_nlink,
            path.read_bytes() if stat.S_ISREG(value.st_mode) else None,
        )
    return snapshot


def _debug_namespace_snapshot(
    workspace: Workspace,
) -> dict[str, tuple[int, int, int, int, bytes | None]]:
    """Capture every canonical debug-directory entry without mutation."""

    return _directory_namespace_snapshot(workspace.path(DEBUG_LOG_DIRECTORY))


def _root_storage_snapshot(
    root: Path,
) -> tuple[
    dict[str, tuple[int, int, int, int, bytes | None]],
    dict[str, tuple[int, int, int, int, bytes | None]],
]:
    """Capture one root namespace and its complete debug-storage namespace."""

    return (
        _directory_namespace_snapshot(root),
        _directory_namespace_snapshot(root / DEBUG_LOG_DIRECTORY),
    )


def _assert_descriptor_closed(
    test_case: unittest.TestCase,
    descriptor: int,
) -> None:
    """Assert one captured descriptor is closed without relying on procfs."""

    try:
        os.fstat(descriptor)
    except OSError as exc:
        test_case.assertEqual(exc.errno, errno.EBADF)
        return
    os.close(descriptor)
    test_case.fail(f"descriptor {descriptor} remained open")


def _traceback_contains(
    current: TracebackType | None,
    expected: TracebackType,
) -> bool:
    """Return whether one exact injected traceback remains in the final chain."""

    while current is not None:
        if current is expected:
            return True
        current = current.tb_next
    return False


def _capture_raised(
    test_case: unittest.TestCase,
    expected_type: type[BaseException],
    callback: Callable[[], None],
) -> tuple[BaseException, TracebackType]:
    """Call once and retain an exact exception traceback before unittest clears it."""

    try:
        callback()
    except BaseException as caught:
        test_case.assertIsInstance(caught, expected_type)
        assert caught.__traceback__ is not None
        return caught, caught.__traceback__
    test_case.fail(f"{expected_type.__name__} was not raised")
    raise AssertionError("unreachable")


@contextlib.contextmanager
def _held_debug_directory(workspace: Workspace):
    """Yield one production-opened debug directory and close both descriptors."""

    directory = debug_module._prepare_debug_directory(workspace)
    try:
        yield directory
    finally:
        try:
            os.close(directory.descriptor)
        finally:
            os.close(directory.root_descriptor)


class _DebugDirectorySwap:
    """Temporarily replace the canonical debug directory with an outside link."""

    def __init__(self, workspace: Workspace, temporary: Path) -> None:
        self.canonical = workspace.root / DEBUG_LOG_DIRECTORY
        self.parked = temporary / "parked-debug-logs"
        self.outside = temporary / "outside-debug-logs"
        self.outside.mkdir()
        self.swapped = False

    def __enter__(self) -> _DebugDirectorySwap:
        return self

    def activate(self) -> None:
        if self.swapped:
            raise AssertionError("debug directory was already swapped")
        os.rename(self.canonical, self.parked)
        self.canonical.symlink_to(self.outside, target_is_directory=True)
        self.swapped = True

    def canonical_snapshot(self) -> tuple[int, int, int, int, str]:
        value = os.stat(self.canonical, follow_symlinks=False)
        return (
            value.st_mode,
            value.st_dev,
            value.st_ino,
            value.st_nlink,
            os.readlink(self.canonical),
        )

    def __exit__(self, *_exc: object) -> None:
        if not self.swapped:
            return
        self.canonical.unlink()
        os.rename(self.parked, self.canonical)
        self.swapped = False


class _WorkspaceRootSwap:
    """Temporarily replace a bound workspace root without following links."""

    def __init__(
        self,
        test_case: unittest.TestCase,
        workspace: Workspace,
        temporary: Path,
        *,
        replacement_kind: str,
    ) -> None:
        self.test_case = test_case
        self.canonical = workspace.root
        self.parked = temporary / "parked-workspace"
        self.outside = temporary / "outside-workspace"
        self.replacement_kind = replacement_kind
        self.swapped = False
        self.outside.mkdir()
        (self.outside / "outside-sentinel").write_bytes(b"outside root bytes")

    @property
    def replacement(self) -> Path:
        return self.outside if self.replacement_kind == "symlink" else self.canonical

    def __enter__(self) -> _WorkspaceRootSwap:
        return self

    def activate(self) -> None:
        if self.swapped:
            raise AssertionError("workspace root was already swapped")
        os.rename(self.canonical, self.parked)
        if self.replacement_kind == "symlink":
            try:
                self.canonical.symlink_to(
                    self.outside,
                    target_is_directory=True,
                )
            except NotImplementedError as exc:
                os.rename(self.parked, self.canonical)
                self.test_case.skipTest(
                    f"directory symlink creation is unsupported: {exc}"
                )
            except OSError as exc:
                unsupported = {
                    errno.EACCES,
                    errno.EPERM,
                    errno.ENOSYS,
                    getattr(errno, "ENOTSUP", errno.EINVAL),
                    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
                }
                if exc.errno not in unsupported:
                    os.rename(self.parked, self.canonical)
                    raise
                os.rename(self.parked, self.canonical)
                self.test_case.skipTest(
                    f"directory symlink creation is unsupported: {exc}"
                )
        elif self.replacement_kind == "directory":
            self.canonical.mkdir()
        else:
            raise AssertionError(f"unknown replacement kind: {self.replacement_kind}")
        replacement_debug = self.replacement / DEBUG_LOG_DIRECTORY
        replacement_debug.mkdir()
        _seed_debug_storage(
            replacement_debug,
            prefix=f"replacement-{self.replacement_kind}",
        )
        (self.replacement / "replacement-sentinel").write_bytes(
            b"replacement root bytes"
        )
        self.swapped = True

    def canonical_snapshot(self) -> tuple[int, int, int, int, str | None]:
        value = os.stat(self.canonical, follow_symlinks=False)
        return (
            value.st_mode,
            value.st_dev,
            value.st_ino,
            value.st_nlink,
            os.readlink(self.canonical) if stat.S_ISLNK(value.st_mode) else None,
        )

    def __exit__(self, *_exc: object) -> None:
        if not self.swapped:
            return
        if self.replacement_kind == "symlink":
            self.canonical.unlink()
        else:
            shutil.rmtree(self.canonical)
        os.rename(self.parked, self.canonical)
        self.swapped = False


class _ControlledProcessLock:
    """Delegate to a real lock while injecting one post-release failure."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self._lock = threading.Lock()
        self.failure = failure
        self.release_attempts = 0
        self.injection_traceback: TracebackType | None = None

    def acquire(self, *, timeout: float) -> bool:
        return self._lock.acquire(timeout=timeout)

    def release(self) -> None:
        self.release_attempts += 1
        self._lock.release()
        if self.failure is not None and self.release_attempts == 1:
            try:
                raise self.failure
            except BaseException as caught:
                self.injection_traceback = caught.__traceback__
                raise

    def locked(self) -> bool:
        return self._lock.locked()

    def assert_reusable(self, test_case: unittest.TestCase) -> None:
        test_case.assertTrue(self._lock.acquire(blocking=False))
        self._lock.release()


class _AcquisitionBoundaryProcessLock:
    """Control acquisition and cleanup around one real process lock."""

    def __init__(
        self,
        *,
        acquire_result: bool = True,
        acquire_failure: BaseException | None = None,
        release_failure: BaseException | None = None,
        arm_post_acquire: Callable[[], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.acquire_result = acquire_result
        self.acquire_failure = acquire_failure
        self.release_failure = release_failure
        self.arm_post_acquire = arm_post_acquire
        self.acquire_attempts = 0
        self.acquire_timeouts: list[float] = []
        self.release_attempts = 0
        self.acquire_traceback: TracebackType | None = None
        self.release_traceback: TracebackType | None = None

    def acquire(self, *, timeout: float) -> bool:
        self.acquire_attempts += 1
        self.acquire_timeouts.append(timeout)
        if self.acquire_failure is not None:
            try:
                raise self.acquire_failure
            except BaseException as caught:
                self.acquire_traceback = caught.__traceback__
                raise
        if not self.acquire_result:
            return False
        acquired = self._lock.acquire(timeout=timeout)
        if acquired and self.arm_post_acquire is not None:
            self.arm_post_acquire()
        return acquired

    def release(self) -> None:
        self.release_attempts += 1
        self._lock.release()
        if self.release_failure is not None and self.release_attempts == 1:
            try:
                raise self.release_failure
            except BaseException as caught:
                self.release_traceback = caught.__traceback__
                raise

    def locked(self) -> bool:
        return self._lock.locked()

    def hold_externally(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise AssertionError("process lock was already held")

    def release_external_hold(self) -> None:
        self._lock.release()

    def assert_reusable(self, test_case: unittest.TestCase) -> None:
        test_case.assertTrue(self._lock.acquire(blocking=False))
        self._lock.release()


class _AttemptCleanupHarness:
    """Capture one real logging attempt and inject named cleanup failures."""

    def __init__(self, failures: dict[str, BaseException]) -> None:
        self.failures = failures
        self.descriptors: dict[str, int] = {}
        self.close_attempts: list[str] = []
        self.release_attempts = 0
        self.injection_tracebacks: dict[str, TracebackType] = {}
        self._real_prepare = debug_module._prepare_debug_directory
        self._real_open = debug_module._open_verified_regular_file
        self._real_close = os.close
        self._real_release = debug_module._release_advisory_lock

    def prepare(self, workspace: Workspace) -> object:
        directory = self._real_prepare(workspace)
        self.descriptors["root"] = directory.root_descriptor
        self.descriptors["debug"] = directory.descriptor
        return directory

    def open(
        self,
        directory: object,
        filename: str,
        *,
        read_write: bool,
    ) -> int:
        descriptor = self._real_open(
            directory,
            filename,
            read_write=read_write,
        )
        role = "guard" if filename == _GUARD_NAME else "active"
        self.descriptors[role] = descriptor
        return descriptor

    def close(self, descriptor: int) -> None:
        role = next(
            (
                candidate
                for candidate, captured in self.descriptors.items()
                if captured == descriptor
            ),
            f"untracked:{descriptor}",
        )
        self.close_attempts.append(role)
        self._real_close(descriptor)
        failure = self.failures.get(role)
        if failure is not None:
            self._raise(role, failure)

    def release(self, descriptor: int) -> None:
        self.release_attempts += 1
        self._real_release(descriptor)
        failure = self.failures.get("release")
        if failure is not None:
            self._raise("release", failure)

    def _raise(self, role: str, failure: BaseException) -> None:
        try:
            raise failure
        except BaseException as caught:
            assert caught.__traceback__ is not None
            self.injection_tracebacks[role] = caught.__traceback__
            raise

    def assert_closed(self, test_case: unittest.TestCase) -> None:
        test_case.assertEqual(
            set(self.descriptors),
            {"root", "debug", "guard", "active"},
        )
        for role, descriptor in self.descriptors.items():
            test_case.assertEqual(self.close_attempts.count(role), 1)
            _assert_descriptor_closed(test_case, descriptor)


@contextlib.contextmanager
def _instrument_cleanup_attempt(
    harness: _AttemptCleanupHarness,
    process_lock: _ControlledProcessLock,
):
    """Patch one attempt through real resources and controlled cleanup seams."""

    holder = mock.Mock(lock=process_lock)
    with (
        mock.patch.object(
            debug_module,
            "_process_lock_for",
            return_value=holder,
        ),
        mock.patch.object(
            debug_module,
            "_prepare_debug_directory",
            side_effect=harness.prepare,
        ),
        mock.patch.object(
            debug_module,
            "_open_verified_regular_file",
            side_effect=harness.open,
        ),
        mock.patch.object(
            debug_module,
            "_release_advisory_lock",
            side_effect=harness.release,
        ),
        mock.patch.object(
            debug_module.os,
            "close",
            side_effect=harness.close,
        ),
    ):
        yield


def _seed_debug_storage(directory: Path, *, prefix: str) -> None:
    """Populate every fixed storage name with recognizable outside bytes."""

    for filename in (
        _GUARD_NAME,
        _ACTIVE_NAME,
        *(
            f"{_ACTIVE_NAME}.{index}"
            for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1)
        ),
    ):
        (directory / filename).write_bytes(
            f"{prefix}:{filename}".encode("ascii")
        )


def _hard_link_or_skip(
    test_case: unittest.TestCase,
    source: Path,
    destination: Path,
) -> None:
    """Create one real hard link or skip only unsupported environments."""

    before = os.stat(source, follow_symlinks=False)
    unsupported = {
        errno.EACCES,
        errno.EPERM,
        errno.EXDEV,
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        os.link(source, destination)
    except (AttributeError, NotImplementedError) as exc:
        test_case.skipTest(f"hard-link creation is unsupported: {exc}")
    except OSError as exc:
        if exc.errno in unsupported or getattr(exc, "winerror", None) == 1314:
            test_case.skipTest(f"hard-link creation is unsupported: {exc}")
        raise

    source_after = os.stat(source, follow_symlinks=False)
    linked = os.stat(destination, follow_symlinks=False)
    test_case.assertEqual(
        (linked.st_dev, linked.st_ino),
        (source_after.st_dev, source_after.st_ino),
    )
    test_case.assertEqual(source_after.st_nlink, before.st_nlink + 1)
    test_case.assertEqual(linked.st_nlink, source_after.st_nlink)


def _fifo_or_skip(test_case: unittest.TestCase, path: Path) -> None:
    """Create one real FIFO or skip only unsupported environments."""

    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is None:
        test_case.skipTest("FIFO creation is unsupported")
    unsupported = {
        errno.EACCES,
        errno.EPERM,
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    try:
        mkfifo(path, 0o600)
    except (AttributeError, NotImplementedError) as exc:
        test_case.skipTest(f"FIFO creation is unsupported: {exc}")
    except OSError as exc:
        if exc.errno in unsupported:
            test_case.skipTest(f"FIFO creation is unsupported: {exc}")
        raise
    test_case.assertTrue(path.is_fifo())


class DebugLogContractTests(unittest.TestCase):
    """Verify the internal schema, taxonomy, privacy, and import boundary."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.log = WorkspaceDebugLog(self.workspace)

    def test_internal_surface_taxonomy_constants_and_signatures_are_exact(self) -> None:
        self.assertEqual(
            debug_module.__all__,
            [
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
            ],
        )
        self.assertEqual(
            (
                DEBUG_LOG_DIRECTORY,
                DEBUG_LOG_RELATIVE_PATH,
                DEBUG_LOG_GUARD_RELATIVE_PATH,
                DEBUG_LOG_MAX_BYTES,
                DEBUG_LOG_BACKUP_COUNT,
                DEBUG_LOG_MAX_FRAMES,
                DEBUG_LOG_LOCK_TIMEOUT_SECONDS,
            ),
            (
                "debug_logs",
                "debug_logs/runtime.jsonl",
                "debug_logs/.runtime.lock",
                1_048_576,
                5,
                32,
                0.1,
            ),
        )
        expected_members = {
            DebugLevel: (("TRACE", "trace"), ("ERROR", "error")),
            DebugComponent: (
                ("CLI", "cli"),
                ("BACKEND", "backend"),
                ("RUNTIME", "runtime"),
                ("INTERACTIVE_CONTROLLER", "interactive_controller"),
                ("SMOKE", "smoke"),
            ),
            DebugOperation: (
                ("WORKSPACE_DISCOVERY", "workspace_discovery"),
                ("WORKSPACE_INITIALIZATION", "workspace_initialization"),
                ("CLI_CONTEXT", "cli_context"),
                ("CLI_COMMAND", "cli_command"),
                ("INTERACTIVE_LOBBY", "interactive_lobby"),
                ("INTERACTIVE_COMMAND", "interactive_command"),
                ("CONFIGURATION", "configuration"),
                ("BACKEND_LIFECYCLE", "backend_lifecycle"),
                ("SESSION_LIFECYCLE", "session_lifecycle"),
                ("PROVIDER_INVOCATION", "provider_invocation"),
                ("ATTACHMENT", "attachment"),
                ("CONVERSATION_FOCUS", "conversation_focus"),
                ("ADVISORY_TURN", "advisory_turn"),
                ("RETRIEVAL_TURN", "retrieval_turn"),
                ("CONSENSUS_RUN", "consensus_run"),
                ("DIRECTIVE", "directive"),
                ("INSPECTION", "inspection"),
                ("CLEANUP", "cleanup"),
                ("EXECUTION_SETTING_CHANGE", "execution_setting_change"),
                ("EXECUTION_PROFILE_APPLIED", "execution_profile_applied"),
                ("EXECUTION_PROFILE_CONFIRMED", "execution_profile_confirmed"),
                (
                    "EXECUTION_PROFILE_RECOVERY_REQUIRED",
                    "execution_profile_recovery_required",
                ),
            ),
            DebugPhase: (
                ("STARTED", "started"),
                ("OBSERVED", "observed"),
                ("SUCCEEDED", "succeeded"),
                ("FAILED", "failed"),
                ("CANCELLED", "cancelled"),
            ),
            DebugOutcome: (
                ("COMPLETED", "completed"),
                ("CONSENSUS", "consensus"),
                ("DEADLOCK", "deadlock"),
                ("FAILED", "failed"),
                ("INTERRUPTED", "interrupted"),
                ("REJECTED", "rejected"),
                ("READY", "ready"),
                ("PAUSED", "paused"),
                ("CLAIMABLE", "claimable"),
                ("HELD_HERE", "held_here"),
            ),
        }
        for enum_type, members in expected_members.items():
            with self.subTest(enum_type=enum_type.__name__):
                self.assertTrue(issubclass(enum_type, StrEnum))
                self.assertEqual(tuple((item.name, item.value) for item in enum_type), members)

        self.assertEqual(
            tuple(inspect.signature(WorkspaceDebugLog.trace).parameters),
            (
                "self",
                "component",
                "operation",
                "phase",
                "session_id",
                "conversation_id",
                "debate_id",
                "agent_id",
                "outcome",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(WorkspaceDebugLog.error).parameters),
            (
                "self",
                "component",
                "operation",
                "exception",
                "phase",
                "session_id",
                "conversation_id",
                "debate_id",
                "agent_id",
                "outcome",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    WorkspaceDebugLog.execution_profile_event
                ).parameters
            ),
            (
                "self",
                "operation",
                "correlation_id",
                "agent_id",
                "route_generation",
                "old_profile",
                "new_profile",
                "active_profile",
                "activation_state",
                "thread_disposition",
                "context_state",
                "recovery_required",
            ),
        )
        for name in debug_module.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)

    def test_construction_and_read_only_path_perform_no_filesystem_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = Workspace(root)
            before = tuple(root.iterdir())
            log = WorkspaceDebugLog(workspace)
            self.assertEqual(log.path, workspace.root / DEBUG_LOG_RELATIVE_PATH)
            self.assertEqual(tuple(root.iterdir()), before)
            with self.assertRaises(AttributeError):
                log.path = root / "elsewhere"  # type: ignore[misc]
            self.assertEqual(tuple(root.iterdir()), before)

    def test_minimal_and_full_schema_are_exact_and_omit_nulls(self) -> None:
        fixed_datetime = mock.Mock()
        fixed_datetime.now.return_value = _FIXED_TIME
        with mock.patch.object(debug_module, "datetime", fixed_datetime):
            self.log.trace(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                DebugPhase.STARTED,
            )
            self.log.trace(
                DebugComponent.RUNTIME,
                DebugOperation.CONSENSUS_RUN,
                DebugPhase.SUCCEEDED,
                session_id="session_1",
                conversation_id="conversation-1",
                debate_id="debate_1",
                agent_id=ROSTER[0],
                outcome=DebugOutcome.CONSENSUS,
            )
        minimal, full = _records(self.workspace)
        self.assertEqual(
            minimal,
            {
                "schema_version": 1,
                "timestamp": "2026-08-20T01:02:03.456+00:00",
                "level": "trace",
                "component": "cli",
                "operation": "cli_command",
                "phase": "started",
            },
        )
        self.assertEqual(
            full,
            {
                "schema_version": 1,
                "timestamp": "2026-08-20T01:02:03.456+00:00",
                "level": "trace",
                "component": "runtime",
                "operation": "consensus_run",
                "phase": "succeeded",
                "session_id": "session_1",
                "conversation_id": "conversation-1",
                "debate_id": "debate_1",
                "agent_id": ROSTER[0],
                "outcome": "consensus",
            },
        )
        raw = self.log.path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 2)
        self.assertNotIn(b"null", raw)

    def test_schema_v2_profile_transitions_are_exact_correlated_and_private(
        self,
    ) -> None:
        old = AgentExecutionProfile(
            agent_id=ROSTER[0],
            provider="codex",
            provider_source=ExecutionValueSource.BUILT_IN_DEFAULT,
            model=None,
            model_source=ExecutionValueSource.PROVIDER_DEFAULT,
            effort="high",
            effort_source=ExecutionValueSource.SYSTEM_DEFAULT,
        )
        new = AgentExecutionProfile(
            agent_id=ROSTER[0],
            provider="antigravity",
            provider_source=ExecutionValueSource.AGENT_CONFIG,
            model="agy-model",
            model_source=ExecutionValueSource.AGENT_CONFIG,
            effort="medium",
            effort_source=ExecutionValueSource.LAUNCH_OVERRIDE,
        )
        fixed_datetime = mock.Mock()
        fixed_datetime.now.return_value = _FIXED_TIME
        with mock.patch.object(debug_module, "datetime", fixed_datetime):
            self.log.trace(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                DebugPhase.OBSERVED,
            )
            for operation, activation_state, recovery_required in (
                (
                    DebugOperation.EXECUTION_SETTING_CHANGE,
                    "accepted",
                    False,
                ),
                (
                    DebugOperation.EXECUTION_PROFILE_APPLIED,
                    "active",
                    False,
                ),
                (
                    DebugOperation.EXECUTION_PROFILE_RECOVERY_REQUIRED,
                    "recovery_required",
                    True,
                ),
            ):
                self.log.execution_profile_event(
                    operation,
                    correlation_id="settings_save_7",
                    agent_id=ROSTER[0],
                    route_generation=7,
                    old_profile=old,
                    new_profile=new,
                    activation_state=activation_state,
                    recovery_required=recovery_required,
                )
            self.log.execution_profile_event(
                DebugOperation.EXECUTION_PROFILE_CONFIRMED,
                correlation_id="settings_save_7",
                agent_id=ROSTER[0],
                route_generation=7,
                active_profile=new,
                activation_state="confirmed",
                thread_disposition="fresh_after_provider_switch",
                context_state="rehydrated",
            )

        records = _records(self.workspace)
        self.assertEqual([record["schema_version"] for record in records], [1, 2, 2, 2, 2])
        transitions = records[1:4]
        profile_projection = {
            "provider": "antigravity",
            "provider_source": "agent_config",
            "model": "agy-model",
            "model_source": "agent_config",
            "effort": "medium",
            "effort_source": "launch_override",
        }
        old_projection = {
            "provider": "codex",
            "provider_source": "built_in_default",
            "model": None,
            "model_source": "provider_default",
            "effort": "high",
            "effort_source": "system_default",
        }
        self.assertEqual(
            [record["operation"] for record in transitions],
            [
                "execution_setting_change",
                "execution_profile_applied",
                "execution_profile_recovery_required",
            ],
        )
        for record in transitions:
            with self.subTest(operation=record["operation"]):
                expected = {
                    "schema_version": 2,
                    "timestamp": "2026-08-20T01:02:03.456+00:00",
                    "level": (
                        "error"
                        if record["operation"]
                        == "execution_profile_recovery_required"
                        else "trace"
                    ),
                    "component": "backend",
                    "operation": record["operation"],
                    "phase": (
                        "failed"
                        if record["operation"]
                        == "execution_profile_recovery_required"
                        else "succeeded"
                    ),
                    "correlation_id": "settings_save_7",
                    "agent_id": ROSTER[0],
                    "route_generation": 7,
                    "activation_state": record["activation_state"],
                    "old_profile": old_projection,
                    "new_profile": profile_projection,
                }
                self.assertEqual(record, expected)
        confirmed = records[4]
        self.assertEqual(
            confirmed,
            {
                "schema_version": 2,
                "timestamp": "2026-08-20T01:02:03.456+00:00",
                "level": "trace",
                "component": "backend",
                "operation": "execution_profile_confirmed",
                "phase": "succeeded",
                "correlation_id": "settings_save_7",
                "agent_id": ROSTER[0],
                "route_generation": 7,
                "activation_state": "confirmed",
                "active_profile": profile_projection,
                "thread_disposition": "fresh_after_provider_switch",
                "context_state": "rehydrated",
            },
        )
        forbidden = {
            "payload",
            "context",
            "story",
            "prompt",
            "reply",
            "stderr",
            "credentials",
            "argv",
            "provider_output",
            "exception",
        }
        for record in records[1:]:
            self.assertTrue(forbidden.isdisjoint(record))

    def test_invalid_schema_v2_profile_events_drop_without_mutation(self) -> None:
        profile = AgentExecutionProfile(
            agent_id=ROSTER[0],
            provider="codex",
            provider_source=ExecutionValueSource.BUILT_IN_DEFAULT,
            model=None,
            model_source=ExecutionValueSource.PROVIDER_DEFAULT,
            effort=None,
            effort_source=ExecutionValueSource.PROVIDER_DEFAULT,
        )
        calls = (
            lambda: self.log.execution_profile_event(
                DebugOperation.CLI_COMMAND,
                correlation_id="valid",
                agent_id=ROSTER[0],
                route_generation=0,
                active_profile=profile,
                activation_state="active",
            ),
            lambda: self.log.execution_profile_event(
                DebugOperation.EXECUTION_PROFILE_APPLIED,
                correlation_id="../unsafe",
                agent_id=ROSTER[0],
                route_generation=0,
                active_profile=profile,
                activation_state="active",
            ),
            lambda: self.log.execution_profile_event(
                DebugOperation.EXECUTION_PROFILE_APPLIED,
                correlation_id="valid",
                agent_id=ROSTER[0],
                route_generation=-1,
                active_profile=profile,
                activation_state="active",
            ),
            lambda: self.log.execution_profile_event(
                DebugOperation.EXECUTION_PROFILE_APPLIED,
                correlation_id="valid",
                agent_id=ROSTER[1],
                route_generation=0,
                active_profile=profile,
                activation_state="active",
            ),
        )
        for index, call in enumerate(calls):
            with self.subTest(index=index):
                call()
        self.assertFalse(self.log.path.exists())

    def test_invalid_inputs_drop_only_the_record(self) -> None:
        calls = (
            lambda: self.log.trace("cli", DebugOperation.CLI_COMMAND, DebugPhase.STARTED),
            lambda: self.log.trace(DebugComponent.CLI, "cli_command", DebugPhase.STARTED),
            lambda: self.log.trace(DebugComponent.CLI, DebugOperation.CLI_COMMAND, "started"),
            lambda: self.log.trace(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                DebugPhase.STARTED,
                session_id="../unsafe",
            ),
            lambda: self.log.trace(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                DebugPhase.STARTED,
                agent_id="not_an_agent",
            ),
            lambda: self.log.trace(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                DebugPhase.STARTED,
                outcome="completed",
            ),
            lambda: self.log.error(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                "not an exception",
            ),
            lambda: self.log.error(
                DebugComponent.CLI,
                DebugOperation.CLI_COMMAND,
                RuntimeError(),
                phase=DebugPhase.SUCCEEDED,
            ),
        )
        for index, call in enumerate(calls):
            with self.subTest(index=index):
                call()  # type: ignore[misc]
        self.assertFalse(self.log.path.exists())

    def test_exception_projection_excludes_all_prohibited_content(self) -> None:
        class SecretError(RuntimeError):
            def __str__(self) -> str:
                raise AssertionError("exception text must not be read")

            def __repr__(self) -> str:
                raise AssertionError("exception repr must not be read")

        local_secret = "LOCAL_SECRET_9921"
        try:
            cause = ValueError("CAUSE_SECRET_1187")
            error = SecretError("MESSAGE_SECRET_7734", {"payload": "ARG_SECRET_4402"})
            error.add_note("NOTE_SECRET_6630")
            raise error from cause
        except SecretError as caught:
            self.log.error(
                DebugComponent.BACKEND,
                DebugOperation.PROVIDER_INVOCATION,
                caught,
            )
        raw = self.log.path.read_text(encoding="utf-8")
        for prohibited in (
            local_secret,
            "CAUSE_SECRET_1187",
            "MESSAGE_SECRET_7734",
            "ARG_SECRET_4402",
            "NOTE_SECRET_6630",
            str(Path(__file__).resolve()),
            "payload",
        ):
            self.assertNotIn(prohibited, raw)
        exception = _records(self.workspace)[0]["exception"]
        assert isinstance(exception, dict)
        self.assertEqual(set(exception), {"type", "frames", "frames_truncated"})
        self.assertTrue(str(exception["type"]).endswith(".SecretError"))
        frames = exception["frames"]
        assert isinstance(frames, list)
        self.assertTrue(frames)
        for frame in frames:
            self.assertEqual(set(frame), {"module", "function", "line"})
            self.assertIsInstance(frame["line"], int)
            self.assertGreater(frame["line"], 0)

    def test_traceback_retains_exact_latest_32_frames(self) -> None:
        namespace: dict[str, object] = {
            "__name__": "test_debug_frames",
            "ErrorType": RuntimeError,
        }
        source = "\n".join(
            [f"def f{i}(): return f{i + 1}()" for i in range(39)]
            + ["def f39(): raise ErrorType('PRIVATE_MESSAGE')"]
        )
        exec(source, namespace)
        try:
            namespace["f0"]()  # type: ignore[operator]
        except RuntimeError as caught:
            self.log.error(
                DebugComponent.RUNTIME,
                DebugOperation.CONSENSUS_RUN,
                caught,
            )
        exception = _records(self.workspace)[0]["exception"]
        assert isinstance(exception, dict)
        frames = exception["frames"]
        assert isinstance(frames, list)
        self.assertEqual(len(frames), DEBUG_LOG_MAX_FRAMES)
        self.assertEqual(
            [frame["function"] for frame in frames],
            [f"f{i}" for i in range(8, 40)],
        )
        self.assertIs(exception["frames_truncated"], True)
        self.assertNotIn("PRIVATE_MESSAGE", self.log.path.read_text(encoding="utf-8"))

    def test_absent_traceback_and_unsafe_type_names_are_bounded(self) -> None:
        self.log.error(
            DebugComponent.RUNTIME,
            DebugOperation.CLEANUP,
            ValueError("private"),
        )

        class UnsafeNameError(RuntimeError):
            pass

        UnsafeNameError.__module__ = "unsafe\nmodule"
        UnsafeNameError.__qualname__ = "Unsafe\x1bName"
        self.log.error(
            DebugComponent.RUNTIME,
            DebugOperation.CLEANUP,
            UnsafeNameError("private"),
        )
        first, second = _records(self.workspace)
        self.assertEqual(
            first["exception"],
            {"type": "builtins.ValueError", "frames": [], "frames_truncated": False},
        )
        self.assertEqual(
            second["exception"],
            {"type": "unknown", "frames": [], "frames_truncated": False},
        )

    def test_record_construction_serialization_and_append_fail_open_once(self) -> None:
        failures = (
            ("_build_record", WorkspaceDebugLog, "_build_record"),
            ("json", debug_module.json, "dumps"),
            ("append", WorkspaceDebugLog, "_append"),
        )
        for label, owner, attribute in failures:
            with self.subTest(label=label):
                with mock.patch.object(owner, attribute, side_effect=OSError(label)) as failed:
                    _trace(self.log)
                failed.assert_called_once()
        self.assertFalse(self.log.path.exists())

    def test_record_does_not_swallow_process_control_exceptions(self) -> None:
        seams = (
            ("construction", WorkspaceDebugLog, "_build_record"),
            ("serialization", debug_module.json, "dumps"),
            ("append", WorkspaceDebugLog, "_append"),
        )
        for exception_type in (KeyboardInterrupt, SystemExit):
            for label, owner, attribute in seams:
                with self.subTest(
                    exception_type=exception_type.__name__,
                    seam=label,
                ):
                    control = exception_type(f"{label} control")
                    with (
                        mock.patch.object(
                            owner,
                            attribute,
                            side_effect=control,
                        ) as failed,
                        self.assertRaises(exception_type) as raised,
                    ):
                        _trace(self.log)
                    self.assertIs(raised.exception, control)
                    failed.assert_called_once()
        self.assertFalse(self.log.path.exists())

    def test_clean_import_dependency_boundary_and_facade_isolation(self) -> None:
        source = Path(debug_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertEqual(dependencies, {"agents", "provider_adapter", "workspace"})
        for dependency in dependencies:
            dependency_source = (
                Path(debug_module.__file__).with_name(f"{dependency}.py")
            ).read_text(encoding="utf-8")
            self.assertNotIn("from .debug_log import", dependency_source)

        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "from pathlib import Path; "
            "import story_writing_agents as facade; "
            "from story_writing_agents import debug_log as module; "
            "assert all(name not in facade.__all__ for name in module.__all__); "
            "assert module.DEBUG_LOG_RELATIVE_PATH == 'debug_logs/runtime.jsonl'; "
            "assert list(Path.cwd().iterdir()) == []; print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])


class DebugLogStorageTests(unittest.TestCase):
    """Verify retention, filesystem defenses, locking, and rollback."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.log = WorkspaceDebugLog(self.workspace)

    def test_first_write_lazily_creates_directory_guard_and_active_file(self) -> None:
        directory = self.workspace.path(DEBUG_LOG_DIRECTORY)
        self.workspace.path(f"{DEBUG_LOG_DIRECTORY}/.gitkeep").unlink()
        directory.rmdir()
        _trace(self.log)
        self.assertTrue(directory.is_dir())
        self.assertTrue(self.workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH).is_file())
        self.assertEqual(len(_records(self.workspace)), 1)

    def test_exact_threshold_and_one_byte_over_rotate_before_append(self) -> None:
        payload = _trace_payload(self.log)
        active = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        active.write_bytes(b"a" * (DEBUG_LOG_MAX_BYTES - len(payload)))
        _trace(self.log)
        self.assertEqual(active.stat().st_size, DEBUG_LOG_MAX_BYTES)
        self.assertFalse(self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.1").exists())

        active.write_bytes(b"b" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1))
        _trace(self.log)
        self.assertEqual(active.stat().st_size, len(payload))
        self.assertEqual(len(_records(self.workspace)), 1)
        self.assertEqual(
            self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.1").read_bytes(),
            b"b" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1),
        )

    def test_rotation_orders_five_backups_and_discards_only_oldest(self) -> None:
        payload = _trace_payload(self.log)
        active = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        prior_active = b"active" * (DEBUG_LOG_MAX_BYTES // 6)
        active.write_bytes(prior_active)
        prior_active_identity = (
            active.stat().st_dev,
            active.stat().st_ino,
        )
        prior_backups: dict[int, bytes] = {}
        prior_backup_identities: dict[int, tuple[int, int]] = {}
        for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
            value = f"backup-{index}".encode("ascii")
            prior_backups[index] = value
            backup = self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.{index}")
            backup.write_bytes(value)
            backup_stat = backup.stat()
            prior_backup_identities[index] = (
                backup_stat.st_dev,
                backup_stat.st_ino,
            )
        _trace(self.log)
        self.assertEqual(active.stat().st_size, len(payload))
        self.assertEqual(len(_records(self.workspace)), 1)
        first_backup = self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.1")
        self.assertEqual(first_backup.read_bytes(), prior_active)
        self.assertEqual(
            (first_backup.stat().st_dev, first_backup.stat().st_ino),
            prior_active_identity,
        )
        for index in range(2, DEBUG_LOG_BACKUP_COUNT + 1):
            backup = self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.{index}")
            self.assertEqual(
                backup.read_bytes(),
                prior_backups[index - 1],
            )
            self.assertEqual(
                (backup.stat().st_dev, backup.stat().st_ino),
                prior_backup_identities[index - 1],
            )
        artifacts = [
            active,
            *(self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.{index}") for index in range(1, 6)),
        ]
        active_identity = (active.stat().st_dev, active.stat().st_ino)
        self.assertNotIn(
            active_identity,
            {
                (path.stat().st_dev, path.stat().st_ino)
                for path in artifacts[1:]
            },
        )
        self.assertTrue(all(path.stat().st_size <= DEBUG_LOG_MAX_BYTES for path in artifacts))
        self.assertLessEqual(
            sum(path.stat().st_size for path in artifacts),
            6 * DEBUG_LOG_MAX_BYTES,
        )

    def test_rotation_cas_rejects_replaced_sources_and_destinations(self) -> None:
        cases = (
            ("oldest_backup", f"{_ACTIVE_NAME}.5", 2, "replace", True),
            ("source_4", f"{_ACTIVE_NAME}.4", 2, "replace", True),
            ("destination_5", f"{_ACTIVE_NAME}.5", 3, "create", True),
            ("source_3", f"{_ACTIVE_NAME}.3", 2, "replace", True),
            ("destination_4", f"{_ACTIVE_NAME}.4", 3, "create", True),
            ("source_2", f"{_ACTIVE_NAME}.2", 2, "replace", True),
            ("destination_3", f"{_ACTIVE_NAME}.3", 3, "create", True),
            ("source_1", f"{_ACTIVE_NAME}.1", 2, "replace", True),
            ("destination_2", f"{_ACTIVE_NAME}.2", 3, "create", True),
            ("active_source", _ACTIVE_NAME, 2, "replace", True),
            ("active_destination", f"{_ACTIVE_NAME}.1", 3, "create", True),
            (
                "unexpected_absent_destination",
                f"{_ACTIVE_NAME}.5",
                2,
                "create",
                False,
            ),
        )
        for label, target, occurrence, action, seed_oldest in cases:
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                payload = _trace_payload(log)
                workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH).write_bytes(
                    b"safe guard"
                )
                workspace.path(DEBUG_LOG_RELATIVE_PATH).write_bytes(
                    b"a" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1)
                )
                for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
                    if index == DEBUG_LOG_BACKUP_COUNT and not seed_oldest:
                        continue
                    workspace.path(
                        f"{DEBUG_LOG_RELATIVE_PATH}.{index}"
                    ).write_bytes(f"backup-{index}".encode("ascii"))

                process_lock = debug_module._process_lock_for(workspace)
                real_append = debug_module._append_under_guard
                real_validate = debug_module._validate_existing_regular
                calls: dict[str, int] = {}
                raced_snapshot: dict[
                    str,
                    tuple[int, int, int, int, bytes | None],
                ] = {}

                def validate_with_race(
                    directory: object,
                    filename: str,
                ) -> object:
                    calls[filename] = calls.get(filename, 0) + 1
                    if filename == target and calls[filename] == occurrence:
                        target_path = workspace.root / DEBUG_LOG_DIRECTORY / target
                        if action == "replace":
                            self.assertTrue(target_path.exists())
                            staged = (
                                workspace.root
                                / DEBUG_LOG_DIRECTORY
                                / f"replacement-{label}"
                            )
                            staged.write_bytes(f"replacement:{label}".encode("ascii"))
                            os.replace(staged, target_path)
                        else:
                            self.assertFalse(target_path.exists())
                            target_path.write_bytes(
                                f"unexpected:{label}".encode("ascii")
                            )
                        raced_snapshot.update(
                            _debug_namespace_snapshot(workspace)
                        )
                    return real_validate(directory, filename)

                def append_with_race(
                    directory: object,
                    append_payload: bytes,
                ) -> None:
                    with mock.patch.object(
                        debug_module,
                        "_validate_existing_regular",
                        side_effect=validate_with_race,
                    ):
                        real_append(directory, append_payload)

                with mock.patch.object(
                    debug_module,
                    "_append_under_guard",
                    side_effect=append_with_race,
                ):
                    _trace(log, session_id="rotation_cas_dropped")

                self.assertTrue(raced_snapshot)
                self.assertEqual(
                    _debug_namespace_snapshot(workspace),
                    raced_snapshot,
                )
                self.assertNotIn(
                    b"rotation_cas_dropped",
                    b"".join(
                        value[-1] or b""
                        for value in raced_snapshot.values()
                    ),
                )
                self.assertTrue(process_lock.lock.acquire(blocking=False))
                process_lock.lock.release()

    def test_preexisting_oversized_active_or_backup_drops_without_mutation(self) -> None:
        for relative in (
            DEBUG_LOG_RELATIVE_PATH,
            f"{DEBUG_LOG_RELATIVE_PATH}.3",
        ):
            with self.subTest(relative=relative):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                workspace.path(relative).write_bytes(b"x" * (DEBUG_LOG_MAX_BYTES + 1))
                guard = workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
                self.assertFalse(guard.exists())
                before = _debug_namespace_snapshot(workspace)
                _trace(log)
                self.assertEqual(_debug_namespace_snapshot(workspace), before)
                self.assertFalse(guard.exists())

    def test_precheck_rejects_hard_linked_candidates_before_guard_creation(
        self,
    ) -> None:
        candidates = (
            DEBUG_LOG_RELATIVE_PATH,
            *(
                f"{DEBUG_LOG_RELATIVE_PATH}.{index}"
                for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1)
            ),
        )
        for relative in candidates:
            with self.subTest(relative=relative):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                guard = workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
                self.assertFalse(guard.exists())
                with tempfile.TemporaryDirectory(
                    dir=workspace.root.parent
                ) as temporary:
                    outside = Path(temporary) / "outside-candidate"
                    outside.write_bytes(b"outside candidate bytes")
                    _hard_link_or_skip(self, outside, workspace.path(relative))
                    outside_before = _file_link_snapshot(outside)
                    namespace_before = _debug_namespace_snapshot(workspace)

                    _trace(log)

                    self.assertEqual(
                        _file_link_snapshot(outside),
                        outside_before,
                    )
                    self.assertEqual(
                        _debug_namespace_snapshot(workspace),
                        namespace_before,
                    )
                    self.assertFalse(guard.exists())

    def test_post_lock_revalidation_contains_candidate_mutation_after_precheck(
        self,
    ) -> None:
        active = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        real_open = debug_module._open_verified_regular_file
        opened: list[str] = []
        snapshots: dict[
            str,
            object,
        ] = {}

        with tempfile.TemporaryDirectory(
            dir=self.workspace.root.parent
        ) as temporary:
            outside = Path(temporary) / "outside-race"
            outside.write_bytes(b"outside race bytes")
            _hard_link_or_skip(self, outside, active)
            active.unlink()

            def mutate_before_guard_open(
                directory: object,
                filename: str,
                *,
                read_write: bool,
            ) -> int:
                if filename == _GUARD_NAME:
                    os.link(outside, active)
                    snapshots["outside"] = _file_link_snapshot(outside)
                descriptor = real_open(
                    directory,
                    filename,
                    read_write=read_write,
                )
                opened.append(filename)
                if filename == _GUARD_NAME:
                    snapshots["namespace"] = _debug_namespace_snapshot(
                        self.workspace
                    )
                return descriptor

            with mock.patch.object(
                debug_module,
                "_open_verified_regular_file",
                side_effect=mutate_before_guard_open,
            ):
                _trace(self.log)

            self.assertEqual(opened, [_GUARD_NAME])
            self.assertEqual(
                _file_link_snapshot(outside),
                snapshots["outside"],
            )
            self.assertEqual(
                _debug_namespace_snapshot(self.workspace),
                snapshots["namespace"],
            )
            self.assertTrue(
                self.workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH).is_file()
            )
            self.assertEqual(active.read_bytes(), b"outside race bytes")

    def test_rotation_reuses_the_no_follow_active_validation_stat(self) -> None:
        self.workspace.path(DEBUG_LOG_RELATIVE_PATH).write_bytes(b"x")
        payload = b"{}\n"
        validated = mock.Mock(
            st_size=DEBUG_LOG_MAX_BYTES - len(payload) + 1,
            st_dev=7,
            st_ino=11,
        )
        opened = mock.Mock(st_size=0, st_dev=1, st_ino=2, st_nlink=1)

        def validate_candidate(_directory: object, filename: str) -> object:
            return validated if filename == _ACTIVE_NAME else None

        with _held_debug_directory(self.workspace) as directory:
            with (
                mock.patch.object(
                    debug_module,
                    "_validate_existing_regular",
                    side_effect=validate_candidate,
                ) as validate,
                mock.patch.object(debug_module, "_verify_debug_directory"),
                mock.patch.object(debug_module.os, "rename") as rename,
                mock.patch.object(
                    debug_module,
                    "_open_verified_regular_file",
                    return_value=41,
                ),
                mock.patch.object(
                    debug_module,
                    "_verify_open_descriptor",
                    return_value=opened,
                ),
                mock.patch.object(debug_module, "_verify_open_file"),
                mock.patch.object(
                    debug_module.os,
                    "write",
                    return_value=len(payload),
                ),
                mock.patch.object(debug_module.os, "fsync"),
                mock.patch.object(debug_module.os, "close"),
            ):
                debug_module._append_under_guard(directory, payload)

        self.assertEqual(validate.call_count, 17)
        self.assertEqual(
            validate.call_args_list[0],
            mock.call(directory, _ACTIVE_NAME),
        )
        rename.assert_called_once_with(
            _ACTIVE_NAME,
            f"{_ACTIVE_NAME}.1",
            src_dir_fd=directory.descriptor,
            dst_dir_fd=directory.descriptor,
        )

    def test_symlinked_storage_paths_never_mutate_outside_targets(self) -> None:
        cases = (
            ("directory", DEBUG_LOG_DIRECTORY),
            ("guard", DEBUG_LOG_GUARD_RELATIVE_PATH),
            ("active", DEBUG_LOG_RELATIVE_PATH),
            ("backup", f"{DEBUG_LOG_RELATIVE_PATH}.2"),
        )
        for label, relative in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                workspace = initialized_workspace(self)
                outside = Path(temporary) / "outside"
                if label == "directory":
                    outside.mkdir()
                    sentinel = outside / "sentinel"
                    sentinel.write_bytes(b"outside")
                    keep = workspace.path(f"{DEBUG_LOG_DIRECTORY}/.gitkeep")
                    keep.unlink()
                    workspace.path(DEBUG_LOG_DIRECTORY).rmdir()
                    workspace.root.joinpath(DEBUG_LOG_DIRECTORY).symlink_to(
                        outside,
                        target_is_directory=True,
                    )
                else:
                    sentinel = outside
                    sentinel.write_bytes(b"outside")
                    workspace.path(relative).symlink_to(sentinel)
                WorkspaceDebugLog(workspace).trace(
                    DebugComponent.CLI,
                    DebugOperation.CLI_COMMAND,
                    DebugPhase.OBSERVED,
                )
                self.assertEqual(sentinel.read_bytes(), b"outside")

    def test_workspace_root_replacement_before_or_after_log_construction_is_contained(
        self,
    ) -> None:
        for replacement_kind in ("directory", "symlink"):
            for logger_before_swap in (False, True):
                with self.subTest(
                    replacement_kind=replacement_kind,
                    logger_before_swap=logger_before_swap,
                ):
                    workspace = initialized_workspace(self)
                    log = WorkspaceDebugLog(workspace) if logger_before_swap else None
                    with tempfile.TemporaryDirectory(
                        dir=workspace.root.parent
                    ) as temporary, _WorkspaceRootSwap(
                        self,
                        workspace,
                        Path(temporary),
                        replacement_kind=replacement_kind,
                    ) as swap:
                        swap.activate()
                        if log is None:
                            log = WorkspaceDebugLog(workspace)
                        canonical_before = swap.canonical_snapshot()
                        parked_before = _root_storage_snapshot(swap.parked)
                        replacement_before = _root_storage_snapshot(
                            swap.replacement
                        )
                        outside_before = _directory_namespace_snapshot(
                            swap.outside
                        )

                        _trace(log, session_id="root_replacement_dropped")

                        self.assertEqual(
                            swap.canonical_snapshot(),
                            canonical_before,
                        )
                        self.assertEqual(
                            _root_storage_snapshot(swap.parked),
                            parked_before,
                        )
                        self.assertEqual(
                            _root_storage_snapshot(swap.replacement),
                            replacement_before,
                        )
                        self.assertEqual(
                            _directory_namespace_snapshot(swap.outside),
                            outside_before,
                        )
                        for snapshot in (
                            parked_before[1],
                            replacement_before[1],
                        ):
                            self.assertNotIn(
                                b"root_replacement_dropped",
                                b"".join(
                                    value[-1] or b""
                                    for value in snapshot.values()
                                ),
                            )

                    _trace(log, session_id="root_replacement_recovered")
                    self.assertEqual(
                        [record["session_id"] for record in _records(workspace)],
                        ["root_replacement_recovered"],
                    )

    def test_workspace_root_swap_after_root_or_debug_acquisition_closes_resources(
        self,
    ) -> None:
        for stage in ("root", "debug"):
            for replacement_kind in ("directory", "symlink"):
                with self.subTest(
                    stage=stage,
                    replacement_kind=replacement_kind,
                ):
                    workspace = initialized_workspace(self)
                    log = WorkspaceDebugLog(workspace)
                    real_open_root = debug_module.open_workspace_root_directory
                    real_prepare = debug_module._prepare_debug_directory
                    real_close = os.close
                    descriptors: dict[str, int] = {}
                    closed: list[int] = []
                    captured: dict[str, object] = {}

                    def track_close(descriptor: int) -> None:
                        closed.append(descriptor)
                        real_close(descriptor)

                    with tempfile.TemporaryDirectory(
                        dir=workspace.root.parent
                    ) as temporary, _WorkspaceRootSwap(
                        self,
                        workspace,
                        Path(temporary),
                        replacement_kind=replacement_kind,
                    ) as swap:

                        def activate_and_capture() -> None:
                            swap.activate()
                            captured["canonical"] = swap.canonical_snapshot()
                            captured["parked"] = _root_storage_snapshot(
                                swap.parked
                            )
                            captured["replacement"] = _root_storage_snapshot(
                                swap.replacement
                            )

                        def open_root_then_swap(candidate: Workspace) -> int:
                            descriptor = real_open_root(candidate)
                            descriptors["root"] = descriptor
                            activate_and_capture()
                            return descriptor

                        def prepare_debug_then_swap(candidate: Workspace) -> object:
                            directory = real_prepare(candidate)
                            descriptors["root"] = directory.root_descriptor
                            descriptors["debug"] = directory.descriptor
                            activate_and_capture()
                            return directory

                        root_patch = (
                            mock.patch.object(
                                debug_module,
                                "open_workspace_root_directory",
                                side_effect=open_root_then_swap,
                            )
                            if stage == "root"
                            else contextlib.nullcontext()
                        )
                        prepare_patch = (
                            mock.patch.object(
                                debug_module,
                                "_prepare_debug_directory",
                                side_effect=prepare_debug_then_swap,
                            )
                            if stage == "debug"
                            else contextlib.nullcontext()
                        )
                        with (
                            root_patch,
                            prepare_patch,
                            mock.patch.object(
                                debug_module,
                                "_open_verified_regular_file",
                                side_effect=AssertionError(
                                    "swapped root reached file mutation"
                                ),
                            ) as opened_file,
                            mock.patch.object(
                                debug_module.os,
                                "close",
                                side_effect=track_close,
                            ),
                        ):
                            _trace(log, session_id="root_acquisition_dropped")

                        opened_file.assert_not_called()
                        self.assertEqual(
                            swap.canonical_snapshot(),
                            captured["canonical"],
                        )
                        self.assertEqual(
                            _root_storage_snapshot(swap.parked),
                            captured["parked"],
                        )
                        self.assertEqual(
                            _root_storage_snapshot(swap.replacement),
                            captured["replacement"],
                        )
                        self.assertEqual(
                            set(descriptors),
                            {"root"} if stage == "root" else {"root", "debug"},
                        )
                        for descriptor in descriptors.values():
                            self.assertEqual(closed.count(descriptor), 1)
                            _assert_descriptor_closed(self, descriptor)

                    _trace(log, session_id="root_acquisition_recovered")
                    self.assertEqual(
                        [record["session_id"] for record in _records(workspace)],
                        ["root_acquisition_recovered"],
                    )

    @unittest.skipUnless(
        os.name == "posix",
        "requires renaming an open directory and creating a directory symlink",
    )
    def test_parent_swap_before_guard_open_drops_and_closes_directory(
        self,
    ) -> None:
        real_prepare = debug_module._prepare_debug_directory
        real_close = os.close
        closed: list[int] = []
        held: list[object] = []
        captured: dict[str, object] = {}

        def track_close(descriptor: int) -> None:
            closed.append(descriptor)
            real_close(descriptor)

        with tempfile.TemporaryDirectory(
            dir=self.workspace.root.parent
        ) as temporary, _DebugDirectorySwap(
            self.workspace,
            Path(temporary),
        ) as swap:
            _seed_debug_storage(swap.outside, prefix="outside-before-guard")
            outside_before = _directory_namespace_snapshot(swap.outside)

            def prepare_then_swap(workspace: Workspace) -> object:
                directory = real_prepare(workspace)
                held.append(directory)
                swap.activate()
                captured["canonical"] = swap.canonical_snapshot()
                captured["parked"] = _directory_namespace_snapshot(
                    swap.parked
                )
                return directory

            with (
                mock.patch.object(
                    debug_module,
                    "_prepare_debug_directory",
                    side_effect=prepare_then_swap,
                ),
                mock.patch.object(
                    debug_module,
                    "_open_verified_regular_file",
                    side_effect=AssertionError(
                        "swapped parent reached guard mutation"
                    ),
                ) as opened,
                mock.patch.object(
                    debug_module.os,
                    "close",
                    side_effect=track_close,
                ),
            ):
                _trace(self.log, session_id="dropped_before_guard")

            opened.assert_not_called()
            self.assertEqual(len(held), 1)
            directory = held[0]
            self.assertEqual(closed.count(directory.descriptor), 1)
            self.assertEqual(swap.canonical_snapshot(), captured["canonical"])
            self.assertEqual(
                _directory_namespace_snapshot(swap.parked),
                captured["parked"],
            )
            self.assertEqual(
                _directory_namespace_snapshot(swap.outside),
                outside_before,
            )
            self.assertNotIn(
                b"dropped_before_guard",
                b"".join(
                    value[-1] or b""
                    for snapshot in (
                        captured["parked"],
                        outside_before,
                    )
                    for value in snapshot.values()
                ),
            )

        _trace(self.log, session_id="recovered_after_guard_swap")
        self.assertEqual(
            [record["session_id"] for record in _records(self.workspace)],
            ["recovered_after_guard_swap"],
        )

    @unittest.skipUnless(
        os.name == "posix",
        "requires renaming an open directory and creating a directory symlink",
    )
    def test_parent_swap_before_append_or_rotation_preserves_namespaces(
        self,
    ) -> None:
        for rotates in (False, True):
            with self.subTest(rotates=rotates):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                payload = _trace_payload(log)
                workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH).write_bytes(
                    b"safe guard"
                )
                if rotates:
                    workspace.path(DEBUG_LOG_RELATIVE_PATH).write_bytes(
                        b"a" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1)
                    )
                    for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
                        workspace.path(
                            f"{DEBUG_LOG_RELATIVE_PATH}.{index}"
                        ).write_bytes(f"backup-{index}".encode("ascii"))

                real_append = debug_module._append_under_guard
                real_open = debug_module._open_verified_regular_file
                real_close = os.close
                opened: dict[str, int] = {}
                attempted: list[str] = []
                directory_descriptors: set[int] = set()
                closed: list[int] = []
                captured: dict[str, object] = {}

                def track_open(
                    directory: object,
                    filename: str,
                    *,
                    read_write: bool,
                ) -> int:
                    attempted.append(filename)
                    directory_descriptors.add(directory.descriptor)
                    descriptor = real_open(
                        directory,
                        filename,
                        read_write=read_write,
                    )
                    opened[filename] = descriptor
                    return descriptor

                def track_close(descriptor: int) -> None:
                    closed.append(descriptor)
                    real_close(descriptor)

                with tempfile.TemporaryDirectory(
                    dir=workspace.root.parent
                ) as temporary, _DebugDirectorySwap(
                    workspace,
                    Path(temporary),
                ) as swap:
                    _seed_debug_storage(
                        swap.outside,
                        prefix=f"outside-rotation-{rotates}",
                    )
                    outside_before = _directory_namespace_snapshot(
                        swap.outside
                    )

                    def swap_then_append(
                        directory: object,
                        append_payload: bytes,
                    ) -> None:
                        swap.activate()
                        captured["canonical"] = swap.canonical_snapshot()
                        captured["parked"] = _directory_namespace_snapshot(
                            swap.parked
                        )
                        real_append(directory, append_payload)

                    with (
                        mock.patch.object(
                            debug_module,
                            "_open_verified_regular_file",
                            side_effect=track_open,
                        ),
                        mock.patch.object(
                            debug_module,
                            "_append_under_guard",
                            side_effect=swap_then_append,
                        ),
                        mock.patch.object(
                            debug_module.os,
                            "close",
                            side_effect=track_close,
                        ),
                    ):
                        _trace(
                            log,
                            session_id=f"dropped_parent_rotation_{rotates}",
                        )

                    self.assertEqual(set(opened), {_GUARD_NAME})
                    self.assertEqual(len(directory_descriptors), 1)
                    directory_descriptor = next(iter(directory_descriptors))
                    self.assertEqual(closed.count(opened[_GUARD_NAME]), 1)
                    self.assertEqual(closed.count(directory_descriptor), 1)
                    self.assertEqual(attempted[0], _GUARD_NAME)
                    self.assertEqual(
                        swap.canonical_snapshot(),
                        captured["canonical"],
                    )
                    self.assertEqual(
                        _directory_namespace_snapshot(swap.parked),
                        captured["parked"],
                    )
                    self.assertEqual(
                        _directory_namespace_snapshot(swap.outside),
                        outside_before,
                    )

                _trace(
                    log,
                    session_id=f"recovered_parent_rotation_{rotates}",
                )
                self.assertEqual(
                    [record["session_id"] for record in _records(workspace)],
                    [f"recovered_parent_rotation_{rotates}"],
                )

    def test_directory_identity_mismatch_is_rejected_portably(self) -> None:
        directory = debug_module._DebugDirectory(self.workspace, 17, 18)
        opened = mock.Mock(
            st_mode=stat.S_IFDIR | 0o700,
            st_dev=3,
            st_ino=4,
        )
        named = mock.Mock(
            st_mode=stat.S_IFDIR | 0o700,
            st_dev=3,
            st_ino=5,
        )

        with (
            mock.patch.object(
                debug_module,
                "verify_workspace_root_directory",
            ) as verify_root,
            mock.patch.object(debug_module.os, "fstat", return_value=opened),
            mock.patch.object(
                debug_module.os,
                "stat",
                return_value=named,
            ) as named_stat,
            self.assertRaises(OSError),
        ):
            debug_module._verify_debug_directory(directory)

        verify_root.assert_called_once_with(self.workspace, 17)
        named_stat.assert_called_once_with(
            DEBUG_LOG_DIRECTORY,
            dir_fd=17,
            follow_symlinks=False,
        )

    def test_unsupported_directory_relative_platform_drops_and_recovers(
        self,
    ) -> None:
        cases: tuple[tuple[str, str, object], ...] = (
            ("O_DIRECTORY", "flag", "O_DIRECTORY"),
            ("O_NOFOLLOW", "flag", "O_NOFOLLOW"),
            ("mkdir_dir_fd", "dir_fd", os.mkdir),
            ("open_dir_fd", "dir_fd", os.open),
            ("stat_dir_fd", "dir_fd", os.stat),
            ("unlink_dir_fd", "dir_fd", os.unlink),
            ("rename_dir_fd", "dir_fd", os.rename),
            ("stat_no_follow", "follow", os.stat),
        )
        for label, capability, value in cases:
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                directory = workspace.path(DEBUG_LOG_DIRECTORY)
                workspace.path(f"{DEBUG_LOG_DIRECTORY}/.gitkeep").unlink()
                directory.rmdir()
                before = _directory_namespace_snapshot(workspace.root)

                with contextlib.ExitStack() as stack:
                    if capability == "flag":
                        stack.enter_context(
                            mock.patch.object(debug_module.os, str(value), 0)
                        )
                    elif capability == "dir_fd":
                        supported = set(os.supports_dir_fd)
                        self.assertIn(value, supported)
                        stack.enter_context(
                            mock.patch.object(
                                debug_module.os,
                                "supports_dir_fd",
                                supported.difference({value}),
                            )
                        )
                    else:
                        supported = set(os.supports_follow_symlinks)
                        self.assertIn(value, supported)
                        stack.enter_context(
                            mock.patch.object(
                                debug_module.os,
                                "supports_follow_symlinks",
                                supported.difference({value}),
                            )
                        )
                    _trace(log, session_id=f"unsupported_{label}")

                self.assertEqual(
                    _directory_namespace_snapshot(workspace.root),
                    before,
                )
                self.assertFalse(directory.exists())
                _trace(log, session_id=f"recovered_{label}")
                self.assertEqual(
                    [record["session_id"] for record in _records(workspace)],
                    [f"recovered_{label}"],
                )

    @unittest.skipUnless(
        os.name == "posix",
        "requires a real POSIX FIFO raced into the active leaf",
    )
    def test_raced_fifo_active_leaf_returns_promptly_without_record(self) -> None:
        active = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        with tempfile.TemporaryDirectory(
            dir=self.workspace.root.parent
        ) as temporary:
            probe = Path(temporary) / "fifo-probe"
            _fifo_or_skip(self, probe)
            probe.unlink()

        real_validate = debug_module._validate_existing_regular
        active_validations = 0
        raced_snapshot: dict[
            str,
            tuple[int, int, int, int, bytes | None],
        ] = {}

        def race_after_prevalidation(
            directory: object,
            filename: str,
        ) -> object:
            nonlocal active_validations
            result = real_validate(directory, filename)
            if filename == _ACTIVE_NAME:
                active_validations += 1
                if active_validations == 2:
                    os.mkfifo(active, 0o600)
                    raced_snapshot.update(
                        _debug_namespace_snapshot(self.workspace)
                    )
            return result

        worker = threading.Thread(
            target=_trace,
            args=(self.log,),
            kwargs={"session_id": "raced_fifo"},
            daemon=True,
        )
        with mock.patch.object(
            debug_module,
            "_validate_existing_regular",
            side_effect=race_after_prevalidation,
        ):
            worker.start()
            worker.join(timeout=0.5)
            blocked = worker.is_alive()
            reader: int | None = None
            if blocked:
                reader = os.open(active, os.O_RDONLY | os.O_NONBLOCK)
                worker.join(timeout=1)
            if reader is not None:
                os.close(reader)

        self.assertFalse(blocked, "raced FIFO open blocked diagnostic return")
        self.assertFalse(worker.is_alive())
        self.assertEqual(active_validations, 2)
        self.assertTrue(raced_snapshot)
        self.assertEqual(
            _debug_namespace_snapshot(self.workspace),
            raced_snapshot,
        )
        self.assertTrue(active.is_fifo())
        self.assertNotIn(b"raced_fifo", b"".join(
            value[-1] or b"" for value in raced_snapshot.values()
        ))

    def test_open_verification_rejects_each_unsafe_link_count(self) -> None:
        candidate = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        candidate.write_bytes(b"candidate")
        ordinary = os.stat(candidate, follow_symlinks=False)

        def file_stat(link_count: int) -> mock.Mock:
            return mock.Mock(
                st_mode=ordinary.st_mode,
                st_dev=ordinary.st_dev,
                st_ino=ordinary.st_ino,
                st_nlink=link_count,
            )

        with _held_debug_directory(self.workspace) as directory:
            for opened_links, named_links in (
                (0, 1),
                (2, 1),
                (1, 0),
                (1, 2),
            ):
                with (
                    self.subTest(
                        opened_links=opened_links,
                        named_links=named_links,
                    ),
                    mock.patch.object(
                        debug_module,
                        "_verify_debug_directory",
                    ),
                    mock.patch.object(
                        debug_module.os,
                        "fstat",
                        return_value=file_stat(opened_links),
                    ),
                    mock.patch.object(
                        debug_module.os,
                        "stat",
                        return_value=file_stat(named_links),
                    ) as named_stat,
                    self.assertRaises(OSError),
                ):
                    debug_module._verify_open_file(
                        17,
                        directory,
                        _ACTIVE_NAME,
                    )
                if opened_links == 1:
                    named_stat.assert_called_once_with(
                        _ACTIVE_NAME,
                        dir_fd=directory.descriptor,
                        follow_symlinks=False,
                    )
                else:
                    named_stat.assert_not_called()

            with (
                mock.patch.object(debug_module, "_verify_debug_directory"),
                mock.patch.object(
                    debug_module.os,
                    "fstat",
                    return_value=file_stat(1),
                ),
                mock.patch.object(
                    debug_module.os,
                    "stat",
                    return_value=file_stat(1),
                ),
            ):
                debug_module._verify_open_file(
                    17,
                    directory,
                    _ACTIVE_NAME,
                )

    def test_hard_linked_guard_drops_without_outside_or_namespace_mutation(
        self,
    ) -> None:
        guard = self.workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
        with tempfile.TemporaryDirectory(
            dir=self.workspace.root.parent
        ) as temporary:
            outside = Path(temporary) / "outside-guard"
            outside.write_bytes(b"outside guard bytes")
            _hard_link_or_skip(self, outside, guard)
            outside_before = _file_link_snapshot(outside)
            namespace_before = _debug_namespace_snapshot(self.workspace)

            with mock.patch.object(
                debug_module,
                "_open_verified_regular_file",
                side_effect=AssertionError("unsafe guard reached open"),
            ) as opened:
                _trace(self.log)

            opened.assert_not_called()
            self.assertEqual(_file_link_snapshot(outside), outside_before)
            self.assertEqual(
                _debug_namespace_snapshot(self.workspace),
                namespace_before,
            )
            self.assertFalse(self.log.path.exists())

    def test_fifo_guard_is_rejected_before_open_without_blocking(self) -> None:
        guard = self.workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
        _fifo_or_skip(self, guard)
        namespace_before = _debug_namespace_snapshot(self.workspace)

        with mock.patch.object(
            debug_module,
            "_open_verified_regular_file",
            side_effect=AssertionError("FIFO guard reached open"),
        ) as opened:
            _trace(self.log)

        opened.assert_not_called()
        self.assertEqual(
            _debug_namespace_snapshot(self.workspace),
            namespace_before,
        )
        self.assertTrue(guard.is_fifo())
        self.assertFalse(self.log.path.exists())

    def test_oversized_single_link_guard_remains_usable_and_unmodified(
        self,
    ) -> None:
        guard = self.workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
        guard.write_bytes(b"g" * (DEBUG_LOG_MAX_BYTES + 1))
        guard_before = _file_link_snapshot(guard)
        self.assertEqual(guard_before[3], 1)

        _trace(self.log, session_id="oversized_guard")

        self.assertEqual(_file_link_snapshot(guard), guard_before)
        self.assertEqual(
            [record["session_id"] for record in _records(self.workspace)],
            ["oversized_guard"],
        )

    @unittest.skipUnless(
        os.name == "posix",
        "requires replacing an open guard pathname",
    )
    def test_guard_mutations_after_precheck_are_rejected_by_later_identity_checks(
        self,
    ) -> None:
        for stage in ("post_open", "post_lock"):
            with self.subTest(stage=stage):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                guard = workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
                active = workspace.path(DEBUG_LOG_RELATIVE_PATH)
                replacement = workspace.path(
                    f"{DEBUG_LOG_DIRECTORY}/replacement-{stage}"
                )
                guard.write_bytes(b"original guard")
                replacement.write_bytes(f"replacement {stage}".encode("ascii"))
                namespace_after_mutation: dict[
                    str,
                    tuple[int, int, int, int, bytes | None],
                ] = {}

                def replace_guard() -> None:
                    os.replace(replacement, guard)
                    namespace_after_mutation.update(
                        _debug_namespace_snapshot(workspace)
                    )

                append_patch = mock.patch.object(
                    debug_module,
                    "_append_under_guard",
                    side_effect=AssertionError("mutated guard reached append"),
                )
                if stage == "post_open":
                    real_verify = debug_module._verify_open_file

                    def verify_after_replace(
                        descriptor: int,
                        directory: object,
                        filename: str,
                    ) -> None:
                        if filename == _GUARD_NAME:
                            replace_guard()
                        real_verify(descriptor, directory, filename)

                    mutation_patch = mock.patch.object(
                        debug_module,
                        "_verify_open_file",
                        side_effect=verify_after_replace,
                    )
                    acquire_patch = mock.patch.object(
                        debug_module,
                        "_acquire_advisory_lock",
                        side_effect=AssertionError(
                            "post-open mutation reached locking"
                        ),
                    )
                else:

                    def lock_then_replace(
                        _descriptor: int,
                        *,
                        timeout_seconds: float,
                    ) -> bool:
                        self.assertGreaterEqual(timeout_seconds, 0.0)
                        replace_guard()
                        return True

                    mutation_patch = mock.patch.object(
                        debug_module,
                        "_acquire_advisory_lock",
                        side_effect=lock_then_replace,
                    )
                    acquire_patch = contextlib.nullcontext()

                with (
                    mutation_patch,
                    acquire_patch as acquire,
                    append_patch as append,
                ):
                    _trace(log)

                if stage == "post_open":
                    acquire.assert_not_called()
                append.assert_not_called()
                self.assertTrue(namespace_after_mutation)
                self.assertEqual(
                    _debug_namespace_snapshot(workspace),
                    namespace_after_mutation,
                )
                self.assertFalse(active.exists())

    def test_hard_linked_active_drops_append_and_rotation_without_mutation(
        self,
    ) -> None:
        for rotates in (False, True):
            with self.subTest(rotates=rotates):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                payload = _trace_payload(log)
                workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH).write_bytes(
                    b"safe guard"
                )
                if rotates:
                    outside_bytes = b"a" * (
                        DEBUG_LOG_MAX_BYTES - len(payload) + 1
                    )
                    for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
                        workspace.path(
                            f"{DEBUG_LOG_RELATIVE_PATH}.{index}"
                        ).write_bytes(f"backup-{index}".encode("ascii"))
                else:
                    outside_bytes = b"outside active bytes"
                with tempfile.TemporaryDirectory(
                    dir=workspace.root.parent
                ) as temporary:
                    outside = Path(temporary) / "outside-active"
                    outside.write_bytes(outside_bytes)
                    _hard_link_or_skip(
                        self,
                        outside,
                        workspace.path(DEBUG_LOG_RELATIVE_PATH),
                    )
                    self.assertEqual(
                        len(outside_bytes) + len(payload) > DEBUG_LOG_MAX_BYTES,
                        rotates,
                    )
                    outside_before = _file_link_snapshot(outside)
                    namespace_before = _debug_namespace_snapshot(workspace)

                    _trace(log)

                    self.assertEqual(
                        _file_link_snapshot(outside),
                        outside_before,
                    )
                    self.assertEqual(
                        _debug_namespace_snapshot(workspace),
                        namespace_before,
                    )

    def test_hard_linked_backups_drop_rotation_without_mutation(self) -> None:
        for linked_index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
            with self.subTest(linked_index=linked_index):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                payload = _trace_payload(log)
                workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH).write_bytes(
                    b"safe guard"
                )
                active = workspace.path(DEBUG_LOG_RELATIVE_PATH)
                active.write_bytes(
                    b"a" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1)
                )
                with tempfile.TemporaryDirectory(
                    dir=workspace.root.parent
                ) as temporary:
                    outside = Path(temporary) / f"outside-backup-{linked_index}"
                    outside.write_bytes(
                        f"outside backup {linked_index}".encode("ascii")
                    )
                    for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
                        backup = workspace.path(
                            f"{DEBUG_LOG_RELATIVE_PATH}.{index}"
                        )
                        if index == linked_index:
                            _hard_link_or_skip(self, outside, backup)
                        else:
                            backup.write_bytes(
                                f"backup-{index}".encode("ascii")
                            )
                    outside_before = _file_link_snapshot(outside)
                    namespace_before = _debug_namespace_snapshot(workspace)

                    _trace(log)

                    self.assertEqual(
                        _file_link_snapshot(outside),
                        outside_before,
                    )
                    self.assertEqual(
                        _debug_namespace_snapshot(workspace),
                        namespace_before,
                    )

    def test_nonregular_storage_paths_drop_without_raising(self) -> None:
        for relative in (
            DEBUG_LOG_GUARD_RELATIVE_PATH,
            DEBUG_LOG_RELATIVE_PATH,
            f"{DEBUG_LOG_RELATIVE_PATH}.4",
        ):
            with self.subTest(relative=relative):
                workspace = initialized_workspace(self)
                workspace.path(relative).mkdir()
                guard = workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
                before = _debug_namespace_snapshot(workspace)
                with mock.patch.object(
                    debug_module,
                    "_open_verified_regular_file",
                    side_effect=AssertionError(
                        "nonregular path reached open"
                    ),
                ) as opened:
                    _trace(WorkspaceDebugLog(workspace))
                opened.assert_not_called()
                self.assertEqual(_debug_namespace_snapshot(workspace), before)
                self.assertTrue(workspace.path(relative).is_dir())
                if relative != DEBUG_LOG_GUARD_RELATIVE_PATH:
                    self.assertFalse(guard.exists())

    def test_threads_using_independent_handles_leave_complete_unique_lines(self) -> None:
        ready = threading.Barrier(9)
        attempted = {f"thread_{index}" for index in range(8)}

        def write(session_id: str) -> None:
            ready.wait(timeout=5)
            _trace(WorkspaceDebugLog(self.workspace), session_id=session_id)

        threads = [threading.Thread(target=write, args=(session,)) for session in attempted]
        for thread in threads:
            thread.start()
        ready.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        raw = self.log.path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        records = _records(self.workspace)
        persisted = [record["session_id"] for record in records]
        self.assertTrue(persisted)
        self.assertEqual(len(persisted), len(set(persisted)))
        self.assertTrue(set(persisted).issubset(attempted))

    def test_concurrent_processes_leave_only_complete_unique_lines(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        script = (
            "import sys; "
            "from story_writing_agents.debug_log import "
            "DebugComponent,DebugOperation,DebugPhase,WorkspaceDebugLog; "
            "from story_writing_agents.workspace import Workspace; "
            "WorkspaceDebugLog(Workspace(sys.argv[1])).trace("
            "DebugComponent.CLI,DebugOperation.CLI_COMMAND,DebugPhase.OBSERVED,"
            "session_id=sys.argv[2])"
        )
        attempted = {f"process_{index}" for index in range(6)}
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(self.workspace.root),
                    session_id,
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            for session_id in attempted
        ]
        for process in processes:
            _stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            if process.stderr is not None:
                process.stderr.close()

        raw = self.log.path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), len(_records(self.workspace)))
        persisted = [record["session_id"] for record in _records(self.workspace)]
        self.assertTrue(persisted)
        self.assertEqual(len(persisted), len(set(persisted)))
        self.assertTrue(set(persisted).issubset(attempted))

    def test_process_lock_nonownership_and_acquire_controls_do_not_release_or_store(
        self,
    ) -> None:
        cases: tuple[tuple[str, BaseException | None], ...] = (
            ("not_acquired", None),
            ("keyboard_interrupt", KeyboardInterrupt("PRIVATE_ACQUIRE_CONTROL")),
            ("system_exit", SystemExit("PRIVATE_ACQUIRE_CONTROL")),
        )
        storage_seams = (
            "_prepare_debug_directory",
            "_validate_existing_regular",
            "_validate_existing_guard",
            "_verify_debug_directory",
            "_open_verified_regular_file",
            "_acquire_advisory_lock",
            "_append_under_guard",
        )
        for label, control in cases:
            with self.subTest(label=label):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                process_lock = _AcquisitionBoundaryProcessLock(
                    acquire_result=False,
                    acquire_failure=control,
                )
                process_lock.hold_externally()
                holder = mock.Mock(lock=process_lock)

                with mock.patch.object(
                    debug_module,
                    "_process_lock_for",
                    return_value=holder,
                ):
                    with contextlib.ExitStack() as stack:
                        storage = {
                            seam: stack.enter_context(
                                mock.patch.object(debug_module, seam)
                            )
                            for seam in storage_seams
                        }
                        close = stack.enter_context(
                            mock.patch.object(debug_module.os, "close")
                        )
                        if control is None:
                            _trace(log, session_id=f"dropped_{label}")
                        else:
                            caught, final_traceback = _capture_raised(
                                self,
                                type(control),
                                lambda: _trace(
                                    log,
                                    session_id=f"dropped_{label}",
                                ),
                            )
                            self.assertIs(caught, control)
                            self.assertIsNone(control.__cause__)
                            self.assertIsNone(control.__context__)
                            assert process_lock.acquire_traceback is not None
                            self.assertTrue(
                                _traceback_contains(
                                    final_traceback,
                                    process_lock.acquire_traceback,
                                )
                            )

                    for operation in (*storage.values(), close):
                        operation.assert_not_called()
                    self.assertEqual(process_lock.acquire_attempts, 1)
                    self.assertEqual(
                        process_lock.acquire_timeouts,
                        [DEBUG_LOG_LOCK_TIMEOUT_SECONDS],
                    )
                    self.assertEqual(process_lock.release_attempts, 0)
                    self.assertTrue(process_lock.locked())
                    process_lock.release_external_hold()
                    self.assertFalse(process_lock.locked())
                    process_lock.assert_reusable(self)
                    self.assertFalse(log.path.exists())

                    process_lock.acquire_failure = None
                    process_lock.acquire_result = True
                    _trace(log, session_id=f"recovered_{label}")

                self.assertEqual(process_lock.acquire_attempts, 2)
                self.assertEqual(process_lock.release_attempts, 1)
                process_lock.assert_reusable(self)
                self.assertEqual(
                    [record["session_id"] for record in _records(workspace)],
                    [f"recovered_{label}"],
                )

    def test_first_post_acquire_controls_release_once_and_body_control_wins(
        self,
    ) -> None:
        cases = (
            (
                KeyboardInterrupt("PRIVATE_POST_ACQUIRE_BODY"),
                SystemExit("PRIVATE_PROCESS_LOCK_CLEANUP"),
            ),
            (
                SystemExit("PRIVATE_POST_ACQUIRE_BODY"),
                KeyboardInterrupt("PRIVATE_PROCESS_LOCK_CLEANUP"),
            ),
        )
        storage_seams = (
            "_prepare_debug_directory",
            "_validate_existing_regular",
            "_validate_existing_guard",
            "_verify_debug_directory",
            "_open_verified_regular_file",
            "_acquire_advisory_lock",
            "_append_under_guard",
        )
        for body, cleanup in cases:
            with self.subTest(
                body=type(body).__name__,
                cleanup=type(cleanup).__name__,
            ):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                trace_state: dict[str, object] = {
                    "armed": False,
                    "injections": 0,
                    "traceback": None,
                }

                def arm_post_acquire() -> None:
                    trace_state["armed"] = True

                def inject_at_first_append_line(
                    frame: object,
                    event: str,
                    _argument: object,
                ) -> object:
                    if (
                        trace_state["armed"]
                        and getattr(frame, "f_code", None)
                        is WorkspaceDebugLog._append.__code__
                        and event == "line"
                    ):
                        trace_state["armed"] = False
                        trace_state["injections"] = int(
                            trace_state["injections"]
                        ) + 1
                        try:
                            raise body
                        except BaseException as caught:
                            trace_state["traceback"] = caught.__traceback__
                            raise
                    return inject_at_first_append_line

                process_lock = _AcquisitionBoundaryProcessLock(
                    release_failure=cleanup,
                    arm_post_acquire=arm_post_acquire,
                )
                holder = mock.Mock(lock=process_lock)
                previous_trace = sys.gettrace()

                with mock.patch.object(
                    debug_module,
                    "_process_lock_for",
                    return_value=holder,
                ):
                    with contextlib.ExitStack() as stack:
                        storage = {
                            seam: stack.enter_context(
                                mock.patch.object(debug_module, seam)
                            )
                            for seam in storage_seams
                        }
                        close = stack.enter_context(
                            mock.patch.object(debug_module.os, "close")
                        )
                        sys.settrace(inject_at_first_append_line)
                        try:
                            caught, final_traceback = _capture_raised(
                                self,
                                type(body),
                                lambda: _trace(
                                    log,
                                    session_id="post_acquire_dropped",
                                ),
                            )
                        finally:
                            sys.settrace(previous_trace)

                    self.assertIs(sys.gettrace(), previous_trace)
                    self.assertIs(caught, body)
                    self.assertIsNone(body.__cause__)
                    self.assertIsNone(body.__context__)
                    self.assertIsNone(cleanup.__cause__)
                    self.assertIsNone(cleanup.__context__)
                    injected_traceback = trace_state["traceback"]
                    assert isinstance(injected_traceback, TracebackType)
                    self.assertTrue(
                        _traceback_contains(final_traceback, injected_traceback)
                    )
                    self.assertEqual(trace_state["injections"], 1)
                    self.assertIsNotNone(process_lock.release_traceback)
                    for operation in (*storage.values(), close):
                        operation.assert_not_called()
                    self.assertEqual(process_lock.acquire_attempts, 1)
                    self.assertEqual(process_lock.release_attempts, 1)
                    self.assertFalse(process_lock.locked())
                    process_lock.assert_reusable(self)
                    self.assertFalse(log.path.exists())

                    process_lock.arm_post_acquire = None
                    process_lock.release_failure = None
                    _trace(log, session_id="post_acquire_recovered")

                self.assertEqual(process_lock.acquire_attempts, 2)
                self.assertEqual(process_lock.release_attempts, 2)
                process_lock.assert_reusable(self)
                self.assertEqual(
                    [record["session_id"] for record in _records(workspace)],
                    ["post_acquire_recovered"],
                )

    def test_thread_and_advisory_locks_share_one_deadline(self) -> None:
        class FakeThreadLock:
            def __init__(self) -> None:
                self.released = False

            def acquire(self, *, timeout: float) -> bool:
                self.timeout = timeout
                return True

            def release(self) -> None:
                self.released = True

        fake_lock = FakeThreadLock()
        holder = mock.Mock(lock=fake_lock)
        with (
            mock.patch.object(debug_module, "_process_lock_for", return_value=holder),
            mock.patch.object(debug_module.time, "monotonic", side_effect=(10.0, 10.075)),
            mock.patch.object(
                debug_module,
                "_open_verified_regular_file",
                return_value=17,
            ),
            mock.patch.object(debug_module, "_verify_open_file"),
            mock.patch.object(
                debug_module,
                "_acquire_advisory_lock",
                return_value=False,
            ) as acquire_advisory,
            mock.patch.object(debug_module.os, "close"),
        ):
            _trace(self.log)
        self.assertEqual(fake_lock.timeout, DEBUG_LOG_LOCK_TIMEOUT_SECONDS)
        self.assertTrue(fake_lock.released)
        timeout = acquire_advisory.call_args.kwargs["timeout_seconds"]
        self.assertAlmostEqual(timeout, 0.025)

    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_real_advisory_contention_drops_promptly_and_recovers(self) -> None:
        _trace(self.log, session_id="before")
        before = self.log.path.read_bytes()
        guard = self.workspace.path(DEBUG_LOG_GUARD_RELATIVE_PATH)
        script = (
            "import fcntl, os, sys; "
            "fd=os.open(sys.argv[1], os.O_RDWR); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); "
            "sys.stdin.read(1); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(guard)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup_process() -> None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(cleanup_process)
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline(), "locked\n")
        started = time.monotonic()
        _trace(self.log, session_id="dropped")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.log.path.read_bytes(), before)
        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()
        process.wait(timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr.read() if process.stderr else "")
        _trace(self.log, session_id="after")
        self.assertEqual(
            [record["session_id"] for record in _records(self.workspace)],
            ["before", "after"],
        )

    def test_partial_write_and_fsync_failures_restore_bytes_and_release_lock(self) -> None:
        _trace(self.log, session_id="baseline")
        baseline = self.log.path.read_bytes()
        real_write = os.write

        def write_prefix_then_fail(descriptor: int, value: object) -> int:
            payload = bytes(value)  # type: ignore[arg-type]
            real_write(descriptor, payload[:5])
            raise OSError("simulated partial append")

        with mock.patch.object(debug_module.os, "write", side_effect=write_prefix_then_fail):
            _trace(self.log, session_id="partial")
        self.assertEqual(self.log.path.read_bytes(), baseline)

        with mock.patch.object(debug_module.os, "fsync", side_effect=OSError("fsync")):
            _trace(self.log, session_id="fsync")
        self.assertEqual(self.log.path.read_bytes(), baseline)

        _trace(self.log, session_id="recovered")
        self.assertEqual(
            [record["session_id"] for record in _records(self.workspace)],
            ["baseline", "recovered"],
        )

    @unittest.skipUnless(
        os.name == "posix",
        "requires renaming an open directory and creating a directory symlink",
    )
    def test_parent_swap_after_partial_write_rolls_back_only_held_inode(
        self,
    ) -> None:
        _trace(self.log, session_id="baseline")
        baseline = self.log.path.read_bytes()
        namespace_before = _debug_namespace_snapshot(self.workspace)
        real_open = debug_module._open_verified_regular_file
        real_write = os.write
        real_close = os.close
        opened: dict[str, int] = {}
        opened_identities: dict[str, tuple[int, int]] = {}
        directory_descriptors: set[int] = set()
        closed: list[int] = []
        swapped = False

        def track_open(
            directory: object,
            filename: str,
            *,
            read_write: bool,
        ) -> int:
            descriptor = real_open(
                directory,
                filename,
                read_write=read_write,
            )
            value = os.fstat(descriptor)
            opened[filename] = descriptor
            opened_identities[filename] = (value.st_dev, value.st_ino)
            directory_descriptors.add(directory.descriptor)
            return descriptor

        def track_close(descriptor: int) -> None:
            closed.append(descriptor)
            real_close(descriptor)

        with tempfile.TemporaryDirectory(
            dir=self.workspace.root.parent
        ) as temporary, _DebugDirectorySwap(
            self.workspace,
            Path(temporary),
        ) as swap:
            _seed_debug_storage(swap.outside, prefix="outside-partial")
            outside_before = _directory_namespace_snapshot(swap.outside)
            captured: dict[str, object] = {}

            def write_prefix_then_swap(descriptor: int, value: object) -> int:
                nonlocal swapped
                payload = bytes(value)  # type: ignore[arg-type]
                written = real_write(descriptor, payload[:5])
                self.assertFalse(swapped)
                swap.activate()
                swapped = True
                captured["canonical"] = swap.canonical_snapshot()
                captured["partial"] = _directory_namespace_snapshot(
                    swap.parked
                )
                return written

            with (
                mock.patch.object(
                    debug_module,
                    "_open_verified_regular_file",
                    side_effect=track_open,
                ),
                mock.patch.object(
                    debug_module.os,
                    "write",
                    side_effect=write_prefix_then_swap,
                ) as write,
                mock.patch.object(
                    debug_module.os,
                    "close",
                    side_effect=track_close,
                ),
            ):
                _trace(self.log, session_id="dropped_after_partial")

            write.assert_called_once()
            self.assertTrue(swapped)
            self.assertEqual(
                swap.canonical_snapshot(),
                captured["canonical"],
            )
            self.assertNotEqual(
                captured["partial"],
                namespace_before,
            )
            self.assertEqual(
                _directory_namespace_snapshot(swap.parked),
                namespace_before,
            )
            self.assertEqual(
                _directory_namespace_snapshot(swap.outside),
                outside_before,
            )
            parked_active = swap.parked / _ACTIVE_NAME
            parked_stat = os.stat(parked_active, follow_symlinks=False)
            self.assertEqual(parked_active.read_bytes(), baseline)
            self.assertEqual(
                (parked_stat.st_dev, parked_stat.st_ino),
                opened_identities[_ACTIVE_NAME],
            )
            self.assertEqual(set(opened), {_GUARD_NAME, _ACTIVE_NAME})
            self.assertEqual(len(directory_descriptors), 1)
            directory_descriptor = next(iter(directory_descriptors))
            self.assertEqual(closed.count(opened[_GUARD_NAME]), 1)
            self.assertEqual(closed.count(opened[_ACTIVE_NAME]), 1)
            self.assertEqual(closed.count(directory_descriptor), 1)

        _trace(self.log, session_id="recovered_after_partial_swap")
        self.assertEqual(
            [record["session_id"] for record in _records(self.workspace)],
            ["baseline", "recovered_after_partial_swap"],
        )

    def test_post_open_active_fstat_failure_closes_descriptors_and_recovers(
        self,
    ) -> None:
        _trace(self.log, session_id="baseline")
        baseline = self.log.path.read_bytes()
        real_open = debug_module._open_verified_regular_file
        real_verify = debug_module._verify_open_descriptor
        real_close = os.close
        opened: dict[str, int] = {}
        directory_descriptors: set[int] = set()
        closed: list[int] = []
        active_size_failed = False

        def track_open(
            directory: object,
            filename: str,
            *,
            read_write: bool,
        ) -> int:
            descriptor = real_open(
                directory,
                filename,
                read_write=read_write,
            )
            opened[filename] = descriptor
            directory_descriptors.add(directory.descriptor)
            return descriptor

        def fail_active_size(descriptor: int) -> os.stat_result:
            nonlocal active_size_failed
            if (
                descriptor == opened.get(_ACTIVE_NAME)
                and not active_size_failed
            ):
                active_size_failed = True
                raise OSError("post-open active size unavailable")
            return real_verify(descriptor)

        def track_close(descriptor: int) -> None:
            closed.append(descriptor)
            real_close(descriptor)

        with (
            mock.patch.object(
                debug_module,
                "_open_verified_regular_file",
                side_effect=track_open,
            ),
            mock.patch.object(
                debug_module,
                "_verify_open_descriptor",
                side_effect=fail_active_size,
            ),
            mock.patch.object(
                debug_module.os,
                "close",
                side_effect=track_close,
            ),
            mock.patch.object(debug_module.os, "ftruncate") as truncate,
        ):
            _trace(self.log, session_id="dropped")

        self.assertTrue(active_size_failed)
        self.assertEqual(set(opened), {_GUARD_NAME, _ACTIVE_NAME})
        self.assertEqual(len(directory_descriptors), 1)
        directory_descriptor = next(iter(directory_descriptors))
        self.assertNotEqual(opened[_GUARD_NAME], opened[_ACTIVE_NAME])
        self.assertEqual(closed.count(opened[_GUARD_NAME]), 1)
        self.assertEqual(closed.count(opened[_ACTIVE_NAME]), 1)
        self.assertEqual(closed.count(directory_descriptor), 1)
        truncate.assert_not_called()
        self.assertEqual(self.log.path.read_bytes(), baseline)

        _trace(self.log, session_id="recovered")
        self.assertEqual(
            [record["session_id"] for record in _records(self.workspace)],
            ["baseline", "recovered"],
        )

    def test_successful_append_propagates_each_cleanup_control_and_closes_all(
        self,
    ) -> None:
        for exception_type in (KeyboardInterrupt, SystemExit):
            for role in (
                "release",
                "active",
                "guard",
                "debug",
                "root",
                "process_lock",
            ):
                with self.subTest(
                    exception_type=exception_type.__name__,
                    role=role,
                ):
                    workspace = initialized_workspace(self)
                    log = WorkspaceDebugLog(workspace)
                    control = exception_type(
                        f"PRIVATE_CLEANUP_{exception_type.__name__}_{role}"
                    )
                    harness = _AttemptCleanupHarness(
                        {} if role == "process_lock" else {role: control}
                    )
                    process_lock = _ControlledProcessLock(
                        control if role == "process_lock" else None
                    )

                    with _instrument_cleanup_attempt(harness, process_lock):
                        caught, final_traceback = _capture_raised(
                            self,
                            exception_type,
                            lambda: _trace(
                                log,
                                session_id="cleanup_success",
                            ),
                        )

                    self.assertIs(caught, control)
                    self.assertIsNone(control.__cause__)
                    self.assertIsNone(control.__context__)
                    injected = (
                        process_lock.injection_traceback
                        if role == "process_lock"
                        else harness.injection_tracebacks[role]
                    )
                    assert injected is not None
                    self.assertTrue(
                        _traceback_contains(final_traceback, injected)
                    )
                    harness.assert_closed(self)
                    self.assertEqual(harness.release_attempts, 1)
                    self.assertEqual(process_lock.release_attempts, 1)
                    process_lock.assert_reusable(self)
                    self.assertEqual(
                        [record["session_id"] for record in _records(workspace)],
                        ["cleanup_success"],
                    )
                    raw = log.path.read_text(encoding="utf-8")
                    self.assertNotIn("PRIVATE_CLEANUP", raw)
                    self.assertNotIn(str(workspace.root), raw)

                    _trace(log, session_id="cleanup_recovered")
                    self.assertEqual(
                        [record["session_id"] for record in _records(workspace)],
                        ["cleanup_success", "cleanup_recovered"],
                    )

    def test_body_control_wins_and_cleanup_control_wins_over_ordinary_body(
        self,
    ) -> None:
        cases = (
            (OSError, KeyboardInterrupt, "cleanup"),
            (KeyboardInterrupt, SystemExit, "body"),
            (SystemExit, KeyboardInterrupt, "body"),
        )
        for body_type, cleanup_type, winner in cases:
            with self.subTest(
                body_type=body_type.__name__,
                cleanup_type=cleanup_type.__name__,
            ):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                _trace(log, session_id="baseline")
                baseline = log.path.read_bytes()
                body = body_type(f"PRIVATE_BODY_{body_type.__name__}")
                cleanup = cleanup_type(
                    f"PRIVATE_CLEANUP_{cleanup_type.__name__}"
                )
                harness = _AttemptCleanupHarness({"release": cleanup})
                process_lock = _ControlledProcessLock()
                body_traceback: TracebackType | None = None

                def fail_body(_descriptor: int, _value: object) -> int:
                    nonlocal body_traceback
                    try:
                        raise body
                    except BaseException as caught:
                        body_traceback = caught.__traceback__
                        raise

                expected = body if winner == "body" else cleanup
                expected_type = type(expected)
                with (
                    _instrument_cleanup_attempt(harness, process_lock),
                    mock.patch.object(
                        debug_module.os,
                        "write",
                        side_effect=fail_body,
                    ),
                ):
                    caught, final_traceback = _capture_raised(
                        self,
                        expected_type,
                        lambda: _trace(log, session_id="body_failure"),
                    )

                self.assertIs(caught, expected)
                expected_traceback = (
                    body_traceback
                    if winner == "body"
                    else harness.injection_tracebacks["release"]
                )
                assert expected_traceback is not None
                self.assertTrue(
                    _traceback_contains(final_traceback, expected_traceback)
                )
                self.assertIsNone(expected.__cause__)
                self.assertIsNone(expected.__context__)
                harness.assert_closed(self)
                self.assertEqual(harness.release_attempts, 1)
                process_lock.assert_reusable(self)
                self.assertEqual(log.path.read_bytes(), baseline)
                raw = log.path.read_text(encoding="utf-8")
                for private in ("PRIVATE_BODY", "PRIVATE_CLEANUP", str(workspace.root)):
                    self.assertNotIn(private, raw)

                _trace(log, session_id="body_recovered")
                self.assertEqual(
                    [record["session_id"] for record in _records(workspace)],
                    ["baseline", "body_recovered"],
                )

    def test_multiple_cleanup_controls_attempt_all_and_keep_first_control(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        log = WorkspaceDebugLog(workspace)
        release = KeyboardInterrupt("PRIVATE_FIRST_RELEASE_CONTROL")
        failures: dict[str, BaseException] = {
            "active": OSError("PRIVATE_ORDINARY_ACTIVE_CLOSE"),
            "release": release,
            "guard": SystemExit("PRIVATE_GUARD_CONTROL"),
            "debug": KeyboardInterrupt("PRIVATE_DEBUG_CONTROL"),
            "root": SystemExit("PRIVATE_ROOT_CONTROL"),
        }
        process_control = KeyboardInterrupt("PRIVATE_PROCESS_CONTROL")
        harness = _AttemptCleanupHarness(failures)
        process_lock = _ControlledProcessLock(process_control)

        with _instrument_cleanup_attempt(harness, process_lock):
            caught, final_traceback = _capture_raised(
                self,
                KeyboardInterrupt,
                lambda: _trace(log, session_id="multiple_cleanup"),
            )

        self.assertIs(caught, release)
        self.assertTrue(
            _traceback_contains(
                final_traceback,
                harness.injection_tracebacks["release"],
            )
        )
        self.assertEqual(
            set(harness.injection_tracebacks),
            {"active", "release", "guard", "debug", "root"},
        )
        self.assertIsNotNone(process_lock.injection_traceback)
        harness.assert_closed(self)
        self.assertEqual(harness.release_attempts, 1)
        self.assertEqual(process_lock.release_attempts, 1)
        process_lock.assert_reusable(self)
        self.assertEqual(
            [record["session_id"] for record in _records(workspace)],
            ["multiple_cleanup"],
        )
        raw = log.path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_", raw)
        self.assertNotIn(str(workspace.root), raw)

    def test_open_verification_and_rollback_cleanup_precedence(self) -> None:
        for body_type, cleanup_type, winner in (
            (OSError, KeyboardInterrupt, "cleanup"),
            (KeyboardInterrupt, SystemExit, "body"),
            (SystemExit, KeyboardInterrupt, "body"),
        ):
            with self.subTest(
                seam="open_verification",
                body_type=body_type.__name__,
                cleanup_type=cleanup_type.__name__,
            ):
                workspace = initialized_workspace(self)
                body = body_type("PRIVATE_OPEN_VERIFICATION_BODY")
                cleanup = cleanup_type("PRIVATE_OPEN_VERIFICATION_CLEANUP")
                body_traceback: TracebackType | None = None
                cleanup_traceback: TracebackType | None = None
                opened_descriptor: int | None = None
                close_attempts = 0
                real_close = os.close

                def fail_verification(
                    descriptor: int,
                    _directory: object,
                    _filename: str,
                ) -> None:
                    nonlocal body_traceback, opened_descriptor
                    opened_descriptor = descriptor
                    try:
                        raise body
                    except BaseException as caught:
                        body_traceback = caught.__traceback__
                        raise

                def close_then_fail(descriptor: int) -> None:
                    nonlocal cleanup_traceback, close_attempts
                    close_attempts += 1
                    real_close(descriptor)
                    try:
                        raise cleanup
                    except BaseException as caught:
                        cleanup_traceback = caught.__traceback__
                        raise

                expected = body if winner == "body" else cleanup
                with _held_debug_directory(workspace) as directory:
                    with (
                        mock.patch.object(
                            debug_module,
                            "_verify_open_file",
                            side_effect=fail_verification,
                        ),
                        mock.patch.object(
                            debug_module.os,
                            "close",
                            side_effect=close_then_fail,
                        ),
                    ):
                        caught, final_traceback = _capture_raised(
                            self,
                            type(expected),
                            lambda: debug_module._open_verified_regular_file(
                                directory,
                                _ACTIVE_NAME,
                                read_write=False,
                            ),
                        )

                self.assertIs(caught, expected)
                expected_traceback = (
                    body_traceback if winner == "body" else cleanup_traceback
                )
                assert expected_traceback is not None
                self.assertTrue(
                    _traceback_contains(final_traceback, expected_traceback)
                )
                self.assertEqual(close_attempts, 1)
                assert opened_descriptor is not None
                _assert_descriptor_closed(self, opened_descriptor)

        for body_type, winner in (
            (OSError, "rollback"),
            (KeyboardInterrupt, "body"),
            (SystemExit, "body"),
        ):
            with self.subTest(
                seam="rollback",
                body_type=body_type.__name__,
            ):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                _trace(log, session_id="rollback_baseline")
                baseline = log.path.read_bytes()
                body = body_type("PRIVATE_ROLLBACK_BODY")
                rollback = SystemExit("PRIVATE_ROLLBACK_CONTROL")
                later = KeyboardInterrupt("PRIVATE_LATER_GUARD_CONTROL")
                harness = _AttemptCleanupHarness({"guard": later})
                process_lock = _ControlledProcessLock()
                body_traceback: TracebackType | None = None
                rollback_traceback: TracebackType | None = None
                rollback_identity: tuple[int, int] | None = None

                def fail_body(_descriptor: int, _value: object) -> int:
                    nonlocal body_traceback
                    try:
                        raise body
                    except BaseException as caught:
                        body_traceback = caught.__traceback__
                        raise

                def fail_rollback(
                    descriptor: int,
                    expected_identity: tuple[int, int],
                ) -> None:
                    nonlocal rollback_traceback, rollback_identity
                    opened = os.fstat(descriptor)
                    rollback_identity = (opened.st_dev, opened.st_ino)
                    self.assertEqual(rollback_identity, expected_identity)
                    try:
                        raise rollback
                    except BaseException as caught:
                        rollback_traceback = caught.__traceback__
                        raise

                expected = body if winner == "body" else rollback
                with (
                    _instrument_cleanup_attempt(harness, process_lock),
                    mock.patch.object(
                        debug_module.os,
                        "write",
                        side_effect=fail_body,
                    ),
                    mock.patch.object(
                        debug_module,
                        "_verify_rollback_descriptor",
                        side_effect=fail_rollback,
                    ) as verify_rollback,
                    mock.patch.object(debug_module.os, "ftruncate") as truncate,
                ):
                    caught, final_traceback = _capture_raised(
                        self,
                        type(expected),
                        lambda: _trace(log, session_id="rollback_failure"),
                    )

                self.assertIs(caught, expected)
                expected_traceback = (
                    body_traceback if winner == "body" else rollback_traceback
                )
                assert expected_traceback is not None
                self.assertTrue(
                    _traceback_contains(final_traceback, expected_traceback)
                )
                verify_rollback.assert_called_once()
                self.assertIsNotNone(rollback_identity)
                truncate.assert_not_called()
                harness.assert_closed(self)
                self.assertIn("guard", harness.injection_tracebacks)
                process_lock.assert_reusable(self)
                self.assertEqual(log.path.read_bytes(), baseline)

                _trace(log, session_id="rollback_recovered")
                self.assertEqual(
                    [record["session_id"] for record in _records(workspace)],
                    ["rollback_baseline", "rollback_recovered"],
                )
                raw = log.path.read_text(encoding="utf-8")
                self.assertNotIn("PRIVATE_", raw)
                self.assertNotIn(str(workspace.root), raw)

    def test_lock_rotation_rollback_and_cleanup_failures_never_escape(self) -> None:
        payload = _trace_payload(self.log)
        active = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        active.write_bytes(b"x" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1))
        with mock.patch.object(debug_module.os, "rename", side_effect=OSError("rotate")):
            _trace(self.log)
        self.assertFalse(self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.1").exists())

        with mock.patch.object(
            debug_module,
            "_acquire_advisory_lock",
            side_effect=OSError("lock"),
        ):
            _trace(self.log)

        real_write = os.write

        def partial_then_fail(descriptor: int, value: object) -> int:
            real_write(descriptor, bytes(value)[:3])  # type: ignore[arg-type]
            raise OSError("write")

        with (
            mock.patch.object(debug_module.os, "write", side_effect=partial_then_fail),
            mock.patch.object(debug_module.os, "ftruncate", side_effect=OSError("rollback")),
        ):
            _trace(self.log)


if __name__ == "__main__":
    unittest.main()
