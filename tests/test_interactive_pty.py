"""Provider-free process verification for the interactive CLI over a POSIX PTY."""

from __future__ import annotations

import errno
import json
import os
import re
import select
import signal
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
    load_conversation_selection,
    save_conversation_selection,
    select_session,
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
_TEARDOWN_DEADLINE_SECONDS = 3.0


_FAKE_CODEX = r'''#!/usr/bin/env python3
"""Deterministic local Codex protocol stand-in used only by POSIX PTY tests."""

import json
import os
import signal
import sys
from pathlib import Path


LOG_PATH = Path(os.environ["STORY_PTY_CODEX_LOG"])
CONTROL_PATH = Path(os.environ["STORY_PTY_CODEX_CONTROL"])


def append_record(value):
    """Append one complete UTF-8 JSONL record with an atomic OS write."""

    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(
        LOG_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(descriptor, payload)
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
    output_path.write_text(json.dumps(response), encoding="utf-8")
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


def _durable_tree(root: Path) -> dict[str, bytes]:
    """Capture exact file bytes below one test-owned workspace."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _durable_tree_without_leases(root: Path) -> dict[str, bytes]:
    """Capture persistent workspace bytes while excluding transient lease files."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith(".lease.json")
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
    ) -> None:
        """Retain validated process and terminal resources for one bounded run."""

        self.process = process
        self.master_fd = master_fd
        self.vintr = vintr
        self.veof = veof
        self.stdout_is_terminal = stdout_is_terminal
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

    def send_interrupt(self) -> None:
        """Deliver the terminal's configured VINTR byte through the PTY."""

        os.write(self.master_fd, self.vintr)

    def send_eof(self) -> None:
        """Deliver the terminal's configured VEOF byte through the PTY."""

        os.write(self.master_fd, self.veof)

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
        readable, _, _ = select.select((self.master_fd,), (), (), remaining)
        if not readable:
            raise AssertionError(
                "CLI process exceeded its output deadline; "
                f"captured={self.normalized_text!r}"
            )
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
        """Read every complete deterministic fake-provider JSONL record."""

        if not self.provider_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.provider_log.read_text(encoding="utf-8").splitlines()
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

    def _seed_selected_session(
        self,
        *,
        session_id: str = "alpha",
        subject: str = "Harbor planning",
    ) -> ConversationMetadata:
        """Persist one active selected session and selected conversation lease-free."""

        backend = AgentBackend(
            self.workspace,
            executable=str(self.fake_executable),
        )
        try:
            backend.start_session(session_id)
        finally:
            backend.close()
        select_session(self.workspace, session_id)
        catalog = FilesystemConversationCatalog(self.workspace)
        selected = catalog.create(session_id, subject)
        save_conversation_selection(
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
    ) -> PtyCliProcess:
        """Spawn the package entry with independently configurable terminal streams."""

        assert pty is not None
        assert fcntl is not None
        assert termios is not None
        try:
            master_fd, slave_fd = pty.openpty()
            attributes = termios.tcgetattr(slave_fd)
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
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "story_writing_agents", *arguments],
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
        )
        self._processes.append(harness)
        return harness

    def test_bare_entry_creates_session_then_quit_releases_only_the_lease(
        self,
    ) -> None:
        """Prove lobby orientation, explicit creation, prompts, and safe quit."""

        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        self.assertIn("Story chat\n", process.normalized_text)
        self.assertIn(
            f"Workspace: {self.workspace.root}\n",
            process.normalized_text,
        )
        self.assertIn("Session: lobby\n", process.normalized_text)

        process.send_line("/session new alpha")
        process.wait_for_text("story[alpha:lead]> ")
        self.assertIn("Session: alpha (created)\n", process.normalized_text)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertIs(manifest["active"], True)
        self.assertFalse(
            self.workspace.path(
                f"{_SESSION_DIRECTORY}/alpha.lease.json"
            ).exists()
        )
        self.assertEqual(load_cli_context(self.workspace).selected_session, "alpha")
        self.assertFalse(self.provider_log.exists())
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", process.normalized_text)

    def test_conversation_management_routes_controls_and_main_interrupt(
        self,
    ) -> None:
        """Cover selected subjects, sticky routing, safe output, and main VINTR."""

        process = self._spawn_cli(())
        process.wait_for_text("story[lobby]> ")
        mark = process.output_mark()
        process.send_line("/session new alpha")
        process.wait_for_text("story[alpha:lead]> ", after=mark)

        mark = process.output_mark()
        process.send_line("/conversation new First Subject")
        created_output = process.wait_for_text("Subject: First Subject", after=mark)
        match = re.search(
            r"Created and selected ([A-Za-z0-9_-]+) \| Subject: First Subject",
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
        self.assertIn("selected", listed)

        mark = process.output_mark()
        process.send_line(f"/conversation use {first_handle}")
        process.wait_for_text("Selected ", after=mark)
        mark = process.output_mark()
        process.send_line("/conversation subject Renamed Subject")
        process.wait_for_text("Subject: Renamed Subject", after=mark)
        mark = process.output_mark()
        process.send_line("/conversation archive")
        process.wait_for_text("[y/N] ", after=mark)
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
        process.wait_for_text("plot> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)

        mark = process.output_mark()
        process.send_line("@character one-shot turn")
        routed = process.wait_for_text(
            "character> deterministic advisory reply",
            after=mark,
        )
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        self.assertIn("sticky target remains plot_architect", routed)

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
            load_conversation_selection(self.workspace, "alpha"),
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
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )
        history_names = {
            ".python_history",
            ".story_history",
            ".story-writing-agents-history",
        }
        self.assertFalse(
            any(path.name in history_names for path in self.temporary_root.rglob("*"))
        )

    def test_explicit_chat_switches_sessions_and_confirmed_end_is_distinct(
        self,
    ) -> None:
        """Prove explicit attachment, safe switching, and confirmed deactivation."""

        selected = self._seed_selected_session()
        noncanonical_option = self.workspace.root / ".." / self.workspace.root.name
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(noncanonical_option)),
            cwd=self.temporary_root,
        )
        process.wait_for_text("story[alpha:lead]> ")
        self.assertIn(
            f"Workspace: {self.workspace.root}\n",
            process.normalized_text,
        )
        self.assertIn("Session: alpha (attached)", process.normalized_text)
        self.assertIn(selected.subject, process.normalized_text)

        mark = process.output_mark()
        process.send_line("/session new beta")
        process.wait_for_text("Session: beta (created)", after=mark)
        process.wait_for_text("story[beta:lead]> ", after=mark)
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )

        mark = process.output_mark()
        process.send_line("/session resume alpha")
        process.wait_for_text("Session: alpha (attached)", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/beta.lease.json").exists()
        )

        mark = process.output_mark()
        process.send_line("/session end")
        process.wait_for_text("End session alpha? [y/N] ", after=mark)
        process.send_line("y")
        process.wait_for_text("[session] Ended alpha; selection retained.", after=mark)
        process.wait_for_text("story[lobby]> ", after=mark)
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)

        alpha = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        beta = self.workspace.read_json(f"{_SESSION_DIRECTORY}/beta.json")
        self.assertIs(alpha["active"], False)
        self.assertIs(beta["active"], True)
        self.assertEqual(load_cli_context(self.workspace).selected_session, "alpha")
        self.assertEqual(
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        for session_id in ("alpha", "beta"):
            self.assertFalse(
                self.workspace.path(
                    f"{_SESSION_DIRECTORY}/{session_id}.lease.json"
                ).exists()
            )
        self.assertFalse(self.provider_log.exists())

    def test_history_search_transcript_and_cited_retrieval_stay_scoped(
        self,
    ) -> None:
        """Exercise provider-free reads followed by trusted retrieval progress."""

        selected = self._seed_selected_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")

        mark = process.output_mark()
        process.send_line("harbor beacon beside the vessel")
        process.wait_for_text("lead> deterministic advisory reply", after=mark)
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
        process.wait_for_text("harbor beacon beside the vessel", after=mark)
        history = process.wait_for_text("deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertIn("[transcript ", history)
        self.assertIn("deterministic advisory reply", history)

        mark = process.output_mark()
        process.send_line("/search harbor beacon")
        search_output = process.wait_for_text("[search ", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertIn("Citation: conversation", search_output)
        self.assertIn(selected.subject, " ".join(search_output.split()))

        mark = process.output_mark()
        process.send_line(f"/transcript {selected.conversation_id} 2")
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
        process.wait_for_text("lead> retrieval-assisted answer", after=mark)
        process.wait_for_text("[context] Source 1:", after=mark)
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
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )

    def test_run_composer_send_cancel_interrupt_and_eof_have_exact_durability(
        self,
    ) -> None:
        """Cover multiline consensus plus local cancel, VINTR, and VEOF discards."""

        selected = self._seed_selected_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_line("/send")
        process.wait_for_text("[compose] Nothing to send.", after=mark)
        process.send_line("/say /send")
        process.wait_for_text("compose[run:3]> ", after=mark)
        process.send_line("/say /cancel")
        process.wait_for_text("compose[run:4]> ", after=mark)
        process.send_line("final literal line")
        process.wait_for_text("compose[run:5]> ", after=mark)
        process.send_line("/send")
        process.wait_for_text("[compose] Sending 4 lines", after=mark)
        successful_output = process.wait_for_text(
            "Consensus | revision:",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        collapsed = " ".join(successful_output.split())
        ordered_fragments = (
            "Started prompt consensus.",
            "Drafted revision r0.",
            "Audit started for r0 at round 0.",
            "Audit completed for r0 at round 0; conflicts: none.",
            "Artifacts committed for r0:",
            "Consensus reached on r0 after 0 revision rounds.",
            "Consensus | revision: r0",
        )
        offsets = [collapsed.index(fragment) for fragment in ordered_fragments]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn("[waiting]", successful_output)
        durable_after_success = _durable_tree_without_leases(self.workspace.root)

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("discarded by slash command")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_line("/cancel")
        process.wait_for_text("Discarded 1 line.", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertEqual(
            _durable_tree_without_leases(self.workspace.root),
            durable_after_success,
        )

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("discarded by interrupt")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_interrupt()
        process.wait_for_text("Discarded 1 line.", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertEqual(
            _durable_tree_without_leases(self.workspace.root),
            durable_after_success,
        )

        mark = process.output_mark()
        process.send_line("/run")
        process.wait_for_text("compose[run:1]> ", after=mark)
        process.send_line("discarded by eof")
        process.wait_for_text("compose[run:2]> ", after=mark)
        process.send_eof()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertIn("Discarded 1 line.", process.text_after(mark))
        self.assertEqual(
            _durable_tree_without_leases(self.workspace.root),
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
        self.assertEqual(
            len(
                [
                    record
                    for record in self._provider_records()
                    if record.get("event") == "started"
                ]
            ),
            5,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )

    def test_main_eof_exits_normally_without_ending_or_mutating_session(self) -> None:
        """Deliver configured VEOF at main input and prove detach-only cleanup."""

        selected = self._seed_selected_session()
        before = _durable_tree_without_leases(self.workspace.root)
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")
        process.send_eof()
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(_durable_tree_without_leases(self.workspace.root), before)
        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertIs(manifest["active"], True)
        self.assertEqual(
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )
        self.assertFalse(self.provider_log.exists())

    def test_advisory_vintr_terminates_provider_taints_then_restarts_thread(
        self,
    ) -> None:
        """Prove joined real-signal advisory cancellation and clean retry semantics."""

        selected = self._seed_selected_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")
        mark = process.output_mark()
        process.send_line("/to plot")
        process.wait_for_text("story[alpha:plot]> ", after=mark)

        mark = process.output_mark()
        process.send_line("seed advisory")
        process.wait_for_text("plot> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        manifest_path = f"{_SESSION_DIRECTORY}/alpha.json"
        manifest = self.workspace.read_json(manifest_path)
        thread_key = "codex:plot_architect"
        self.assertIs(manifest["threads"][thread_key]["tainted"], False)

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
            "affected provider thread may restart on the next message.",
            after=mark,
        )
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        self.assertIn("story session remains attached", process.text_after(mark))

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
        self.assertIs(manifest["threads"][thread_key]["tainted"], True)
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
        process.wait_for_text("plot> deterministic advisory reply", after=mark)
        process.wait_for_text("story[alpha:plot]> ", after=mark)
        manifest = self.workspace.read_json(manifest_path)
        self.assertIs(manifest["threads"][thread_key]["tainted"], False)
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
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )
        self.assertNotIn("PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR", cancelled_output)

    def test_consensus_vintr_persists_interruption_and_restarts_tainted_drafter(
        self,
    ) -> None:
        """Prove real-signal interruption, artifact safety, and drafter retry."""

        selected = self._seed_selected_session()
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")

        mark = process.output_mark()
        process.send_line("/run seed consensus")
        process.wait_for_text("Consensus | revision: r0", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        mark = process.output_mark()
        process.send_line("establish current lead thread")
        process.wait_for_text("lead> deterministic advisory reply", after=mark)
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
        process.wait_for_text('"alpha" remains active and attached.', after=mark)
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
            manifest["threads"]["codex:execution_specialist"]["tainted"],
            True,
        )

        self._set_provider_control(mode="auto")
        mark = process.output_mark()
        process.send_line("/run recovered consensus")
        process.wait_for_text("Consensus | revision: r0", after=mark)
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        manifest = self.workspace.read_json(manifest_path)
        self.assertIs(
            manifest["threads"]["codex:execution_specialist"]["tainted"],
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
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
        )

    def test_vintr_during_large_local_history_stops_sequential_rendering(
        self,
    ) -> None:
        """Use PTY backpressure to interrupt provider-free row rendering safely."""

        selected = self._seed_selected_session()
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
        before = _durable_tree_without_leases(self.workspace.root)
        process = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        process.wait_for_text("story[alpha:lead]> ")
        mark = process.output_mark()
        process.send_line("/history 24")
        process.wait_for_text("[transcript ", after=mark)
        process.send_interrupt()
        process.wait_for_text(
            "[cancelled] History cancelled; attached state is unchanged.",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        interrupted_output = process.text_after(mark)
        self.assertLess(interrupted_output.count("[transcript "), 24)
        self.assertNotIn("[result] Transcript:", interrupted_output)
        self.assertEqual(
            _durable_tree_without_leases(self.workspace.root),
            before,
        )
        self.assertFalse(self.provider_log.exists())
        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
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

    def test_second_real_process_reports_session_lease_without_exposing_token(
        self,
    ) -> None:
        """Prove one natural lease conflict preserves the owner and durable scope."""

        selected = self._seed_selected_session()
        owner = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        owner.wait_for_text("story[alpha:lead]> ")
        lease_path = f"{_SESSION_DIRECTORY}/alpha.lease.json"
        lease = self.workspace.read_json(lease_path)
        lease_token = lease["token"]
        before_contender = _durable_tree(self.workspace.root)

        contender = self._spawn_cli(
            ("chat", "alpha", "--workspace", str(self.workspace.root))
        )
        contender.wait_for_text("[error session_in_use]")
        contender.wait_for_text("story[lobby]> ")
        contender.send_line("/quit")
        self.assertEqual(contender.wait_for_exit(), 0, contender.normalized_text)
        self.assertEqual(_durable_tree(self.workspace.root), before_contender)
        self.assertNotIn(lease_token, contender.normalized_text)
        self.assertNotIn(
            "PTY_TEST_CREDENTIAL_MUST_NOT_APPEAR",
            contender.normalized_text,
        )
        self.assertFalse(self.provider_log.exists())

        owner.send_line("/quit")
        self.assertEqual(owner.wait_for_exit(), 0, owner.normalized_text)
        self.assertFalse(self.workspace.path(lease_path).exists())
        manifest = self.workspace.read_json(f"{_SESSION_DIRECTORY}/alpha.json")
        self.assertIs(manifest["active"], True)
        self.assertEqual(
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )

    def test_natural_command_provider_draft_and_transcript_errors_are_sanitized(
        self,
    ) -> None:
        """Cover stable real-process error categories and recovery without leaks."""

        selected = self._seed_selected_session()
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
        process.wait_for_text("lead> deterministic advisory reply", after=mark)
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
        malformed_history = process.wait_for_text(
            "malformed JSON",
            after=mark,
        )
        process.wait_for_text("story[alpha:lead]> ", after=mark)
        self.assertNotIn(
            "MALFORMED_PROVIDER_VALUE_MUST_NOT_APPEAR",
            malformed_history,
        )

        self._set_provider_control(mode="auto")
        mark = process.output_mark()
        process.send_line("recovered malformed provider turn")
        process.wait_for_text("lead> deterministic advisory reply", after=mark)
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
            if line.startswith(("[error ", "[hint] "))
        ]
        self.assertTrue(error_lines)
        self.assertTrue(all(len(line) <= 80 for line in error_lines))

        process.send_line("/quit")
        self.assertEqual(process.wait_for_exit(), 0, process.normalized_text)
        self.assertEqual(
            load_conversation_selection(self.workspace, "alpha"),
            selected.conversation_id,
        )
        self.assertFalse(
            self.workspace.path(f"{_SESSION_DIRECTORY}/alpha.lease.json").exists()
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
