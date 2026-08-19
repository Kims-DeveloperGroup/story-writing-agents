"""Provider-free process verification for the interactive CLI over a POSIX PTY."""

from __future__ import annotations

import errno
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    load_cli_context,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversations import (
    ConversationEventAppend,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
)
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)


try:
    import fcntl
    import pty
    import termios
except ImportError as _pty_import_error:
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    _POSIX_PTY_SKIP_REASON = (
        "POSIX PTY facilities are unavailable: "
        f"{type(_pty_import_error).__name__}"
    )
else:
    _POSIX_PTY_SKIP_REASON = ""


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_PROCESS_DEADLINE_SECONDS = 12.0
_PROCESS_POLL_QUANTUM_SECONDS = 0.02
_TEARDOWN_DEADLINE_SECONDS = 3.0


_FAKE_CODEX = r'''#!/usr/bin/env python3
"""Deterministic local Codex protocol stand-in used only by POSIX PTY tests."""

import fcntl
import json
import os
import signal
import sys
from pathlib import Path


LOG_PATH = Path(os.environ["STORY_PTY_CODEX_LOG"])
CONTROL_PATH = Path(os.environ["STORY_PTY_CODEX_CONTROL"])


def append_record(value):
    """Append one locked, complete UTF-8 JSONL record."""

    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(
        LOG_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("fake provider log write made no progress")
            remaining = remaining[written:]
    finally:
        os.close(descriptor)


def terminate(_signum, _frame):
    """Record backend-directed termination and exit without provider work."""

    append_record({"event": "terminated", "pid": os.getpid()})
    raise SystemExit(143)


signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, terminate)

argv = sys.argv[1:]
payload = json.loads(sys.stdin.read())
control = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
append_record(
    {
        "event": "started",
        "pid": os.getpid(),
        "argv": argv,
        "operation": payload.get("operation"),
        "phase": payload.get("phase"),
        "payload": payload,
    }
)

if control.get("mode") == "block":
    while True:
        signal.pause()

if control.get("mode") == "nonzero":
    sys.stderr.write(control.get("diagnostic", "simulated provider failure") + "\n")
    raise SystemExit(7)

output_path = Path(argv[argv.index("--output-last-message") + 1])
if control.get("mode") == "malformed":
    output_path.write_text(
        control.get("response", "not-json"),
        encoding="utf-8",
    )
elif payload.get("operation") == "conversation_retrieval":
    action_index = payload["retrieval"]["action_index"]
    correlation = {
        "session_id": payload["session_id"],
        "conversation_id": payload["conversation_id"],
        "turn_id": payload["turn_id"],
        "action_index": action_index,
    }
    if action_index == 1 and payload["content"].startswith("retrieve "):
        response = {
            "kind": "search_conversations",
            **correlation,
            "query": payload["content"].removeprefix("retrieve "),
        }
    elif action_index == 2:
        observation = json.loads(payload["observation"].splitlines()[1])
        citation_id = observation["hits"][0]["citation_id"]
        sequence = observation["hits"][0]["source"]["citation"]["sequence"]
        response = {
            "kind": "read_transcript",
            **correlation,
            "citation_id": citation_id,
            "start_sequence": sequence,
            "end_sequence": None,
        }
    elif action_index == 3:
        observation = json.loads(payload["observation"].splitlines()[1])
        response = {
            "kind": "answer",
            **correlation,
            "content": "retrieval-assisted answer",
            "citation_ids": [observation["source_citation_id"]],
        }
    else:
        content = (
            "visible \x1b[31mred\x1b[0m and bell \x07"
            if payload.get("content") == "show visible controls"
            else "deterministic advisory reply"
        )
        response = {
            "kind": "answer",
            **correlation,
            "content": content,
            "citation_ids": [],
        }
    output_path.write_text(
        json.dumps({"action": response}),
        encoding="utf-8",
    )
elif payload["phase"] == "audit":
    response = {
        "revision_id": payload["revision_id"],
        "verdict": "approve",
        "content": "",
    }
    output_path.write_text(json.dumps(response), encoding="utf-8")
elif payload["phase"] == "finalize":
    response = {
        "revision_id": payload["revision_id"],
        "core_updates": [
            {"filename": "global_outline.md", "content": "updated outline"}
        ],
    }
    output_path.write_text(json.dumps(response), encoding="utf-8")
else:
    response = {
        "revision_id": payload["revision_id"],
        "content": "deterministic candidate",
    }
    output_path.write_text(json.dumps(response), encoding="utf-8")

if "resume" not in argv:
    print(
        json.dumps(
            {
                "type": "thread.started",
                "thread_id": (
                    "thread-" + payload["session_id"] + "-" + payload["agent_id"]
                ),
            }
        )
    )
'''


def _control_character_byte(value: object, *, label: str) -> bytes:
    """Return one configured termios control character as a single byte."""

    if isinstance(value, bytes) and len(value) == 1:
        return value
    if isinstance(value, int) and 0 <= value <= 255:
        return bytes((value,))
    raise unittest.SkipTest(f"POSIX PTY {label} is not a one-byte value")


def _copy_terminal_attributes(attributes: list[Any]) -> list[Any]:
    """Copy one termios attribute list including its control-character vector."""

    copied = list(attributes)
    copied[6] = list(attributes[6])
    return copied


def _durable_tree(root: Path) -> dict[str, bytes]:
    """Capture exact file bytes below one test-owned workspace."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _durable_tree_without_access(root: Path) -> dict[str, bytes]:
    """Capture persistent bytes while excluding transient access claims."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith(".access.json")
    }


class PtyCliProcess:
    """Own one CLI child, its terminal, bounded reads, and exact group cleanup."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        master_fd: int,
        *,
        vintr: bytes,
        veof: bytes,
        stdout_is_terminal: bool,
        original_terminal_attributes: list[Any],
    ) -> None:
        """Retain validated process and terminal resources for one bounded run."""

        self.process = process
        self.master_fd = master_fd
        self.vintr = vintr
        self.veof = veof
        self.stdout_is_terminal = stdout_is_terminal
        self.original_terminal_attributes = _copy_terminal_attributes(
            original_terminal_attributes
        )
        self.final_terminal_attributes: list[Any] | None = None
        self.raw_bytes = bytearray()
        self.pipe_stdout = b""
        self.pipe_stderr = b""
        self._closed = False
        self._validated_process_group: int | None = None
        try:
            process_group = os.getpgid(process.pid)
        except ProcessLookupError:
            process_group = None
        if (
            process_group == process.pid
            and process_group != os.getpgrp()
            and process_group > 1
        ):
            self._validated_process_group = process_group

    @property
    def normalized_bytes(self) -> bytes:
        """Return captured terminal bytes with only CRLF framing normalized."""

        terminal = bytes(self.raw_bytes).replace(b"\r\n", b"\n")
        return terminal + self.pipe_stdout + self.pipe_stderr

    @property
    def normalized_text(self) -> str:
        """Decode the normalized capture for semantic terminal assertions."""

        return self.normalized_bytes.decode("utf-8", errors="replace")

    def output_mark(self) -> int:
        """Return a byte offset for assertions about subsequently rendered output."""

        return len(self.normalized_bytes)

    def text_after(self, mark: int) -> str:
        """Decode normalized output rendered at or after one prior byte mark."""

        return self.normalized_bytes[mark:].decode("utf-8", errors="replace")

    def send_line(self, line: str) -> None:
        """Write one UTF-8 canonical input line to the terminal."""

        if self.process.poll() is not None:
            raise AssertionError("cannot write to an exited CLI process")
        os.write(self.master_fd, line.encode("utf-8") + b"\n")

    def send_text(self, text: str) -> None:
        """Write exact UTF-8 input without adding a submission character."""

        if self.process.poll() is not None:
            raise AssertionError("cannot write to an exited CLI process")
        os.write(self.master_fd, text.encode("utf-8"))

    def send_interrupt(self) -> None:
        """Deliver the terminal's configured VINTR byte through the PTY."""

        os.write(self.master_fd, self.vintr)

    def send_eof(self) -> None:
        """Deliver the terminal's configured VEOF byte through the PTY."""

        os.write(self.master_fd, self.veof)

    def resize(self, columns: int) -> None:
        """Change this PTY's reported width for the next explicit redraw."""

        if type(columns) is not int or columns <= 0:
            raise ValueError("columns must be a positive integer")
        assert fcntl is not None
        assert termios is not None
        fcntl.ioctl(
            self.master_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", 24, columns, 0, 0),
        )

    def wait_for_text(
        self,
        expected: str,
        *,
        after: int = 0,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
    ) -> str:
        """Read until one semantic fragment appears or a bounded failure occurs."""

        encoded = expected.encode("utf-8")
        deadline = time.monotonic() + timeout
        while encoded not in self.normalized_bytes[after:]:
            if self.process.poll() is not None:
                self._drain_terminal()
                break
            self._read_terminal_once(deadline)
        if encoded not in self.normalized_bytes[after:]:
            raise AssertionError(
                f"CLI output did not contain {expected!r}; "
                f"captured_after_mark={self.text_after(after)!r}"
            )
        return self.text_after(after)

    def wait_for_exit(
        self,
        *,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
    ) -> int:
        """Wait for bounded exit, drain owned descriptors, and return status."""

        deadline = time.monotonic() + timeout
        while self.process.poll() is None:
            self._read_terminal_once(deadline)
        self._drain_terminal()
        if self.stdout_is_terminal:
            assert termios is not None
            self.final_terminal_attributes = _copy_terminal_attributes(
                termios.tcgetattr(self.master_fd)
            )
        stdout, stderr = self.process.communicate(
            timeout=_TEARDOWN_DEADLINE_SECONDS
        )
        self.pipe_stdout += stdout or b""
        self.pipe_stderr += stderr or b""
        return_code = self.process.returncode
        assert return_code is not None
        self.close()
        return return_code

    def close(self) -> None:
        """Reap this exact child group and close its PTY without broad signals."""

        if self._closed:
            return
        self._closed = True
        try:
            if self.process.poll() is None:
                self._terminate_validated_group()
            if self.process.poll() is None:
                self.process.wait(timeout=_TEARDOWN_DEADLINE_SECONDS)
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

    def _terminate_validated_group(self) -> None:
        """Terminate, then kill, only the child session group validated at spawn."""

        process_group = self._validated_process_group
        if process_group is None:
            self.process.terminate()
        else:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            self.process.wait(timeout=_TEARDOWN_DEADLINE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        if process_group is None:
            self.process.kill()
        else:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return
        self.process.wait(timeout=_TEARDOWN_DEADLINE_SECONDS)

    def _read_terminal_once(self, deadline: float) -> None:
        """Read available PTY bytes within an absolute monotonic deadline."""

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                "CLI process exceeded its output deadline; "
                f"captured={self.normalized_text!r}"
            )
        readable, _, _ = select.select(
            (self.master_fd,),
            (),
            (),
            min(remaining, _PROCESS_POLL_QUANTUM_SECONDS),
        )
        if not readable:
            return
        self._read_available_terminal_bytes()

    def _read_available_terminal_bytes(self) -> None:
        """Drain currently available PTY bytes without blocking."""

        while True:
            try:
                chunk = os.read(self.master_fd, 65_536)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return
                raise
            if not chunk:
                return
            self.raw_bytes.extend(chunk)

    def _drain_terminal(self) -> None:
        """Drain terminal bytes left after child exit without waiting."""

        self._read_available_terminal_bytes()


