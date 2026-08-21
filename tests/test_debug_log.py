"""Content-safe bounded workspace debug-log contract tests."""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
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
from story_writing_agents.workspace import Workspace

from tests.helpers import initialized_workspace


_FIXED_TIME = datetime(2026, 8, 20, 1, 2, 3, 456000, tzinfo=UTC)


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

    def test_clean_import_dependency_boundary_and_facade_isolation(self) -> None:
        source = Path(debug_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 1
        }
        self.assertEqual(dependencies, {"agents", "workspace"})
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
        prior_backups: dict[int, bytes] = {}
        for index in range(1, DEBUG_LOG_BACKUP_COUNT + 1):
            value = f"backup-{index}".encode("ascii")
            prior_backups[index] = value
            self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.{index}").write_bytes(value)
        _trace(self.log)
        self.assertEqual(active.stat().st_size, len(payload))
        self.assertEqual(len(_records(self.workspace)), 1)
        self.assertEqual(
            self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.1").read_bytes(),
            prior_active,
        )
        for index in range(2, DEBUG_LOG_BACKUP_COUNT + 1):
            self.assertEqual(
                self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.{index}").read_bytes(),
                prior_backups[index - 1],
            )
        artifacts = [
            active,
            *(self.workspace.path(f"{DEBUG_LOG_RELATIVE_PATH}.{index}") for index in range(1, 6)),
        ]
        self.assertTrue(all(path.stat().st_size <= DEBUG_LOG_MAX_BYTES for path in artifacts))
        self.assertLessEqual(
            sum(path.stat().st_size for path in artifacts),
            6 * DEBUG_LOG_MAX_BYTES,
        )

    def test_preexisting_oversized_active_or_backup_drops_without_mutation(self) -> None:
        for relative in (
            DEBUG_LOG_RELATIVE_PATH,
            f"{DEBUG_LOG_RELATIVE_PATH}.3",
        ):
            with self.subTest(relative=relative):
                workspace = initialized_workspace(self)
                log = WorkspaceDebugLog(workspace)
                workspace.path(relative).write_bytes(b"x" * (DEBUG_LOG_MAX_BYTES + 1))
                before = _rotation_snapshot(workspace)
                _trace(log)
                self.assertEqual(_rotation_snapshot(workspace), before)

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

    def test_nonregular_storage_paths_drop_without_raising(self) -> None:
        for relative in (
            DEBUG_LOG_GUARD_RELATIVE_PATH,
            DEBUG_LOG_RELATIVE_PATH,
            f"{DEBUG_LOG_RELATIVE_PATH}.4",
        ):
            with self.subTest(relative=relative):
                workspace = initialized_workspace(self)
                workspace.path(relative).mkdir()
                _trace(WorkspaceDebugLog(workspace))
                self.assertTrue(workspace.path(relative).is_dir())

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

    def test_lock_rotation_rollback_and_cleanup_failures_never_escape(self) -> None:
        payload = _trace_payload(self.log)
        active = self.workspace.path(DEBUG_LOG_RELATIVE_PATH)
        active.write_bytes(b"x" * (DEBUG_LOG_MAX_BYTES - len(payload) + 1))
        with mock.patch.object(debug_module.os, "replace", side_effect=OSError("rotate")):
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
