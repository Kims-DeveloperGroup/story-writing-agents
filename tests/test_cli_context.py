"""Workspace defaults, lifecycle automation, and run-input tests."""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

from story_writing_agents import (
    AgentReply,
    BackendProtocolError,
    DebateStatus,
    Phase,
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    Workspace,
    initialize_workspace,
)
from story_writing_agents import cli as cli_module
from story_writing_agents.cli_context import (
    CliContext,
    load_cli_context,
    load_default_conversation,
    resolve_session,
    resolve_workspace,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.errors import (
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.run_inputs import read_draft_file

from tests.helpers import ScriptedBackend, phase_requests


CLI_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
CONVERSATION_DEFAULT_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_defaults"
)
V1_CONVERSATION_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_selections"
)
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"


def conversation_default_path(workspace: Workspace, session_id: str) -> Path:
    """Return one safe test path for a V2 conversation default."""

    return workspace.root / CONVERSATION_DEFAULT_DIRECTORY / f"{session_id}.json"


def tree_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture file bytes, modes, and timestamps for purity assertions."""

    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def invoke_cli(arguments: list[str]) -> tuple[int, str, str]:
    """Invoke the CLI in process and capture its public streams."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli_module.main(arguments)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@contextlib.contextmanager
def working_directory(path: Path) -> Iterator[None]:
    """Temporarily switch the caller directory and always restore it."""

    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def successful_backend() -> ScriptedBackend:
    """Return a provider-free backend that reaches immediate consensus."""

    def respond(request):
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(request.revision_id, "neutral candidate")
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise AssertionError(f"unexpected phase: {request.phase}")

    return ScriptedBackend(respond)


class LifecycleCommandBackend:
    """Provide correlated lifecycle facts for CLI automation tests."""

    def __init__(self, *_args: Any, fail_at: str | None = None, **_kwargs: Any) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, str | None]] = []
        self._states: dict[str, SessionLifecycleState] = {}

    @staticmethod
    def state(
        session_id: str,
        *,
        durable: SessionDurableState,
        access: SessionAccessState,
    ) -> SessionLifecycleState:
        """Build one exact lifecycle command result."""

        held = access is SessionAccessState.HELD_HERE
        return SessionLifecycleState(
            session_id=session_id,
            durable_state=durable,
            access_state=access,
            owner_hostname="test-host" if held else None,
            owner_pid=123 if held else None,
            created_at=_CREATED,
            updated_at=_UPDATED,
        )

    def _record(self, method: str, session_id: str) -> None:
        self.calls.append((method, session_id))
        if self.fail_at == method:
            raise BackendProtocolError(f"{method} failed")

    def validate_provider_configuration(self, session_id: str) -> bool:
        self._record("validate", session_id)
        return True

    def create_session_with_access(self, session_id: str) -> SessionLifecycleState:
        self._record("create", session_id)
        state = self.state(session_id, durable=SessionDurableState.READY, access=SessionAccessState.HELD_HERE)
        self._states[session_id] = state
        return state

    def reopen_session_with_access(self, session_id: str) -> SessionLifecycleState:
        self._record("reopen", session_id)
        state = self.state(session_id, durable=SessionDurableState.READY, access=SessionAccessState.HELD_HERE)
        self._states[session_id] = state
        return state

    def pause_session_and_release_access(self, session_id: str) -> SessionLifecycleState:
        self._record("pause", session_id)
        state = self.state(session_id, durable=SessionDurableState.PAUSED, access=SessionAccessState.CLAIMABLE)
        self._states[session_id] = state
        return state

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        self._record("get", session_id)
        return self._states[session_id]

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        self._record("release", session_id)
        durable = self._states[session_id].durable_state
        state = self.state(session_id, durable=durable, access=SessionAccessState.CLAIMABLE)
        self._states[session_id] = state
        return state

    def close(self) -> None:
        self.calls.append(("close", None))