@unittest.skipUnless(
    not _POSIX_PTY_SKIP_REASON,
    _POSIX_PTY_SKIP_REASON or "POSIX PTY facilities are unavailable",
)
class InteractiveCliPtyTests(unittest.TestCase):
    """Exercise the real CLI process with deterministic local terminal resources."""

    def setUp(self) -> None:
        """Create one isolated workspace root and local fake provider command."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary_root = Path(temporary.name)
        self.workspace = Workspace(self.temporary_root / "workspace").initialize()
        self.fake_directory = self.temporary_root / "provider-bin"
        self.fake_directory.mkdir()
        self.fake_executable = self.fake_directory / "codex"
        self.fake_executable.write_text(_FAKE_CODEX, encoding="utf-8")
        self.fake_executable.chmod(0o755)
        self.provider_log = self.temporary_root / "provider.jsonl"
        self.provider_control = self.temporary_root / "provider-control.json"
        self.provider_control.write_text('{"mode":"auto"}', encoding="utf-8")
        self._processes: list[PtyCliProcess] = []
        self.addCleanup(self._close_processes)

    def _close_processes(self) -> None:
        """Close every process harness retained by the current test."""

        for process in reversed(self._processes):
            process.close()

    def _environment(self) -> dict[str, str]:
        """Return a provider-free child environment with explicit source imports."""

        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(_SOURCE_ROOT),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": os.pathsep.join(
                    (str(self.fake_directory), environment.get("PATH", ""))
                ),
                "NO_COLOR": "1",
                "TERM": "dumb",
                "STORY_PTY_CODEX_LOG": str(self.provider_log),
                "STORY_PTY_CODEX_CONTROL": str(self.provider_control),
                "CODEX_API_KEY": "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            }
        )
        return environment

    def _set_provider_control(self, **values: object) -> None:
        """Replace the fake provider's next-invocation behavior."""

        self.provider_control.write_text(
            json.dumps(values, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _provider_records(self) -> list[dict[str, Any]]:
        """Read one shared-lock-consistent fake-provider JSONL snapshot."""

        if not self.provider_log.exists():
            return []
        assert fcntl is not None, "POSIX PTY tests require fcntl"
        with self.provider_log.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            return [
                json.loads(line)
                for line in handle.read().splitlines()
                if line
            ]

    def _wait_for_provider_record(
        self,
        process: PtyCliProcess,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        after_count: int = 0,
        timeout: float = _PROCESS_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        """Wait on explicit provider-log readiness while continuing to drain the PTY."""

        deadline = time.monotonic() + timeout
        while True:
            for record in self._provider_records()[after_count:]:
                if predicate(record):
                    return record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    "fake provider did not publish the expected readiness record"
                )
            readable, _, _ = select.select(
                (process.master_fd,),
                (),
                (),
                min(remaining, 0.02),
            )
            if readable:
                process._read_available_terminal_bytes()
            if process.process.poll() is not None:
                raise AssertionError(
                    "CLI exited before fake-provider readiness; "
                    f"captured={process.normalized_text!r}"
                )

    def _seed_default_session(
        self,
        *,
        session_id: str = "alpha",
        subject: str = "Harbor planning",
    ) -> ConversationMetadata:
        """Persist one ready default session and default conversation."""

        backend = AgentBackend(
            self.workspace,
            executable=str(self.fake_executable),
        )
        try:
            backend.create_session_with_access(session_id)
        finally:
            backend.close()
        save_default_session(self.workspace, session_id)
        catalog = FilesystemConversationCatalog(self.workspace)
        selected = catalog.create(session_id, subject)
        save_default_conversation(
            self.workspace,
            session_id,
            selected.conversation_id,
        )
        return selected

    def _spawn_cli(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin_is_terminal: bool = True,
        stdout_is_terminal: bool = True,
        environment_overrides: Mapping[str, str] | None = None,
        terminal_columns: int = 80,
        decorative_color: bool = False,
    ) -> PtyCliProcess:
        """Spawn the package entry with independently configurable terminal streams."""

        return self._spawn_command(
            (sys.executable, "-m", "story_writing_agents", *arguments),
            cwd=cwd,
            stdin_is_terminal=stdin_is_terminal,
            stdout_is_terminal=stdout_is_terminal,
            environment_overrides=environment_overrides,
            terminal_columns=terminal_columns,
            decorative_color=decorative_color,
        )

    def _spawn_python(
        self,
        source: str,
        *,
        terminal_columns: int = 80,
        environment_overrides: Mapping[str, str] | None = None,
        decorative_color: bool = False,
    ) -> PtyCliProcess:
        """Spawn one source-backed package integration probe on a real PTY."""

        return self._spawn_command(
            (sys.executable, "-c", source),
            cwd=self.workspace.root,
            environment_overrides=environment_overrides,
            terminal_columns=terminal_columns,
            decorative_color=decorative_color,
        )

    def _spawn_command(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        stdin_is_terminal: bool = True,
        stdout_is_terminal: bool = True,
        environment_overrides: Mapping[str, str] | None = None,
        terminal_columns: int = 80,
        decorative_color: bool = False,
    ) -> PtyCliProcess:
        """Spawn an exact child command with independently configurable streams."""

        assert pty is not None
        assert fcntl is not None
        assert termios is not None
        try:
            master_fd, slave_fd = pty.openpty()
            attributes = termios.tcgetattr(slave_fd)
            fcntl.ioctl(
                slave_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 24, terminal_columns, 0, 0),
            )
        except OSError as exc:
            self.skipTest(
                "POSIX PTY allocation/configuration is unavailable: "
                f"{type(exc).__name__}"
            )
        attributes[3] |= termios.ICANON | termios.ISIG | termios.ECHO
        attributes[0] |= termios.ICRNL
        attributes[1] |= termios.OPOST | termios.ONLCR
        termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)
        configured = termios.tcgetattr(slave_fd)
        vintr = _control_character_byte(
            configured[6][termios.VINTR],
            label="VINTR",
        )
        veof = _control_character_byte(
            configured[6][termios.VEOF],
            label="VEOF",
        )
        fcntl.fcntl(master_fd, fcntl.F_SETFL, os.O_NONBLOCK)

        terminal_descriptor = 0 if stdin_is_terminal else 1

        def establish_controlling_terminal() -> None:
            """Create the child's isolated session and acquire its supplied PTY."""

            os.setsid()
            fcntl.ioctl(terminal_descriptor, termios.TIOCSCTTY, 0)
            os.tcsetpgrp(terminal_descriptor, os.getpgrp())

        environment = self._environment()
        if environment_overrides:
            environment.update(environment_overrides)
        if decorative_color:
            environment.pop("NO_COLOR", None)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd or self.workspace.root,
                env=environment,
                stdin=slave_fd if stdin_is_terminal else subprocess.PIPE,
                stdout=slave_fd if stdout_is_terminal else subprocess.PIPE,
                stderr=slave_fd if stdout_is_terminal else subprocess.PIPE,
                bufsize=0,
                close_fds=True,
                preexec_fn=establish_controlling_terminal,
            )
        finally:
            os.close(slave_fd)
        harness = PtyCliProcess(
            process,
            master_fd,
            vintr=vintr,
            veof=veof,
            stdout_is_terminal=stdout_is_terminal,
            original_terminal_attributes=configured,
        )
        self._processes.append(harness)
        return harness

    def test_internal_picker_ansi_resizes_navigates_commits_and_restores_repeatedly(
        self,
    ) -> None:
        """Exercise the unlisted stream adapter, live window, and final commit."""

        source = r'''
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

choices = (
    Choice("secret-1", "Alpha"),
    Choice("secret-2", "Beta", enabled=False, disabled_reason="Unavailable"),
    Choice("secret-3", "Gamma"),
    Choice("secret-4", "Delta"),
    Choice("secret-5", "Epsilon"),
    Choice("secret-6", "Zeta"),
    Choice("secret-7", "Eta"),
    Choice("secret-8", "Theta"),
    Choice("secret-9", "Iota"),
    Choice("secret-10", "Kappa"),
)
terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose route"),
    choices,
    highlighted_id="secret-1",
    selected_id="secret-10",
)
assert outcome.text == "secret-10"
print("RESULT=" + outcome.kind.value)
'''
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                before = _durable_tree(self.workspace.root)
                process = self._spawn_python(
                    source,
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=60,
                    decorative_color=True,
                )
                initial = process.wait_for_text(
                    "Choices (highlighted 1/10; showing 1-7/10) [open]"
                )
                self.assertIn(
                    "Selected outside view: 10. [selected] Kappa",
                    initial,
                )
                assert termios is not None
                active = termios.tcgetattr(process.master_fd)
                self.assertFalse(active[3] & termios.ICANON)
                self.assertFalse(active[3] & termios.ECHO)

                process.send_text("\x1b[B" * 7)
                process.wait_for_text(
                    "Choices (highlighted 9/10; showing 3-9/10) [open]"
                )
                process.resize(20)
                resize_mark = process.output_mark()
                process.send_text("\x1b[B")
                resized = process.wait_for_text(
                    "d 10/10; showing 4-",
                    after=resize_mark,
                )
                self.assertIn("10/10) [open]", resized)
                process.send_text("\n")
                final = process.wait_for_text("RESULT=commit", after=resize_mark)
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                collapsed = "".join(
                    re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", final).split()
                )
                self.assertIn("[selected][highlighted]Kappa", collapsed)
                self.assertIn("[completed]", collapsed)
                self.assertNotIn("secret-", process.normalized_text)
                self.assertIn(b"\x1b[35m", process.raw_bytes)
                self.assertNotIn(b"\x1b[?1049", process.raw_bytes)
                self.assertNotIn(b"\x1b[2J", process.raw_bytes)
                self.assertEqual(_durable_tree(self.workspace.root), before)
                self.assertFalse(self.provider_log.exists())
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_internal_picker_no_color_narrow_cancel_sanitizes_and_restores_repeatedly(
        self,
    ) -> None:
        """Keep combined cues and hostile text visible without decorative SGR."""

        source = r'''
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose unsafe"),
    (
        Choice(
            "opaque-id",
            "界e\u0301 bad\x1b]0;owned\x07\u202e",
            description="desc\x00",
        ),
    ),
    selected_id="opaque-id",
)
assert outcome.text is None
print("RESULT=" + outcome.kind.value)
'''
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                process = self._spawn_python(
                    source,
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=20,
                )
                opened = process.wait_for_text(r"desc\x00", timeout=12)
                self.assertIn("b]0;owned\\", opened)
                self.assertIn(r"x07\u202e", opened)
                self.assertIn(r"desc\x00", opened)
                self.assertNotIn("opaque-id", opened)
                process.send_text("\x1b")
                result = process.wait_for_text("RESULT=cancel")
                self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
                visible = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result)
                collapsed = "".join(visible.split())
                self.assertIn("[selected][highlighted]", collapsed)
                self.assertIn("[cancelled]", collapsed)
                self.assertNotIn("opaque-id", process.normalized_text)
                self.assertNotRegex(
                    process.normalized_text,
                    r"\x1b\[(?:0|1|3[1-7])m",
                )
                self.assertFalse(self.provider_log.exists())
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_internal_picker_vintr_and_veof_clear_without_final_frame_repeatedly(
        self,
    ) -> None:
        """Return typed terminal events with raw restoration and no final record."""

        source = r'''
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose event"),
    (Choice("opaque-id", "Visible choice"),),
)
assert outcome.text is None
print("RESULT=" + outcome.kind.value)
'''
        for event_name in ("interrupt", "eof"):
            for attempt in range(3):
                with self.subTest(event=event_name, attempt=attempt):
                    process = self._spawn_python(
                        source,
                        environment_overrides={"TERM": "xterm-256color"},
                        terminal_columns=40,
                    )
                    process.wait_for_text("Visible choice")
                    mark = process.output_mark()
                    if event_name == "interrupt":
                        process.send_interrupt()
                    else:
                        process.send_eof()
                    result = process.wait_for_text(f"RESULT={event_name}", after=mark)
                    self.assertEqual(
                        process.wait_for_exit(),
                        0,
                        process.normalized_text,
                    )
                    self.assertIn("\x1b[J", result)
                    self.assertNotIn("[completed]", result)
                    self.assertNotIn("[cancelled]", result)
                    self.assertNotIn("opaque-id", process.normalized_text)
                    self.assertFalse(self.provider_log.exists())
                    self.assertEqual(
                        process.final_terminal_attributes,
                        process.original_terminal_attributes,
                    )

    def test_internal_picker_dumb_fallback_retries_disabled_then_commits(self) -> None:
        """Use complete numbered line input without cursor or identity output."""

        source = r'''
import sys

from story_writing_agents.interactive_terminal import (
    Choice,
    InteractivePickerInput,
    Prompt,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
outcome = InteractivePickerInput(terminal).read_picker(
    Prompt("Choose fallback"),
    (
        Choice("secret-1", "Alpha"),
        Choice("secret-2", "Beta", enabled=False, disabled_reason="Unavailable"),
        Choice("secret-3", "Gamma"),
    ),
)
assert outcome.text == "secret-3"
print("RESULT=" + outcome.kind.value)
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "dumb"},
            terminal_columns=40,
        )
        initial = process.wait_for_text("3. [available] Gamma")
        self.assertIn("1. [highlighted] Alpha", initial)
        self.assertIn("2. [disabled] Beta", initial)
        self.assertNotIn("secret-", initial)
        process.send_line("2")
        retry = process.wait_for_text("[validation]")
        self.assertEqual(retry.count("[validation]"), 1)
        process.send_line("3")
        result = process.wait_for_text("RESULT=commit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("[completed]", result)
        self.assertIn("3. [selected] [highlighted] Gamma", result)
        self.assertNotIn("secret-", process.normalized_text)
        self.assertNotIn("\x1b[J", process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_semantic_views_use_ansi_and_no_color_profiles_on_real_ptys(
        self,
    ) -> None:
        """Prove decorative SGR is optional and terminal restoration is exact."""

        ansi = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            decorative_color=True,
        )
        ansi.wait_for_text("story[lobby]> ")
        self.assertIn(b"\x1b[1mStory chat\x1b[0m", ansi.raw_bytes)
        self.assertIn(b"\x1b[33m  [warning]", ansi.raw_bytes)
        self.assertIn(b"\x1b[0m", ansi.raw_bytes)
        mark = ansi.output_mark()
        ansi.send_line("/help /run")
        ansi.wait_for_text("Help [completed]", after=mark)
        ansi.wait_for_text("story[lobby]> ", after=mark)
        dense_ansi = bytes(ansi.raw_bytes[mark:])
        self.assertIn(b"\x1b[1mHelp [completed]\x1b[0m", dense_ansi)
        self.assertIn(b"\x1b[35m  [command] [completed]", dense_ansi)
        ansi.send_line("/quit")
        self.assertEqual(ansi.wait_for_exit(), 0, ansi.normalized_text)
        self.assertEqual(
            ansi.final_terminal_attributes,
            ansi.original_terminal_attributes,
        )

        no_color = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
        )
        no_color.wait_for_text("story[lobby]> ")
        self.assertIn("Story chat\n", no_color.normalized_text)
        self.assertNotIn(b"\x1b[1mStory chat", no_color.raw_bytes)
        self.assertNotIn(b"\x1b[33m  [warning]", no_color.raw_bytes)
        mark = no_color.output_mark()
        no_color.send_line("/agents")
        no_color.wait_for_text("Agents (4) [completed]", after=mark)
        no_color.wait_for_text("story[lobby]> ", after=mark)
        dense_plain = bytes(no_color.raw_bytes[mark:])
        self.assertNotIn(b"\x1b[1mAgents", dense_plain)
        self.assertNotIn(b"\x1b[34m  [agent]", dense_plain)
        no_color.send_line("/quit")
        self.assertEqual(no_color.wait_for_exit(), 0, no_color.normalized_text)
        self.assertEqual(
            no_color.final_terminal_attributes,
            no_color.original_terminal_attributes,
        )

        line = self._spawn_cli(())
        line.wait_for_text("story[lobby]> ")
        mark = line.output_mark()
        line.send_line("/session list")
        line.wait_for_text("Story sessions (0) [empty]", after=mark)
        line.wait_for_text("[session] No story sessions found.", after=mark)
        line.wait_for_text("story[lobby]> ", after=mark)
        self.assertNotIn(b"\x1b", bytes(line.raw_bytes[mark:]))
        line.send_line("/quit")
        self.assertEqual(line.wait_for_exit(), 0, line.normalized_text)
        self.assertEqual(
            line.final_terminal_attributes,
            line.original_terminal_attributes,
        )

    def test_settings_ansi_picker_save_history_isolation_and_restore_repeatedly(
        self,
    ) -> None:
        """Exercise the real settings picker, prompt boundary, and next-shell save."""

        expected_languages = ("EN", "JA", "ZH")
        for attempt, expected_language in enumerate(expected_languages):
            with self.subTest(attempt=attempt):
                process = self._spawn_cli(
                    (),
                    environment_overrides={"TERM": "xterm-256color"},
                    terminal_columns=48,
                    decorative_color=True,
                )
                process.wait_for_text("story[lobby]> ")
                mark = process.output_mark()
                process.send_line("/settings")
                opened = process.wait_for_text(
                    "Choose a setting to edit, save, or cancel.",
                    after=mark,
                )
                self.assertIn("Settings [open]", opened)
                self.assertIn("[highlighted] Language", opened)
                self.assertIn(b"\x1b[1mSettings [open]\x1b[0m", process.raw_bytes)

                language_mark = process.output_mark()
                process.send_text("\n")
                process.wait_for_text(
                    "Choose the workspace language.",
                    after=language_mark,
                )
                process.send_text("\x1b[B\n")
                process.wait_for_text(
                    f"[selected] [highlighted] {expected_language}",
                    after=language_mark,
                )
                process.wait_for_text(
                    "Choose a setting to edit, save, or cancel.",
                    after=language_mark,
                )
                returned_mark = process.output_mark()
                process.send_text(("\x1b[B" * 6) + "\n")
                process.wait_for_text(
                    "[saved] Settings saved.",
                    after=returned_mark,
                )
                process.wait_for_text("story[lobby]> ", after=returned_mark)

                history_mark = process.output_mark()
                process.send_text("\x1b[A")
                process.wait_for_text("story[lobby]> /settings", after=history_mark)
                self.assertNotIn(
                    "story[lobby]> /cancel",
                    process.text_after(history_mark),
                )
                process.send_interrupt()
                process.wait_for_text("[cancelled]", after=history_mark)
                process.wait_for_text("story[lobby]> ", after=history_mark)
                process.send_line("/quit")
                self.assertEqual(
                    process.wait_for_exit(),
                    0,
                    process.normalized_text,
                )
                self.assertEqual(
                    self.workspace.read_json("config/system_defaults.json")[
                        "language"
                    ],
                    expected_language,
                )
                self.assertFalse(self.provider_log.exists())
                self.assertEqual(
                    process.final_terminal_attributes,
                    process.original_terminal_attributes,
                )

    def test_settings_no_color_cancel_events_clear_and_restore_repeatedly(self) -> None:
        """Cancel settings through Escape, VINTR, and VEOF without mutation."""

        for event_name in ("escape", "interrupt", "eof"):
            for attempt in range(3):
                with self.subTest(event=event_name, attempt=attempt):
                    before = _durable_tree(self.workspace.root)
                    process = self._spawn_cli(
                        (),
                        environment_overrides={"TERM": "xterm-256color"},
                        terminal_columns=30,
                    )
                    process.wait_for_text("story[lobby]> ")
                    mark = process.output_mark()
                    process.send_line("/settings")
                    process.wait_for_text(
                        "[highlighted] Language",
                        after=mark,
                    )
                    event_mark = process.output_mark()
                    if event_name == "escape":
                        process.send_text("\x1b")
                    elif event_name == "interrupt":
                        process.send_interrupt()
                    else:
                        process.send_eof()
                    process.wait_for_text(
                        "[cancelled] Settings",
                        after=event_mark,
                    )
                    cancelled = process.wait_for_text(
                        "story[lobby]> ",
                        after=event_mark,
                    )
                    self.assertIn(
                        "Settingscancelled;nopendingchangesweresaved.",
                        "".join(cancelled.split()),
                    )
                    self.assertNotRegex(
                        cancelled,
                        r"\x1b\[(?:0|1|3[1-7])m",
                    )
                    process.send_line("/quit")
                    self.assertEqual(
                        process.wait_for_exit(),
                        0,
                        process.normalized_text,
                    )
                    self.assertEqual(_durable_tree(self.workspace.root), before)
                    self.assertFalse(self.provider_log.exists())
                    self.assertEqual(
                        process.final_terminal_attributes,
                        process.original_terminal_attributes,
                    )

    def test_settings_dumb_fallback_keeps_cancel_literal_and_provider_free(self) -> None:
        """Use numbered settings fallback and preserve model text literally."""

        process = self._spawn_cli((), terminal_columns=36)
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_line("/settings")
        process.wait_for_text("8. [available] Cancel", after=mark)
        process.send_line("5")
        process.wait_for_text("Codex model [settings] > ", after=mark)
        model_mark = process.output_mark()
        process.send_line("/cancel")
        process.wait_for_text("8. [available] Cancel", after=model_mark)
        process.send_line("7")
        process.wait_for_text("Settings saved", after=mark)
        process.wait_for_text("story[lobby]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            self.workspace.read_json("config/system_defaults.json")["codex_model"],
            "/cancel",
        )
        self.assertNotIn("\x1b", process.text_after(mark))
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_bare_entry_creates_session_then_quit_releases_only_access(
        self,
    ) -> None:
        """Prove lobby orientation, explicit creation, prompts, and safe quit."""

        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        self.assertIn("Story chat\n", process.normalized_text)
        self.assertIn(
            f"Workspace:{self.workspace.root}",
            "".join(process.normalized_text.split()),
        )
        self.assertIn("Session: lobby\n", process.normalized_text)

        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha:lead]> ")
        self.assertIn("Session: alpha (created)\n", process.normalized_text)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertFalse(
            self.workspace.path(
                f"{_SESSION_DIRECTORY}/alpha.access.json"
            ).exists()
        )
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "alpha")
        self.assertFalse(self.provider_log.exists())
        self.assertNotIn("[suggestions]", process.normalized_text)
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", process.normalized_text)

    def test_capable_main_editor_previews_edits_clears_and_restores(self) -> None:
        """Exercise live suggestions and exact PTY cleanup without side effects."""

        before = _durable_tree(self.workspace.root)
        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=20,
        )
        process.wait_for_text("story[lobby]> ")
        assert termios is not None
        active_attributes = termios.tcgetattr(process.master_fd)
        self.assertFalse(active_attributes[3] & termios.ICANON)
        self.assertFalse(active_attributes[3] & termios.ECHO)

        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("/exit", after=preview_mark)
        preview = process.text_after(preview_mark)
        self.assertIn("Suggestions [open]", preview)
        compact_preview = "".join(preview.split())
        for token in (
            "/say",
            "/to",
            "/agent",
            "/agents",
            "/language",
            "/persona",
            "/reset",
            "/settings",
            "/run",
            "/draft-file",
            "/session",
            "/conversation",
            "/search",
            "/transcript",
            "/inspect",
            "/history",
            "/status",
            "/help",
            "/?",
            "/quit",
            "/exit",
        ):
            with self.subTest(token=token):
                self.assertIn(token, compact_preview)
        self.assertNotIn("/compose", preview)
        self.assertNotIn("/send", preview)
        self.assertNotIn("/cancel", preview)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        process.send_text("statuX")
        process.wait_for_text(
            "story[lobby]> /stat\nuX",
            after=preview_mark,
        )
        backspace_mark = process.output_mark()
        process.send_text("\x7f")
        process.wait_for_text("Suggestions [open]", after=backspace_mark)
        edited = process.text_after(backspace_mark)
        self.assertIn("Suggestions [open]", edited)
        self.assertIn("story[lobby]> /stat\nu", edited)
        self.assertIn("/status", "".join(edited.split()))
        process.send_text("s")
        process.wait_for_text(
            "story[lobby]> /stat\nus",
            after=backspace_mark,
        )

        submit_mark = process.output_mark()
        process.send_text("\n")
        submitted = process.wait_for_text("[session]\n", after=submit_mark)
        process.wait_for_text("story[lobby]> ", after=submit_mark)
        self.assertIn("\x1b[J", submitted)
        self.assertNotIn("Suggestions [open]", submitted)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_main_history_restores_draft_edits_preview_and_nonempty_veof(
        self,
    ) -> None:
        """Exercise real CLI history without persistence or cleanup drift."""

        before = _durable_tree(self.workspace.root)
        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=80,
        )
        process.wait_for_text("story[lobby]> ")

        status_mark = process.output_mark()
        process.send_line("/status")
        process.wait_for_text("story[lobby]> ", after=status_mark)

        draft_mark = process.output_mark()
        process.send_text("draft-tail" + "\x1b[D" * 4)
        process.send_text("\x1b[A")
        process.wait_for_text("story[lobby]> /status", after=draft_mark)
        close_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("story[lobby]> /status", after=close_mark)
        process.send_text("\x1b[BX")
        process.wait_for_text("story[lobby]> draft-Xtail", after=draft_mark)
        process.send_text("\n")
        process.wait_for_text("[error session_not_attached]", after=draft_mark)
        process.wait_for_text("story[lobby]> ", after=draft_mark)

        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("Suggestions [open]", after=preview_mark)
        process.send_text("\x1b[B")
        process.wait_for_text("[highlighted] /to", after=preview_mark)
        close_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("story[lobby]> /", after=close_mark)
        process.send_text("\x1b[A")
        recalled = process.wait_for_text(
            "story[lobby]> draft-Xtail",
            after=preview_mark,
        )
        self.assertIn("\x1b[J", recalled)
        process.send_interrupt()
        process.wait_for_text("[cancelled] No action was taken.", after=preview_mark)
        process.wait_for_text("story[lobby]> ", after=preview_mark)

        veof_mark = process.output_mark()
        process.send_text("/status")
        process.send_eof()
        process.wait_for_text("[session]", after=veof_mark)
        process.wait_for_text("story[lobby]> ", after=veof_mark)
        recall_mark = process.output_mark()
        process.send_text("\x1bOA\n")
        process.wait_for_text("story[lobby]> /status", after=recall_mark)
        process.wait_for_text("[session]", after=recall_mark)

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertFalse(
            any(
                path.name
                in {
                    ".python_history",
                    ".story_history",
                    ".story-writing-agents-history",
                }
                for path in self.temporary_root.rglob("*")
            )
        )
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_main_completion_navigates_tabs_restores_snapshot_and_commits(self) -> None:
        """Exercise selectable ANSI completion through the real public CLI."""

        before = _durable_tree(self.workspace.root)
        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color"},
            decorative_color=True,
            terminal_columns=40,
        )
        process.wait_for_text("story[lobby]> ")

        mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("Suggestions [open]", after=mark)
        process.send_text("\x1b[B")
        process.wait_for_text("[highlighted] /to", after=mark)
        process.send_text("\t")
        process.wait_for_text("story[lobby]> /to", after=mark)
        snapshot_mark = process.output_mark()
        process.send_text("\x1b")
        process.wait_for_text("story[lobby]> /", after=snapshot_mark)
        self.assertNotIn("Suggestions [open]", process.text_after(snapshot_mark))

        process.send_text("stat\t\n")
        process.wait_for_text("story[lobby]> /status", after=snapshot_mark)
        process.wait_for_text("[session]", after=snapshot_mark)
        process.wait_for_text("story[lobby]> ", after=snapshot_mark)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_main_completion_no_color_narrow_empty_and_interrupt_cleanup(self) -> None:
        """Keep narrow palettes legible without decorative SGR on a real PTY."""

        before = _durable_tree(self.workspace.root)
        process = self._spawn_cli(
            (),
            environment_overrides={"TERM": "xterm-256color", "NO_COLOR": "1"},
            terminal_columns=18,
        )
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_text("/s")
        process.wait_for_text("Suggestions [open", after=mark)
        process.wait_for_text("[highlighted]", after=mark)
        narrow = "".join(process.text_after(mark).split())
        self.assertIn("Suggestions[open]", narrow)
        self.assertIn("[highlighted]/say", narrow)
        process.send_text("z")
        process.wait_for_text("[empty]", after=mark)
        self.assertIn(
            "[empty]Nosuggestions.",
            "".join(process.text_after(mark).split()),
        )
        output = bytes(process.raw_bytes[mark:])
        for decorative_sgr in (b"\x1b[35m", b"\x1b[36m", b"\x1b[0m"):
            self.assertNotIn(decorative_sgr, output)

        process.send_interrupt()
        process.wait_for_text("[cancelled]", after=mark)
        self.assertIn(
            "[cancelled]Noactionwastaken.",
            "".join(process.text_after(mark).split()),
        )
        process.wait_for_text("story[lobby]> ", after=mark)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_editor_history_crosses_fallback_then_isolates_composition_and_instance(
        self,
    ) -> None:
        """Prove fallback continuity and both contextual and instance isolation."""

        source = r'''
import json
import os
import sys

from story_writing_agents.interactive_line_editor import (
    InteractiveMainLineEditor,
    bind_suggestion_reader,
    composition_slash_command_suggestions,
)

editor = InteractiveMainLineEditor(input, sys.stdin, sys.stdout)
os.environ["TERM"] = "dumb"
fallback = editor.read_line("fallback> ")
os.environ["TERM"] = "xterm-256color"
composition = bind_suggestion_reader(
    editor,
    composition_slash_command_suggestions,
)("compose> ")
recalled = editor.read_line("main> ")
other = InteractiveMainLineEditor(input, sys.stdin, sys.stdout)
isolated = other.read_line("other> ")
print(
    "RESULT="
    + json.dumps(
        {
            "fallback": fallback,
            "composition": composition,
            "recalled": recalled,
            "isolated": isolated,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
        )
        process.wait_for_text("fallback> ")
        process.send_line("  fallback 🌊  ")
        process.wait_for_text("compose> ")
        process.send_text("\x1b[Acontext\n")
        process.wait_for_text("main> ")
        process.send_text("\x1b[A\n")
        process.wait_for_text("other> ")
        process.send_text("\x1bOA\n")
        process.wait_for_text(
            'RESULT={"fallback":"  fallback 🌊  ",'
            '"composition":"context",'
            '"recalled":"  fallback 🌊  ",'
            '"isolated":""}'
        )
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_capable_chat_composer_previews_edits_sanitizes_and_restores(
        self,
    ) -> None:
        """Exercise the internal chat reader binding on one real narrow PTY."""

        source = r'''
import json
import sys

from story_writing_agents import PLOT_ARCHITECT
from story_writing_agents.interactive_line_editor import (
    InteractiveMainLineEditor,
    bind_suggestion_reader,
    composition_slash_command_suggestions,
)
from story_writing_agents.interactive_terminal import (
    ComposeMode,
    build_contextual_terminal,
)

editor = InteractiveMainLineEditor(input, sys.stdin, sys.stdout)
composition_reader = bind_suggestion_reader(
    editor,
    composition_slash_command_suggestions,
)
terminal = build_contextual_terminal(
    input,
    sys.stdout.write,
    main_read_line=editor.read_line,
    composition_read_line=composition_reader,
)
action = terminal.compose(ComposeMode.CHAT, agent_id=PLOT_ARCHITECT)
print(
    "RESULT="
    + json.dumps(
        {
            "kind": action.kind.value,
            "text": action.text,
            "line_count": action.line_count,
            "character_count": action.character_count,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=20,
        )
        initial_prompt = process.wait_for_text("se[chat:plot:1]> ")
        collapsed_prompt = "".join(initial_prompt.split())
        for fact in (
            "Mode:chat",
            "Target:plot",
            "Nextline:1",
            "Lines:0",
            "Characters:0",
            "Use/sendtosubmitor/canceltodiscard.",
            "compose[chat:plot:1]>",
        ):
            self.assertIn(fact, collapsed_prompt)
        assert termios is not None
        active_attributes = termios.tcgetattr(process.master_fd)
        self.assertFalse(active_attributes[3] & termios.ICANON)
        self.assertFalse(active_attributes[3] & termios.ECHO)

        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("/cancel", after=preview_mark)
        preview = process.text_after(preview_mark)
        self.assertIn("[suggestions]", preview)
        self.assertIn("/send", preview)
        self.assertIn("/cancel", preview)
        self.assertNotIn("/status", preview)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        clear_mark = process.output_mark()
        process.send_text("\x7f")
        cleared = process.wait_for_text(
            "se[chat:plot:1]> ",
            after=clear_mark,
        )
        self.assertIn("\x1b[J", cleared)
        self.assertNotIn("[suggestions]", cleared)

        process.send_line("/say /send")
        process.wait_for_text("se[chat:plot:2]> ")
        process.send_text("韓界e\u0301\x00")
        process.wait_for_text(r"\x00")
        process.send_text("\n")
        process.wait_for_text("se[chat:plot:3]> ")

        edit_mark = process.output_mark()
        process.send_text("/sX\x1b[D\x7fsend\x1b[3~")
        edited = process.wait_for_text("/cancel", after=edit_mark)
        self.assertIn("[suggestions]", edited)
        self.assertIn("/send", edited)
        self.assertNotIn("/status", edited)
        process.send_text("\n")

        result_output = process.wait_for_text("RESULT=")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        result_match = re.search(r"RESULT=(\{[^\n]+\})", result_output)
        self.assertIsNotNone(result_match, result_output)
        assert result_match is not None
        self.assertEqual(
            json.loads(result_match.group(1)),
            {
                "kind": "send",
                "text": "/send\n韓界e\u0301\x00",
                "line_count": 2,
                "character_count": 11,
            },
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_no_color_composer_confirmation_reserved_keys_and_invalid_retry(
        self,
    ) -> None:
        """Exercise both semantic input routes without decorative SGR/history."""

        source = r'''
import json
import sys

from story_writing_agents.interactive_terminal import (
    ComposeMode,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
action = terminal.compose(ComposeMode.RUN)
confirmation = terminal.request_confirmation("Continue?", default=False)
print(
    "RESULT="
    + json.dumps(
        {
            "kind": action.kind.value,
            "text": action.text,
            "line_count": action.line_count,
            "character_count": action.character_count,
            "confirmation": confirmation.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "xterm-256color", "NO_COLOR": "1"},
            terminal_columns=40,
        )
        mark = process.output_mark()
        opened = process.wait_for_text("compose[run:1]> ", after=mark)
        self.assertIn("Mode: run", opened)
        self.assertIn("Next line: 1", opened)
        self.assertNotRegex(opened, r"\x1b\[(?:0|1|3[1-7])m")

        process.send_text("\x1b[A\x1b[B\t\x1b[Z\x1bexact line\n")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_line("/send")
        confirmation = process.wait_for_text("confirm [y/N]> ", after=mark)
        self.assertIn("Question: Continue?", confirmation)
        self.assertIn("Choices: y/yes or n/no", confirmation)
        self.assertNotRegex(confirmation, r"\x1b\[(?:0|1|3[1-7])m")

        invalid_mark = process.output_mark()
        process.send_text("\x1b[A\x1b[B\t\x1b[Zmaybe\n")
        invalid = process.wait_for_text("Choices [error]:", after=invalid_mark)
        self.assertIn("Choices [error]:", invalid)
        self.assertIn("[hint] Enter y, yes, n, or no.", invalid)
        process.send_line("yes")
        result = process.wait_for_text("RESULT=", after=mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        match = re.search(r"RESULT=(\{[^\n]+\})", result)
        self.assertIsNotNone(match, result)
        assert match is not None
        self.assertEqual(
            json.loads(match.group(1)),
            {
                "kind": "send",
                "text": "exact line",
                "line_count": 1,
                "character_count": 10,
                "confirmation": "yes",
            },
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_dumb_terminal_semantic_inputs_use_plain_fallback_without_controls(
        self,
    ) -> None:
        """Keep structured composer and confirmation facts on line fallback."""

        source = r'''
import json
import sys

from story_writing_agents.interactive_terminal import (
    ComposeMode,
    build_stream_terminal,
)

terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
action = terminal.compose(ComposeMode.RUN)
confirmation = terminal.request_confirmation("Continue?", default=True)
print(
    "RESULT="
    + json.dumps(
        {
            "text": action.text,
            "confirmation": confirmation.value,
        },
        separators=(",", ":"),
    )
)
'''
        before = _durable_tree(self.workspace.root)
        process = self._spawn_python(
            source,
            environment_overrides={"TERM": "dumb"},
            terminal_columns=40,
        )
        mark = process.output_mark()
        opened = process.wait_for_text("compose[run:1]> ", after=mark)
        self.assertIn("Mode: run", opened)
        self.assertNotIn("\x1b", opened)
        process.send_line("fallback line")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_line("/send")
        confirmation = process.wait_for_text("confirm [Y/n]> ", after=mark)
        self.assertIn("Question: Continue?", confirmation)
        self.assertIn("Default: yes", confirmation)
        self.assertNotIn("\x1b", confirmation)
        process.send_line("")
        result = process.wait_for_text("RESULT=", after=mark)
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn(
            'RESULT={"text":"fallback line","confirmation":"yes"}',
            result,
        )
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())
        self.assertEqual(
            process.final_terminal_attributes,
            process.original_terminal_attributes,
        )

    def test_conversation_management_routes_controls_and_main_interrupt(
        self,
    ) -> None:
        """Cover selected subjects, sticky routing, safe output, and main VINTR."""

        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_line("/session create alpha")
        process.wait_for_text("story[alpha:lead]> ", after=mark)

        mark = process.output_mark()
        process.send_line("/conversation new First Subject")
        created_output = process.wait_for_text("Subject: First Subject", after=mark)
        match = re.search(
            r"Created and focused ([A-Za-z0-9_-]+) \| Subject: First Subject",
            created_output,
        )
        self.assertIsNotNone(match, created_output)
        assert match is not None
        first_handle = match.group(1)

        mark = process.output_mark()
        process.send_line("/conversation new Second Subject")
        process.wait_for_text("Subject: Second Subject", after=mark)
        mark = process.output_mark()
        process.send_line("/conversation list")
        listed = process.wait_for_text("First Subject", after=mark)
        process.wait_for_text("Second Subject", after=mark)
        self.assertIn("focused", listed)

        mark = process.output_mark()
        process.send_line(f"/conversation use {first_handle}")
        process.wait_for_text("Current focus: ", after=mark)
        mark = process.output_mark()
        process.send_line("/conversation subject Renamed Subject")
        process.wait_for_text("Subject: Renamed Subject", after=mark)
        mark = process.output_mark()
        process.send_line("/conversation archive")
        confirmation = process.wait_for_text("confirm [y/N]> ", after=mark)
        self.assertIn("Choices: y/yes or n/no", confirmation)
        self.assertIn("Default: no", confirmation)
        process.send_line("yes")
        process.wait_for_text("Archived ", after=mark)
        process.wait_for_text("Subject: Second Subject", after=mark)

        mark = process.output_mark()
        process.send_line("/to plot")
        process.wait_for_text(
            "Sticky target set to plot_architect.",
            after=mark,
        )
        process.wait_for_text("story[alpha:plot]> ", after=mark)

        mark = process.output_mark()
        process.send_line("ordinary sticky turn")
        process.wait_for_text("plot [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)

        mark = process.output_mark()
        process.send_line("@character one-shot turn")
        routed = process.wait_for_text(
            "character [completed]> deterministic advisory reply",
            after=mark,
        )
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        self.assertIn(
            "stickytargetremainsplot_architect.",
            "".join(routed.split()),
        )

        mark = process.output_mark()
        process.send_line("show visible controls")
        safe_output = process.wait_for_text(r"\x1b[31mred", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        self.assertIn(r"bell \x07", safe_output)
        self.assertNotIn(b"\x1b[", process.raw_bytes)
        self.assertNotIn(b"\x1b]", process.raw_bytes)
        self.assertNotIn(b"\x9b", process.raw_bytes)

        mark = process.output_mark()
        process.send_interrupt()
        process.wait_for_text("[cancelled] No action was taken.", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        catalog = FilesystemConversationCatalog(self.workspace)
        conversations = catalog.list("alpha", include_archived=True)
        self.assertEqual(len(conversations), 2)
        by_subject = {item.subject: item for item in conversations}
        self.assertIs(
            by_subject["Renamed Subject"].status,
            ConversationStatus.ARCHIVED,
        )
        successor = by_subject["Second Subject"]
        self.assertIs(successor.status, ConversationStatus.ACTIVE)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            successor.conversation_id,
        )
        events = FilesystemConversationEventStore(
            self.workspace,
            catalog=catalog,
        ).read("alpha", successor.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )
        history_names = {
            ".python_history",
            ".story_history",
            ".story-writing-agents-history",
        }
        self.assertFalse(
            any(path.name in history_names for path in self.temporary_root.rglob("*"))
        )

    def test_explicit_chat_switches_sessions_and_confirmed_pause_is_distinct(
        self,
    ) -> None:
        """Prove explicit attachment, safe switching, and confirmed deactivation."""

        selected = self._seed_default_session()
        noncanonical_option = self.workspace.root / ".." / self.workspace.root.name
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(noncanonical_option)),
            cwd=self.temporary_root,
        )
        process.wait_for_text("story[alpha:lead]> ")
        self.assertIn(
            f"Workspace:{self.workspace.root}",
            "".join(process.normalized_text.split()),
        )
        self.assertIn("Session: alpha (opened)", process.normalized_text)
        self.assertIn(selected.subject, process.normalized_text)

        mark = process.output_mark()
        process.send_line("/session create beta")
        process.wait_for_text("Session: beta (created)", after=mark)
        process.wait_for_text("story[beta:lead]> ", after=mark)
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

        mark = process.output_mark()
        process.send_line("/session open alpha")
        process.wait_for_text("Session: alpha (opened)", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/beta.access.json").exists()
        )

        mark = process.output_mark()
        process.send_line("/session pause")
        confirmation = process.wait_for_text("confirm [y/N]> ", after=mark)
        self.assertIn("Question: Pause session alpha?", confirmation)
        self.assertIn("Choices: y/yes or n/no", confirmation)
        self.assertIn("Default: no", confirmation)
        process.send_line("y")
        process.wait_for_text("[session] Paused alpha; default retained.", after=mark)
        process.wait_for_text("story[lobby]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        alpha = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        beta = self.workspace.read_json(f"{_SESSION_DIRECTORY}/beta.json")
        self.assertEqual(alpha["durable_state"], "paused")
        self.assertEqual(beta["durable_state"], "ready")
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "alpha")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        for session_id in ("alpha", "beta"):
            self.assertFalse(
                self.workspace.path(
                    f"{_SESSION_DIRECTORY}/{session_id}.access.json"
                ).exists()
            )
        self.assertFalse(self.provider_log.exists())

    def test_history_search_transcript_and_cited_retrieval_stay_scoped(
        self,
    ) -> None:
        """Exercise provider-free reads followed by trusted retrieval progress."""

        selected = self._seed_default_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")

        mark = process.output_mark()
        process.send_line("harbor beacon beside the vessel")
        process.wait_for_text("lead [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        provider_calls_before_reads = len(
            [
                record
                for record in self._provider_records()
                if record.get("event") == "started"
            ]
        )
        self.assertEqual(provider_calls_before_reads, 1)

        mark = process.output_mark()
        process.send_line("/history 2")
        process.wait_for_text("History (1/2) [completed]", after=mark)
        process.wait_for_text("harbor beacon beside the vessel", after=mark)
        history = process.wait_for_text("deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertIn("History (2/2) [completed]", history)
        self.assertIn("deterministic advisory reply", history)

        mark = process.output_mark()
        process.send_line("/search harbor beacon")
        search_output = process.wait_for_text(
            "Search results (1/1) [completed]",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertIn("Citation: conversation", search_output)
        self.assertIn(selected.subject, " ".join(search_output.split()))

        mark = process.output_mark()
        process.send_line(f"/transcript {selected.conversation_id} 2")
        process.wait_for_text("Transcript (1/2) [completed]", after=mark)
        process.wait_for_text(
            "harbor beacon beside the vessel",
            after=mark,
        )
        transcript_output = process.wait_for_text(
            "deterministic advisory reply",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertIn("deterministic advisory reply", transcript_output)
        self.assertEqual(
            len(
                [
                    record
                    for record in self._provider_records()
                    if record.get("event") == "started"
                ]
            ),
            provider_calls_before_reads,
        )

        mark = process.output_mark()
        process.send_line("retrieve harbor beacon")
        retrieval_output = process.wait_for_text(
            "Retrieval search action 1 started.",
            after=mark,
        )
        process.wait_for_text(
            "Retrieval search action 1 completed:",
            after=mark,
        )
        process.wait_for_text("Retrieval read action 2 started.", after=mark)
        process.wait_for_text("Retrieval read action 2 completed:", after=mark)
        process.wait_for_text("lead [completed]> retrieval-assisted answer", after=mark)
        process.wait_for_text("[context] Source 1 [completed]", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        retrieval_output = process.text_after(mark)
        self.assertIn(f"Conversation: {selected.conversation_id}", retrieval_output)
        self.assertIn("Sequence: 1", retrieval_output)

        started = [
            record
            for record in self._provider_records()
            if record.get("event") == "started"
        ]
        self.assertEqual(len(started), provider_calls_before_reads + 3)
        self.assertEqual(
            [record["payload"]["retrieval"]["action_index"] for record in started[-3:]],
            [1, 2, 3],
        )
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        catalog = FilesystemConversationCatalog(self.workspace)
        events = FilesystemConversationEventStore(
            self.workspace,
            catalog=catalog,
        ).read("alpha", selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        self.assertEqual(events[-1].content, "retrieval-assisted answer")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_run_composer_send_cancel_interrupt_and_eof_have_exact_durability(
        self,
    ) -> None:
        """Cover multiline consensus plus local cancel, VINTR, and VEOF discards."""

        selected = self._seed_default_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            environment_overrides={"TERM": "xterm-256color"},
            terminal_columns=20,
        )
        process.wait_for_text("story[alpha:lead]> ")

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        preview_bytes = _durable_tree_without_access(self.workspace.root)
        preview_mark = process.output_mark()
        process.send_text("/")
        process.wait_for_text("/cancel", after=preview_mark)
        preview = process.text_after(preview_mark)
        self.assertIn("[suggestions]", preview)
        self.assertIn("/send", preview)
        self.assertIn("/cancel", preview)
        self.assertNotIn("/status", preview)
        self.assertEqual(
            _durable_tree_without_access(self.workspace.root),
            preview_bytes,
        )
        self.assertFalse(self.provider_log.exists())
        clear_mark = process.output_mark()
        process.send_text("\x7f")
        cleared = process.wait_for_text("compose[run:1]> ", after=clear_mark)
        self.assertIn("\x1b[J", cleared)
        self.assertNotIn("[suggestions]", cleared)
        process.send_line("")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_line("/send")
        rejected = process.wait_for_text("send.", after=mark)
        self.assertIn("[compose]Nothingtosend.", "".join(rejected.split()))
        process.send_line("/say /send")
        process.wait_for_text("compose[run:3]> ", after=mark)
        process.send_line("/say /cancel")
        process.wait_for_text("compose[run:4]> ", after=mark)
        process.send_line("final literal line")
        process.wait_for_text("compose[run:5]> ", after=mark)
        process.send_line("/send")
        process.wait_for_text("core updates:", after=mark)
        successful_output = process.wait_for_text("Conversation scope", after=mark)
        collapsed = "".join(successful_output.split())
        self.assertIn(
            "[compose]Sending4lines,33characterstoconsensus.",
            collapsed,
        )
        ordered_fragments = (
            "Startedpromptconsensus.",
            "Draftedrevisionr0.",
            "Auditstartedforr0atround0.",
            "Auditcompletedforr0atround0;conflicts:none.",
            "Artifactscommittedforr0:",
            "Consensusreachedonr0after0revisionrounds.",
            "Consensus[completed][success]revision:r0",
        )
        for fragment in ordered_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, collapsed)
        offsets = [collapsed.index(fragment) for fragment in ordered_fragments]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn("[waiting]", successful_output)
        durable_after_success = _durable_tree_without_access(self.workspace.root)

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("discarded by slash command")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_line("/cancel")
        discarded = process.wait_for_text("r sent.", after=mark)
        self.assertIn(
            "[compose]Discarded1line.Nomessagewassavedorsent.",
            "".join(discarded.split()),
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertEqual(
            _durable_tree_without_access(self.workspace.root),
            durable_after_success,
        )

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("discarded by interrupt")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_interrupt()
        discarded = process.wait_for_text("r sent.", after=mark)
        self.assertIn(
            "[compose]Discarded1line.Nomessagewassavedorsent.",
            "".join(discarded.split()),
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertEqual(
            _durable_tree_without_access(self.workspace.root),
            durable_after_success,
        )

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("discarded by eof")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_eof()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn(
            "[compose]Discarded1line.Nomessagewassavedorsent.",
            "".join(process.text_after(mark).split()),
        )
        self.assertEqual(
            _durable_tree_without_access(self.workspace.root),
            durable_after_success,
        )

        inspection = self.workspace.inspect_session("alpha")
        self.assertEqual(len(inspection["debates"]), 1)
        self.assertEqual(inspection["debates"][0]["status"], "consensus")
        manuscripts = [
            path
            for path in self.workspace.path("manuscript").rglob("*")
            if path.is_file() and path.name != ".gitkeep"
        ]
        self.assertEqual(len(manuscripts), 1)
        self.assertEqual(
            manuscripts[0].read_text(encoding="utf-8"),
            "deterministic candidate",
        )
        self.assertEqual(
            self.workspace.read_text("core_documents/global_outline.md"),
            "updated outline",
        )
        catalog = FilesystemConversationCatalog(self.workspace)
        self.assertEqual(
            FilesystemConversationEventStore(
                self.workspace,
                catalog=catalog,
            ).read("alpha", selected.conversation_id),
            (),
        )
        started = [
            record
            for record in self._provider_records()
            if record.get("event") == "started"
        ]
        self.assertEqual(len(started), 5)
        self.assertEqual(
            started[0]["payload"]["user_payload"],
            "\n/send\n/cancel\nfinal literal line",
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_main_eof_exits_normally_without_ending_or_mutating_session(self) -> None:
        """Deliver configured VEOF at main input and prove detach-only cleanup."""

        selected = self._seed_default_session()
        before = _durable_tree_without_access(self.workspace.root)
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")
        process.send_eof()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree_without_access(self.workspace.root), before)
        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )
        self.assertFalse(self.provider_log.exists())

    def test_advisory_vintr_terminates_provider_taints_then_restarts_thread(
        self,
    ) -> None:
        """Prove joined real-signal advisory cancellation and clean retry semantics."""

        selected = self._seed_default_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")
        mark = process.output_mark()
        process.send_line("/to plot")
        process.wait_for_text("story[alpha:plot]> ", after=mark)

        mark = process.output_mark()
        process.send_line("seed advisory")
        process.wait_for_text("plot [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        manifest_path = f"{_SESSION_DIRECTORY}/alpha.json"
        manifest = self.workspace.read_json(manifest_path)
        thread_key = "codex:plot_architect"
        self.assertIs(
            manifest["provider_threads"][thread_key]["tainted"],
            False,
        )

        self._set_provider_control(mode="block")
        records_before_cancel = len(self._provider_records())
        mark = process.output_mark()
        process.send_line("cancel advisory")
        process.wait_for_text(
            "Plot & Structure Architect is responding; Ctrl-C cancels this turn.",
            after=mark,
        )

        def is_cancelled_invocation(record: dict[str, Any]) -> bool:
            """Identify the blocking advisory provider invocation by payload."""

            payload = record.get("payload")
            return (
                record.get("event") == "started"
                and isinstance(payload, dict)
                and payload.get("content") == "cancel advisory"
            )

        blocked = self._wait_for_provider_record(
            process,
            is_cancelled_invocation,
            after_count=records_before_cancel,
        )
        process.send_interrupt()
        cancelled_output = process.wait_for_text(
            "[cancelled] Plot & Structure Architect turn was interrupted.",
            after=mark,
        )
        process.wait_for_text(
            "[hint] The story session remains attached;",
            after=mark,
        )
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        self.assertIn(
            "thestorysessionremainsattached;theaffectedproviderthreadmay"
            "restartonthenextmessage.",
            "".join(process.text_after(mark).casefold().split()),
        )

        blocked_pid = blocked["pid"]

        def is_blocked_termination(record: dict[str, Any]) -> bool:
            """Identify backend termination of the exact blocking provider PID."""

            return (
                record.get("event") == "terminated"
                and record.get("pid") == blocked_pid
            )

        self._wait_for_provider_record(
            process,
            is_blocked_termination,
            after_count=records_before_cancel,
        )
        with self.assertRaises(ProcessLookupError):
            os.kill(blocked_pid, 0)

        manifest = self.workspace.read_json(manifest_path)
        self.assertIs(
            manifest["provider_threads"][thread_key]["tainted"],
            True,
        )
        catalog = FilesystemConversationCatalog(self.workspace)
        store = FilesystemConversationEventStore(self.workspace, catalog=catalog)
        events = store.read("alpha", selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        self.assertEqual(
            events[-1].content,
            "CancelledError: conversation turn was cancelled",
        )

        self._set_provider_control(mode="auto")
        mark = process.output_mark()
        process.send_line("after cancellation")
        process.wait_for_text("plot [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        manifest = self.workspace.read_json(manifest_path)
        self.assertIs(
            manifest["provider_threads"][thread_key]["tainted"],
            False,
        )
        started = [
            record
            for record in self._provider_records()
            if record.get("event") == "started"
        ]
        self.assertEqual(len(started), 3)
        self.assertEqual(
            ["resume" in record["argv"] for record in started],
            [False, True, False],
        )
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", cancelled_output)

    def test_consensus_vintr_persists_interruption_and_restarts_tainted_drafter(
        self,
    ) -> None:
        """Prove real-signal interruption, artifact safety, and drafter retry."""

        selected = self._seed_default_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")

        mark = process.output_mark()
        process.send_line("/run seed consensus")
        process.wait_for_text("Consensus [completed] [success]", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        mark = process.output_mark()
        process.send_line("establish current lead thread")
        process.wait_for_text("lead [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        core_after_seed = self.workspace.read_text(
            "core_documents/global_outline.md"
        )
        seed_manuscripts = {
            path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in self.workspace.path("manuscript").rglob("*")
            if path.is_file() and path.name != ".gitkeep"
        }
        self.assertEqual(len(seed_manuscripts), 1)

        self._set_provider_control(mode="block")
        records_before_cancel = len(self._provider_records())
        mark = process.output_mark()
        process.send_line("/run cancel consensus")
        process.wait_for_text("Started prompt consensus.", after=mark)

        def is_blocked_consensus_draft(record: dict[str, Any]) -> bool:
            """Identify the blocked execution draft for the cancellation run."""

            payload = record.get("payload")
            return (
                record.get("event") == "started"
                and isinstance(payload, dict)
                and payload.get("phase") == "draft"
                and payload.get("user_payload") == "cancel consensus"
            )

        blocked = self._wait_for_provider_record(
            process,
            is_blocked_consensus_draft,
            after_count=records_before_cancel,
        )
        process.send_interrupt()
        process.wait_for_text(
            "Run interrupted after durable state transition.",
            after=mark,
        )
        process.wait_for_text("[cancelled] Debate ", after=mark)
        process.wait_for_text("was marked interrupted.", after=mark)
        process.wait_for_text("It cannot resume.", after=mark)
        process.wait_for_text('"alpha" remains ready and attached.', after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)

        blocked_pid = blocked["pid"]

        def is_consensus_termination(record: dict[str, Any]) -> bool:
            """Identify termination of the blocked consensus provider PID."""

            return (
                record.get("event") == "terminated"
                and record.get("pid") == blocked_pid
            )

        self._wait_for_provider_record(
            process,
            is_consensus_termination,
            after_count=records_before_cancel,
        )
        with self.assertRaises(ProcessLookupError):
            os.kill(blocked_pid, 0)

        interrupted_inspection = self.workspace.inspect_session("alpha")
        statuses = [item["status"] for item in interrupted_inspection["debates"]]
        self.assertEqual(statuses.count("consensus"), 1)
        self.assertEqual(statuses.count("interrupted"), 1)
        interrupted = next(
            item
            for item in interrupted_inspection["debates"]
            if item["status"] == "interrupted"
        )
        self.assertIs(interrupted["terminal"], True)
        self.assertEqual(
            self.workspace.read_text("core_documents/global_outline.md"),
            core_after_seed,
        )
        manuscripts_after_cancel = {
            path.relative_to(self.workspace.root).as_posix(): path.read_bytes()
            for path in self.workspace.path("manuscript").rglob("*")
            if path.is_file() and path.name != ".gitkeep"
        }
        self.assertEqual(manuscripts_after_cancel, seed_manuscripts)
        manifest_path = f"{_SESSION_DIRECTORY}/alpha.json"
        manifest = self.workspace.read_json(manifest_path)
        self.assertIs(
            manifest["provider_threads"]["codex:execution_specialist"]["tainted"],
            True,
        )

        self._set_provider_control(mode="auto")
        mark = process.output_mark()
        process.send_line("/run recovered consensus")
        process.wait_for_text("Consensus [completed] [success]", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        manifest = self.workspace.read_json(manifest_path)
        self.assertIs(
            manifest["provider_threads"]["codex:execution_specialist"]["tainted"],
            False,
        )
        drafter_invocations = [
            record
            for record in self._provider_records()
            if record.get("event") == "started"
            and record["payload"].get("agent_id") == "execution_specialist"
        ]
        self.assertEqual(
            [record["payload"]["phase"] for record in drafter_invocations],
            ["draft", "audit", "draft", "draft", "audit"],
        )
        self.assertEqual(
            ["resume" in record["argv"] for record in drafter_invocations],
            [False, True, True, False, True],
        )
        final_inspection = self.workspace.inspect_session("alpha")
        final_statuses = [item["status"] for item in final_inspection["debates"]]
        self.assertEqual(final_statuses.count("consensus"), 2)
        self.assertEqual(final_statuses.count("interrupted"), 1)
        final_manuscripts = [
            path
            for path in self.workspace.path("manuscript").rglob("*")
            if path.is_file() and path.name != ".gitkeep"
        ]
        self.assertEqual(len(final_manuscripts), 2)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_vintr_during_large_local_history_stops_sequential_rendering(
        self,
    ) -> None:
        """Use PTY backpressure to interrupt provider-free row rendering safely."""

        selected = self._seed_default_session()
        catalog = FilesystemConversationCatalog(self.workspace)
        store = FilesystemConversationEventStore(self.workspace, catalog=catalog)
        for ordinal in range(1, 13):
            turn_id = f"seed_{ordinal}"
            store.append(
                ConversationEventAppend(
                    session_id="alpha",
                    conversation_id=selected.conversation_id,
                    turn_id=turn_id,
                    sender="user",
                    recipient="lead_orchestrator",
                    kind=ConversationEventKind.USER_MESSAGE,
                    content=f"question {ordinal} " + ("q" * 4096),
                )
            )
            store.append(
                ConversationEventAppend(
                    session_id="alpha",
                    conversation_id=selected.conversation_id,
                    turn_id=turn_id,
                    sender="lead_orchestrator",
                    recipient="user",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=f"answer {ordinal} " + ("a" * 4096),
                )
            )
        before = _durable_tree_without_access(self.workspace.root)
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")
        mark = process.output_mark()
        process.send_line("/history 24")
        process.wait_for_text("History (1/24) [completed]", after=mark)
        process.send_interrupt()
        process.wait_for_text(
            "[cancelled] History cancelled; attached state is unchanged.",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        interrupted_output = process.text_after(mark)
        self.assertLess(interrupted_output.count("History ("), 24)
        self.assertNotIn("[context]", interrupted_output)
        self.assertEqual(
            _durable_tree_without_access(self.workspace.root),
            before,
        )
        self.assertFalse(self.provider_log.exists())
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_tty_startup_workspace_configuration_and_preflight_errors_are_safe(
        self,
    ) -> None:
        """Exercise natural startup failures without prompts, secrets, or mutation."""

        uninitialized = self.temporary_root / "uninitialized"
        uninitialized.mkdir()
        empty_before = _durable_tree(uninitialized)
        process = self._spawn_cli((), cwd=uninitialized)
        process.wait_for_text("[error workspace_not_initialized]")
        self.assertEqual(process.wait_for_exit(), 2, process.normalized_text)
        self.assertNotIn("story[", process.normalized_text)
        self.assertEqual(_durable_tree(uninitialized), empty_before)

        before = _durable_tree(self.workspace.root)
        process = self._spawn_cli(
            (
                "chat",
                "alpha",
                "--workspace",
                str(self.workspace.root),
                "--codex-timeout",
                "invalid",
            )
        )
        process.wait_for_text("[error configuration_invalid]")
        self.assertEqual(process.wait_for_exit(), 2, process.normalized_text)
        self.assertNotIn("story[", process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)

        empty_path = self.temporary_root / "empty-path"
        empty_path.mkdir()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root)),
            environment_overrides={"PATH": str(empty_path)},
        )
        process.wait_for_text("[error provider_preflight_failed]")
        process.wait_for_text("story[lobby]> ")
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before)
        self.assertFalse(self.provider_log.exists())

        combined = "\n".join(
            retained.normalized_text for retained in self._processes[-3:]
        )
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", combined)
        self.assertNotIn(b"\x1b[", combined.encode("utf-8"))

    def test_second_real_process_reports_blocked_access_without_exposing_token(
        self,
    ) -> None:
        """Prove a natural access conflict preserves owner and durable scope."""

        selected = self._seed_default_session()
        owner = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        owner.wait_for_text("story[alpha:lead]> ")
        access_path = f"{_SESSION_DIRECTORY}/alpha.access.json"
        access = self.workspace.read_json(access_path)
        claim_token = access["claim_token"]
        before_contender = _durable_tree(self.workspace.root)

        contender = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        contender.wait_for_text("[error session_access_blocked]")
        contender.wait_for_text("story[lobby]> ")
        contender.send_line("/quit")
        self.assertEqual(contender.wait_for_exit(), 0, contender.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before_contender)
        self.assertNotIn(claim_token, contender.normalized_text)
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            contender.normalized_text,
        )
        self.assertFalse(self.provider_log.exists())

        owner.send_line("/quit")
        self.assertEqual(owner.wait_for_exit(), 0, owner.normalized_text)
        self.assertFalse(self.workspace.path(access_path).exists())
        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertEqual(manifest["durable_state"], "ready")
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )

    def test_natural_command_provider_draft_and_transcript_errors_are_sanitized(
        self,
    ) -> None:
        """Cover stable real-process error categories and recovery without leaks."""

        selected = self._seed_default_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")

        for command, code in (
            ("/rn", "command_invalid"),
            ("/to unknown_agent", "agent_invalid"),
            ("/draft-file missing-draft.md", "draft_input_invalid"),
            ("/transcript missing_conversation", "conversation_not_found"),
        ):
            with self.subTest(code=code):
                mark = process.output_mark()
                process.send_line(command)
                process.wait_for_text(f"[error {code}]", after=mark)
                process.wait_for_text("[hint] ", after=mark)
                process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertFalse(self.provider_log.exists())

        diagnostic_secret = "PRIVATE_PROVIDER_STATE_MUST_NOT_APPEAR"
        hidden_path = str(self.workspace.root / "private" / "provider-state.json")
        self._set_provider_control(
            mode="nonzero",
            diagnostic=(
                f"{diagnostic_secret} {hidden_path} "
                "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR \x1b]8;;unsafe\x07"
            ),
        )
        mark = process.output_mark()
        process.send_line("provider failure turn")
        process.wait_for_text(
            "[error provider_protocol_error]",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        provider_error = process.text_after(mark)
        for secret in (
            diagnostic_secret,
            hidden_path,
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
        ):
            self.assertNotIn(secret, provider_error)
        self.assertNotIn(b"\x1b]", process.raw_bytes)
        self.assertNotIn(b"\x07", process.raw_bytes)

        catalog = FilesystemConversationCatalog(self.workspace)
        store = FilesystemConversationEventStore(self.workspace, catalog=catalog)
        failed_events = store.read("alpha", selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in failed_events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
            ),
        )
        for secret in (diagnostic_secret, hidden_path):
            self.assertNotIn(secret, failed_events[-1].content)
        self.assertEqual(
            failed_events[-1].content,
            "BackendProtocolError: Codex CLI exited with status 7",
        )
        self.assertLessEqual(len(failed_events[-1].content), 4096)

        mark = process.output_mark()
        process.send_line("/history 2")
        provider_history = process.wait_for_text(
            "Codex CLI exited with status 7",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        for secret in (
            diagnostic_secret,
            hidden_path,
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
        ):
            self.assertNotIn(secret, provider_history)

        self._set_provider_control(mode="auto")
        mark = process.output_mark()
        process.send_line("recovered provider turn")
        process.wait_for_text("lead [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertEqual(
            len(
                [
                    record
                    for record in self._provider_records()
                    if record.get("event") == "started"
                ]
            ),
            2,
        )

        malformed_value = "MALFORMED_PROVIDER_VALUE_MUST_NOT_APPEAR\x1b]unsafe\x07"
        self._set_provider_control(
            mode="malformed",
            response=malformed_value,
        )
        mark = process.output_mark()
        process.send_line("malformed provider turn")
        process.wait_for_text("[error provider_protocol_error]", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        malformed_error = process.text_after(mark)
        self.assertNotIn("MALFORMED_PROVIDER_VALUE_MUST_NOT_APPEAR", malformed_error)
        self.assertNotIn(b"\x1b]", process.raw_bytes)
        self.assertNotIn(b"\x07", process.raw_bytes)
        malformed_events = store.read("alpha", selected.conversation_id)
        self.assertEqual(
            malformed_events[-1].content,
            (
                "BackendProtocolError: Codex conversation retrieval response "
                "is malformed JSON"
            ),
        )
        self.assertNotIn(
            "MALFORMED_PROVIDER_VALUE_MUST_NOT_APPEAR",
            malformed_events[-1].content,
        )

        mark = process.output_mark()
        process.send_line("/history 2")
        process.wait_for_text("[context] [completed]", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        malformed_history = process.text_after(mark)
        self.assertIn("malformedJSON", "".join(malformed_history.split()))
        self.assertNotIn(
            "MALFORMED_PROVIDER_VALUE_MUST_NOT_APPEAR",
            malformed_history,
        )

        self._set_provider_control(mode="auto")
        mark = process.output_mark()
        process.send_line("recovered malformed provider turn")
        process.wait_for_text("lead [completed]> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)

        transcript_path = self.workspace.path(
            "discussion_logs/alpha/interactive/conversations/"
            f"{selected.conversation_id}/transcript.jsonl"
        )
        transcript_secret = "HIDDEN_TRANSCRIPT_STATE_MUST_NOT_APPEAR"
        transcript_path.write_text(
            json.dumps({"secret": transcript_secret}) + "\n",
            encoding="utf-8",
        )
        mark = process.output_mark()
        process.send_line("/history")
        process.wait_for_text("[error transcript_invalid]", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        transcript_error = process.text_after(mark)
        self.assertNotIn(transcript_secret, transcript_error)
        self.assertNotIn(str(transcript_path), transcript_error)
        error_lines = [
            line
            for line in process.normalized_text.splitlines()
            if line.lstrip().startswith(("[error ", "[hint] "))
        ]
        self.assertTrue(error_lines)
        self.assertTrue(all(len(line) <= 80 for line in error_lines))

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_default_conversation(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.access.json").exists()
        )

    def test_each_one_stream_non_tty_entry_rejects_without_any_mutation(
        self,
    ) -> None:
        """Prove both split-stream guards precede prompts, files, and provider work."""

        cases = (
            (False, True, ()),
            (
                True,
                False,
                (
                    "chat",
                    "target",
                    "--workspace",
                    str(self.workspace.root),
                    "--codex-timeout",
                    "not-a-number",
                ),
            ),
        )
        for stdin_is_terminal, stdout_is_terminal, arguments in cases:
            with self.subTest(
                stdin_is_terminal=stdin_is_terminal,
                stdout_is_terminal=stdout_is_terminal,
            ):
                before = _durable_tree(self.workspace.root)
                process = self._spawn_cli(
                    arguments,
                    stdin_is_terminal=stdin_is_terminal,
                    stdout_is_terminal=stdout_is_terminal,
                )
                self.assertEqual(process.wait_for_exit(), 2, process.normalized_text)
                self.assertIn("[error tty_required]", process.normalized_text)
                self.assertNotIn("story[", process.normalized_text)
                self.assertNotIn("Story chat", process.normalized_text)
                self.assertEqual(_durable_tree(self.workspace.root), before)
                self.assertFalse(self.provider_log.exists())
                self.assertNotIn(
                    "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
                    process.normalized_text,
                )


if __name__ == "__main__":
    unittest.main()