class WorkspaceDiscoveryTests(unittest.TestCase):
    """Verify explicit and nearest-ancestor workspace resolution."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_nearest_initialized_workspace_and_explicit_precedence(self) -> None:
        outer = initialize_workspace(self.base / "outer")
        inner = initialize_workspace(outer.root / "projects" / "inner")
        nested = inner.root / "one" / "two"
        nested.mkdir(parents=True)
        self.assertEqual(resolve_workspace(None, start=nested).root, inner.root)
        self.assertEqual(resolve_workspace(str(outer.root), start=nested).root, outer.root)

    def test_uninitialized_and_absent_discovery_are_actionable(self) -> None:
        uninitialized = self.base / "plain"
        uninitialized.mkdir()
        with self.assertRaisesRegex(ConfigurationError, "workspace is not initialized"):
            resolve_workspace(str(uninitialized), start=uninitialized)
        with self.assertRaisesRegex(ConfigurationError, "no initialized story workspace"):
            resolve_workspace(None, start=uninitialized)


class CliDefaultPersistenceTests(unittest.TestCase):
    """Verify strict V2 default schemas and clean-break rejection."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = initialize_workspace(Path(temporary.name) / "workspace")

    def test_default_session_missing_save_load_and_resolve_are_exact(self) -> None:
        self.assertEqual(tuple(inspect.signature(CliContext).parameters), ("default_session_id",))
        self.assertEqual(tuple(inspect.signature(save_default_session).parameters), ("workspace", "session_id"))
        self.assertEqual(
            tuple(inspect.signature(resolve_session).parameters),
            ("workspace", "context", "option_session"),
        )
        for public_callable in (
            load_cli_context,
            save_default_session,
            load_default_conversation,
            save_default_conversation,
            resolve_session,
        ):
            with self.subTest(public_callable=public_callable.__name__):
                self.assertTrue(inspect.getdoc(public_callable))
        before = tree_snapshot(self.workspace.root)
        self.assertEqual(load_cli_context(self.workspace), CliContext())
        self.assertEqual(tree_snapshot(self.workspace.root), before)
        saved = save_default_session(self.workspace, "story_session")
        self.assertEqual(saved, CliContext(default_session_id="story_session"))
        self.assertEqual(
            self.workspace.read_json(CLI_CONTEXT_PATH),
            {"schema_version": 2, "default_session_id": "story_session"},
        )
        context = load_cli_context(self.workspace)
        self.assertEqual(
            resolve_session(workspace=self.workspace, context=context, option_session=None),
            "story_session",
        )
        self.assertEqual(
            resolve_session(workspace=self.workspace, context=context, option_session="explicit"),
            "explicit",
        )
        self.assertEqual(load_cli_context(self.workspace), context)
        with self.assertRaisesRegex(ConfigurationError, "no default session"):
            resolve_session(workspace=self.workspace, context=CliContext(), option_session=None)

    def test_read_time_disappearance_is_absent_but_other_io_propagates(self) -> None:
        save_default_conversation(self.workspace, "chapter", "conversation")
        target = conversation_default_path(self.workspace, "chapter")
        original_read_text = self.workspace.read_text

        def disappear_then_read(relative: str | os.PathLike[str]) -> str:
            """Remove the target immediately before the guarded read."""

            target.unlink()
            return original_read_text(relative)

        with mock.patch.object(
            self.workspace,
            "read_text",
            side_effect=disappear_then_read,
        ) as read_text:
            self.assertIsNone(load_default_conversation(self.workspace, "chapter"))
        read_text.assert_called_once_with(
            f"{CONVERSATION_DEFAULT_DIRECTORY}/chapter.json"
        )
        self.assertFalse(target.exists())

        save_default_conversation(self.workspace, "chapter", "conversation")
        before = (target.read_bytes(), target.stat().st_mtime_ns)

        def deny_read(_relative: str | os.PathLike[str]) -> str:
            """Expose a wrapped non-absence I/O failure to the loader."""

            try:
                raise PermissionError("read-secret")
            except PermissionError as exc:
                raise WorkspaceFormatError("cannot read default") from exc

        with mock.patch.object(
            self.workspace,
            "read_text",
            side_effect=deny_read,
        ):
            with self.assertRaises(WorkspaceFormatError) as raised:
                load_default_conversation(self.workspace, "chapter")
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_default_session_schema_is_strict_and_v1_is_unchanged(self) -> None:
        valid = {"schema_version": 2, "default_session_id": "story_session"}
        rows = (
            ([], WorkspaceFormatError),
            ({"schema_version": 2}, WorkspaceFormatError),
            ({**valid, "extra": True}, WorkspaceFormatError),
            ({**valid, "schema_version": True}, WorkspaceFormatError),
            ({**valid, "schema_version": 1}, WorkspaceVersionUnsupportedError),
            ({**valid, "default_session_id": None}, WorkspaceFormatError),
            ({**valid, "default_session_id": "../escape"}, WorkspaceFormatError),
        )
        target = self.workspace.path(CLI_CONTEXT_PATH)
        for index, (value, exception) in enumerate(rows):
            with self.subTest(index=index):
                self.workspace.write_json(CLI_CONTEXT_PATH, value)
                before = (target.read_bytes(), target.stat().st_mtime_ns)
                with self.assertRaises(exception):
                    load_cli_context(self.workspace)
                self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.workspace.write_text(
            CLI_CONTEXT_PATH,
            '{"schema_version":2,"schema_version":2,"default_session_id":"story"}',
        )
        with self.assertRaises(WorkspaceFormatError):
            load_cli_context(self.workspace)

    def test_conversation_defaults_are_independent_exact_v2_records(self) -> None:
        self.assertEqual(tuple(inspect.signature(load_default_conversation).parameters), ("workspace", "session_id"))
        self.assertEqual(
            tuple(inspect.signature(save_default_conversation).parameters),
            ("workspace", "session_id", "default_conversation_id"),
        )
        before = tree_snapshot(self.workspace.root)
        self.assertIsNone(load_default_conversation(self.workspace, "chapter_one"))
        self.assertEqual(tree_snapshot(self.workspace.root), before)
        save_default_session(self.workspace, "story_session")
        context_before = self.workspace.path(CLI_CONTEXT_PATH).read_bytes()
        values = {"chapter_one": "conversation_one", "chapter_two": "conversation_two"}
        for session, conversation in values.items():
            save_default_conversation(self.workspace, session, conversation)
            self.assertEqual(load_default_conversation(self.workspace, session), conversation)
            self.assertEqual(
                self.workspace.read_json(f"{CONVERSATION_DEFAULT_DIRECTORY}/{session}.json"),
                {
                    "schema_version": 2,
                    "session_id": session,
                    "default_conversation_id": conversation,
                },
            )
        save_default_conversation(self.workspace, "chapter_one", None)
        self.assertIsNone(load_default_conversation(self.workspace, "chapter_one"))
        self.assertEqual(load_default_conversation(self.workspace, "chapter_two"), "conversation_two")
        self.assertEqual(self.workspace.path(CLI_CONTEXT_PATH).read_bytes(), context_before)

    def test_conversation_default_schema_and_v1_artifact_fail_unchanged(self) -> None:
        valid = {
            "schema_version": 2,
            "session_id": "chapter_one",
            "default_conversation_id": "conversation_one",
        }
        target = conversation_default_path(self.workspace, "chapter_one")
        rows = (
            ([], WorkspaceFormatError),
            ({**valid, "schema_version": 1}, WorkspaceVersionUnsupportedError),
            ({**valid, "schema_version": True}, WorkspaceFormatError),
            ({**valid, "session_id": "chapter_two"}, WorkspaceFormatError),
            ({**valid, "default_conversation_id": 7}, WorkspaceFormatError),
            ({**valid, "extra": True}, WorkspaceFormatError),
        )
        for index, (value, exception) in enumerate(rows):
            self.workspace.write_json(
                f"{CONVERSATION_DEFAULT_DIRECTORY}/chapter_one.json",
                value,
            )
            before = (target.read_bytes(), target.stat().st_mtime_ns)
            with self.subTest(index=index), self.assertRaises(exception):
                load_default_conversation(self.workspace, "chapter_one")
            self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

        target.unlink()
        v1 = self.workspace.write_json(
            f"{V1_CONVERSATION_DIRECTORY}/chapter_one.json",
            {"schema_version": 1},
        )
        before = (v1.read_bytes(), v1.stat().st_mtime_ns)
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            load_default_conversation(self.workspace, "chapter_one")
        with self.assertRaises(WorkspaceVersionUnsupportedError):
            save_default_conversation(self.workspace, "chapter_one", "conversation")
        self.assertEqual((v1.read_bytes(), v1.stat().st_mtime_ns), before)

    def test_malformed_and_non_utf8_defaults_reject_without_rewriting(self) -> None:
        targets = (
            (self.workspace.path(CLI_CONTEXT_PATH), load_cli_context),
            (
                conversation_default_path(self.workspace, "chapter"),
                lambda _workspace: load_default_conversation(
                    self.workspace,
                    "chapter",
                ),
            ),
        )
        for target, loader in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            for label, content in (
                ("malformed", b"{not-json}\n"),
                ("non_utf8", b"\xff\n"),
            ):
                with self.subTest(target=target.name, content=label):
                    target.write_bytes(content)
                    before = (target.read_bytes(), target.stat().st_mtime_ns)
                    with self.assertRaises(WorkspaceFormatError):
                        loader(self.workspace)
                    self.assertEqual(
                        (target.read_bytes(), target.stat().st_mtime_ns),
                        before,
                    )

    def test_identifier_symlink_and_atomic_write_failures_are_safe(self) -> None:
        for operation in (
            lambda: save_default_session(self.workspace, "../session"),
            lambda: load_default_conversation(self.workspace, "../session"),
            lambda: save_default_conversation(self.workspace, "session", "../conversation"),
        ):
            with self.assertRaises(SessionIdError):
                operation()

        save_default_conversation(self.workspace, "chapter", "first")
        target = conversation_default_path(self.workspace, "chapter")
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        with mock.patch("story_writing_agents.workspace.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_default_conversation(self.workspace, "chapter", "second")
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

        second = initialize_workspace(self.workspace.root.parent / "second")
        external = self.workspace.root.parent / "external"
        external.mkdir()
        directory = second.root / CONVERSATION_DEFAULT_DIRECTORY
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        try:
            directory.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        with self.assertRaises(WorkspaceSecurityError):
            save_default_conversation(second, "chapter", "conversation")

    def test_symlinked_default_directory_and_file_are_rejected_safely(self) -> None:
        external = self.workspace.root.parent / "external-defaults"
        external.mkdir()
        directory = self.workspace.root / CONVERSATION_DEFAULT_DIRECTORY
        if directory.exists():
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
        try:
            directory.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        for operation in (
            lambda: load_default_conversation(self.workspace, "chapter"),
            lambda: save_default_conversation(
                self.workspace,
                "chapter",
                "conversation",
            ),
        ):
            with self.subTest(kind="directory"), self.assertRaises(
                WorkspaceSecurityError
            ):
                operation()

        second = initialize_workspace(self.workspace.root.parent / "symlink-file")
        target = conversation_default_path(second, "chapter")
        target.parent.mkdir(parents=True, exist_ok=True)
        external_file = self.workspace.root.parent / "outside-default.json"
        external_file.write_text("{}\n", encoding="utf-8")
        target.symlink_to(external_file)
        for operation in (
            lambda: load_default_conversation(second, "chapter"),
            lambda: save_default_conversation(second, "chapter", "conversation"),
        ):
            with self.subTest(kind="file"), self.assertRaises(
                WorkspaceSecurityError
            ):
                operation()
        self.assertEqual(external_file.read_text(encoding="utf-8"), "{}\n")


class LifecycleAutomationTests(unittest.TestCase):
    """Verify exact create/reopen/pause grammar, output, and defaults."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = initialize_workspace(Path(temporary.name) / "workspace")

    def invoke_with(self, backend: LifecycleCommandBackend, *arguments: str) -> tuple[int, str, str]:
        """Invoke one lifecycle command with a deterministic backend."""

        with mock.patch.object(cli_module, "AgentBackend", return_value=backend):
            return invoke_cli(["session", *arguments, "--workspace", str(self.workspace.root)])

    def test_create_requires_target_saves_default_after_success_and_formats_json(self) -> None:
        backend = LifecycleCommandBackend()
        code, stdout, stderr = self.invoke_with(backend, "create", "created")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            stdout,
            '{\n  "access_state": "claimable",\n  "durable_state": "ready",\n  "session_id": "created"\n}\n',
        )
        self.assertEqual(
            backend.calls,
            [("validate", "created"), ("create", "created"), ("get", "created"), ("release", "created"), ("close", None)],
        )
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "created")

    def test_reopen_explicit_and_default_save_only_after_success(self) -> None:
        save_default_session(self.workspace, "original")
        backend = LifecycleCommandBackend()
        code, stdout, stderr = self.invoke_with(backend, "reopen", "replacement")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), {"access_state": "claimable", "durable_state": "ready", "session_id": "replacement"})
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "replacement")

        backend = LifecycleCommandBackend()
        code, stdout, stderr = self.invoke_with(backend, "reopen")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["session_id"], "replacement")
        self.assertEqual(backend.calls[0:2], [("validate", "replacement"), ("reopen", "replacement")])

        save_default_session(self.workspace, "retained")
        failing = LifecycleCommandBackend(fail_at="reopen")
        code, stdout, stderr = self.invoke_with(failing, "reopen", "failed")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("BackendProtocolError", stderr)
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "retained")

    def test_pause_uses_optional_default_and_always_retains_it(self) -> None:
        save_default_session(self.workspace, "default_session")
        backend = LifecycleCommandBackend()
        code, stdout, stderr = self.invoke_with(backend, "pause", "other_session")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            stdout,
            '{\n  "access_state": "claimable",\n  "durable_state": "paused",\n  "session_id": "other_session"\n}\n',
        )
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "default_session")
        self.assertEqual(backend.calls, [("pause", "other_session"), ("close", None)])

        backend = LifecycleCommandBackend()
        code, stdout, stderr = self.invoke_with(backend, "pause")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["session_id"], "default_session")
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "default_session")

    def test_lifecycle_grammar_has_no_session_option_or_legacy_actions(self) -> None:
        invalid = (
            ["session", "create", "--workspace", str(self.workspace.root)],
            ["session", "reopen", "one", "two", "--workspace", str(self.workspace.root)],
            ["session", "pause", "one", "two", "--workspace", str(self.workspace.root)],
            ["session", "create", "one", "--session", "two", "--workspace", str(self.workspace.root)],
            ["session", "start", "one", "--workspace", str(self.workspace.root)],
        )
        for arguments in invalid:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                self.subTest(arguments=arguments),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as caught,
            ):
                cli_module.main(arguments)
            self.assertEqual(caught.exception.code, 2)

        code, stdout, stderr = invoke_cli(["session", "reopen", "--workspace", str(self.workspace.root)])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("no default session", stderr)


class RunInputTests(unittest.TestCase):
    """Verify shared input selection and default-session run routing."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.workspace = initialize_workspace(self.base / "workspace")

    def run_with(self, arguments: list[str], backend: ScriptedBackend) -> tuple[int, str, str]:
        """Invoke a run command with one provider-free backend."""

        with mock.patch.object(cli_module, "AgentBackend", return_value=backend):
            return invoke_cli(arguments)

    def test_prompt_draft_and_draft_file_are_exact(self) -> None:
        draft_path = self.base / "draft.md"
        exact = "\ufefffirst\r\nsecond\x00  \n"
        draft_path.write_bytes(exact.encode("utf-8"))
        rows = (
            (["positional prompt"], "prompt", "positional prompt"),
            (["--prompt", "option prompt"], "prompt", "option prompt"),
            (["--draft", "option draft"], "draft", "option draft"),
            (["--draft-file", str(draft_path)], "draft", exact),
        )
        for index, (input_args, kind, payload) in enumerate(rows):
            backend = successful_backend()
            code, stdout, stderr = self.run_with(
                [
                    "run",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    f"session_{index}",
                    *input_args,
                    "--debate-id",
                    f"debate_{index}",
                    "--output",
                    f"part-01/result-{index}.md",
                ],
                backend,
            )
            self.assertEqual((code, stderr), (0, ""))
            report = json.loads(stdout)
            self.assertEqual((report["status"], report["input_kind"]), (DebateStatus.CONSENSUS.value, kind))
            requests = phase_requests(backend, Phase.DRAFT if kind == "prompt" else Phase.AUDIT)
            self.assertTrue(requests)
            self.assertTrue(all(request.user_payload == payload for request in requests))

    def test_shared_draft_loader_preserves_full_path_semantics(self) -> None:
        caller = self.base / "caller"
        caller.mkdir()
        exact = "\ufefffirst\r\nsecond\x00  \n"
        relative = caller / "neutral.md"
        relative.write_bytes(exact.encode("utf-8"))
        absolute = caller / "absolute.md"
        absolute.write_text("absolute draft", encoding="utf-8")
        metachar = caller / 'literal ;$(touch marker) [*] "quoted".md'
        metachar.write_text("literal metacharacters", encoding="utf-8")
        empty = caller / "empty.md"
        empty.write_bytes(b"")
        linked = caller / "linked.md"
        try:
            linked.symlink_to(absolute)
        except (OSError, NotImplementedError):
            linked = None

        with working_directory(caller):
            self.assertEqual(read_draft_file("neutral.md"), exact)
            self.assertEqual(read_draft_file(absolute), "absolute draft")
            self.assertEqual(
                read_draft_file(metachar.name),
                "literal metacharacters",
            )
            self.assertEqual(read_draft_file(empty), "")
            if linked is not None:
                self.assertEqual(read_draft_file(linked.name), "absolute draft")
        self.assertFalse((caller / "marker").exists())

        backend = successful_backend()
        with (
            working_directory(caller),
            mock.patch.object(
                cli_module,
                "read_draft_file",
                wraps=read_draft_file,
            ) as shared_loader,
        ):
            code, stdout, stderr = self.run_with(
                [
                    "run",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    "relative_file",
                    "--draft-file",
                    "neutral.md",
                    "--debate-id",
                    "relative_file",
                    "--output",
                    "part-01/relative-file.md",
                ],
                backend,
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["input_kind"], "draft")
        shared_loader.assert_called_once_with("neutral.md")

    def test_draft_loader_errors_precede_backend_and_input_is_exclusive(self) -> None:
        invalid = self.base / "invalid.md"
        invalid.write_bytes(b"\xff")
        with self.assertRaisesRegex(ConfigurationError, "not valid UTF-8"):
            read_draft_file(invalid)
        with mock.patch.object(cli_module, "AgentBackend") as backend_class:
            code, stdout, stderr = invoke_cli(
                [
                    "run",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    "invalid_file",
                    "--draft-file",
                    str(invalid),
                    "--output",
                    "part-01/invalid.md",
                ]
            )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("not valid UTF-8", stderr)
        backend_class.assert_not_called()

        for suffix in (
            ["run", "--output", "part-01/missing-input.md"],
            [
                "run",
                "positional",
                "--prompt",
                "option",
                "--output",
                "part-01/exclusive.md",
            ],
        ):
            with mock.patch.object(cli_module, "AgentBackend") as backend_class:
                code, stdout, stderr = invoke_cli(
                    [*suffix, "--workspace", str(self.workspace.root), "--session", "invalid"]
                )
            self.assertEqual((code, stdout), (2, ""))
            self.assertIn("exactly one run input", stderr)
            backend_class.assert_not_called()

    def test_nested_cwd_uses_saved_default_without_changing_it(self) -> None:
        save_default_session(self.workspace, "default_session")
        nested = self.workspace.root / "caller" / "nested"
        nested.mkdir(parents=True)
        backend = successful_backend()
        with working_directory(nested), mock.patch.object(cli_module, "AgentBackend", return_value=backend):
            code, stdout, stderr = invoke_cli(
                ["run", "inferred prompt", "--debate-id", "inferred", "--output", "part-01/inferred.md"]
            )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["session_id"], "default_session")
        self.assertEqual(load_cli_context(self.workspace).default_session_id, "default_session")

    def test_explicit_non_lifecycle_session_never_changes_default_or_falls_back(
        self,
    ) -> None:
        save_default_session(self.workspace, "default_session")
        for arguments in (
            [
                "directive",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "other_session",
                "[SYS: LANG=EN]",
            ],
            [
                "inspect",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "other_session",
            ],
        ):
            with self.subTest(command=arguments[0]):
                code, _stdout, stderr = invoke_cli(arguments)
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual(
                    load_cli_context(self.workspace).default_session_id,
                    "default_session",
                )

        backend = successful_backend()
        code, stdout, stderr = self.run_with(
            [
                "run",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "other_session",
                "explicit prompt",
                "--debate-id",
                "other_debate",
                "--output",
                "part-01/other.md",
            ],
            backend,
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["session_id"], "other_session")
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id,
            "default_session",
        )

        code, stdout, stderr = invoke_cli(
            [
                "inspect",
                "--workspace",
                str(self.workspace.root),
                "--session",
                "",
            ]
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("SessionIdError", stderr)
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id,
            "default_session",
        )

    def test_nested_cwd_infers_default_for_directive_and_inspect(self) -> None:
        save_default_session(self.workspace, "default_session")
        nested = self.workspace.root / "caller" / "commands"
        nested.mkdir(parents=True)
        with working_directory(nested):
            code, stdout, stderr = invoke_cli(["directive", "[SYS: LANG=KO]"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["language"], "KO")

            code, stdout, stderr = invoke_cli(["inspect"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(json.loads(stdout)["session_id"], "default_session")
        self.assertEqual(
            load_cli_context(self.workspace).default_session_id,
            "default_session",
        )


if __name__ == "__main__":
    unittest.main()
