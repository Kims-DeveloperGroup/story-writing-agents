"""Session-lobby controller contracts, transitions, and cleanup tests."""

from __future__ import annotations

import ast
import inspect
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import story_writing_agents
from story_writing_agents import interactive_controller as controller_module
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    CliContext,
    load_cli_context,
    select_session,
)
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    SessionExistsError,
    SessionIdError,
    SessionInUseError,
    SessionNotFoundError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents.models import SessionLeaseState, SessionSummary
from story_writing_agents.workspace import Workspace

from tests.helpers import initialized_workspace


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"


class ScriptedTerminalCallbacks:
    """Record terminal traffic and supply scripted text, events, or callbacks."""

    def __init__(self, *reads: object) -> None:
        self.reads = list(reads)
        self.prompts: list[str] = []
        self.records: list[str] = []

    def read_line(self, prompt: str) -> str:
        """Return the next scripted line after recording its exact prompt."""

        self.prompts.append(prompt)
        if not self.reads:
            raise AssertionError(f"unexpected input request: {prompt}")
        value = self.reads.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(prompt)
        return value  # type: ignore[return-value]

    def write_text(self, text: str) -> None:
        """Append one terminal-owned output record without transformation."""

        self.records.append(text)


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture file bytes, mode, and timestamp to detect silent rewrites."""

    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _manifest_relative(session_id: str) -> str:
    """Return the backend-owned manifest path for one safe test session."""

    return f"{_SESSION_DIRECTORY}/{session_id}.json"


def _lease_relative(session_id: str) -> str:
    """Return the backend-owned lease path for one safe test session."""

    return f"{_SESSION_DIRECTORY}/{session_id}.lease.json"


def _error_record(code: InteractiveErrorCode) -> str:
    """Return the exact default terminal record for one typed error code."""

    spec = INTERACTIVE_ERROR_SPECS[code]
    return (
        f"[error {code.value}] {spec.summary}\n"
        f"[hint] {spec.hint}\n"
    )


def _summary(
    session_id: str,
    *,
    active: bool = True,
    selected: bool = False,
    lease_state: SessionLeaseState = SessionLeaseState.AVAILABLE,
    hostname: str | None = None,
    pid: int | None = None,
) -> SessionSummary:
    """Build one deterministic public summary for formatting/fault tests."""

    return SessionSummary(
        session_id,
        active,
        selected,
        lease_state,
        hostname,
        pid,
        _CREATED,
        _UPDATED,
    )


class ControllerTestCase(unittest.TestCase):
    """Provide real workspace/backend/controller construction helpers."""

    def make_workspace(self) -> Workspace:
        """Create one initialized workspace registered for test cleanup."""

        return initialized_workspace(self)

    def make_backend(self, workspace: Workspace) -> AgentBackend:
        """Create a provider-free lifecycle backend using Python as executable."""

        backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(backend.close)
        return backend

    def seed_session(
        self,
        workspace: Workspace,
        session_id: str,
        *,
        active: bool,
    ) -> None:
        """Persist one active or inactive session without retaining its lease."""

        backend = AgentBackend(workspace, executable=sys.executable)
        try:
            backend.start_session(session_id)
            if not active:
                backend.end_session(session_id)
        finally:
            backend.close()

    def make_controller(
        self,
        workspace: Workspace,
        *reads: object,
        context: CliContext | None = None,
        backend: AgentBackend | None = None,
    ) -> tuple[
        InteractiveController,
        AgentBackend,
        ScriptedTerminalCallbacks,
    ]:
        """Bind a controller to real lifecycle state and scripted terminal I/O."""

        selected_context = context or load_cli_context(workspace)
        selected_backend = backend or self.make_backend(workspace)
        callbacks = ScriptedTerminalCallbacks(*reads)
        terminal = InteractiveTerminal(
            callbacks.read_line,
            callbacks.write_text,
        )
        return (
            InteractiveController(
                workspace,
                selected_context,
                selected_backend,
                terminal,
            ),
            selected_backend,
            callbacks,
        )


class ControllerContractTests(ControllerTestCase):
    """Verify the module-supported surface and approved dependency boundary."""

    def test_surface_signatures_non_facade_and_single_use_cleanup(self) -> None:
        self.assertEqual(controller_module.__all__, ["InteractiveController"])
        self.assertNotIn("InteractiveController", story_writing_agents.__all__)
        self.assertFalse(hasattr(story_writing_agents, "InteractiveController"))

        constructor = inspect.signature(InteractiveController)
        self.assertEqual(
            tuple(constructor.parameters),
            ("workspace", "context", "backend", "terminal"),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                and parameter.default is inspect.Parameter.empty
                for parameter in constructor.parameters.values()
            )
        )
        self.assertEqual(
            get_type_hints(InteractiveController.__init__),
            {
                "workspace": Workspace,
                "context": CliContext,
                "backend": AgentBackend,
                "terminal": InteractiveTerminal,
                "return": type(None),
            },
        )
        run_signature = inspect.signature(
            InteractiveController.run_session_lobby
        )
        self.assertEqual(
            tuple(run_signature.parameters),
            ("self", "requested_session"),
        )
        requested = run_signature.parameters["requested_session"]
        self.assertIs(requested.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(requested.default)
        self.assertEqual(
            get_type_hints(InteractiveController.run_session_lobby),
            {"requested_session": str | None, "return": int},
        )

        workspace = self.make_workspace()
        backend = self.make_backend(workspace)
        callbacks = ScriptedTerminalCallbacks("/quit")
        terminal = InteractiveTerminal(
            callbacks.read_line,
            callbacks.write_text,
        )
        before = _tree_snapshot(workspace.root)
        controller = InteractiveController(
            workspace,
            CliContext(),
            backend,
            terminal,
        )
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(callbacks.prompts, [])
        self.assertEqual(callbacks.records, [])

        with mock.patch.object(backend, "close", wraps=backend.close) as close:
            self.assertEqual(controller.run_session_lobby(), 0)
            close.assert_called_once_with()
            with self.assertRaisesRegex(RuntimeError, "single-use"):
                controller.run_session_lobby()
            close.assert_called_once_with()

    def test_dependency_docstring_and_descriptive_name_boundary(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
            / "interactive_controller.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            dependencies,
            {
                "agents",
                "backend",
                "cli_context",
                "errors",
                "interactive_commands",
                "interactive_terminal",
                "models",
                "workspace",
            },
        )

        generic_names = {
            "confirm",
            "do",
            "execute",
            "handle",
            "helper",
            "process",
            "run",
        }
        callable_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                callable_nodes.append(node)
            elif isinstance(node, ast.ClassDef):
                callable_nodes.extend(
                    member
                    for member in node.body
                    if isinstance(
                        member,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                )
        for node in callable_nodes:
            with self.subTest(callable=node.name):
                self.assertTrue(ast.get_docstring(node))
                self.assertNotIn(node.name.strip("_"), generic_names)

    def test_direct_import_is_clean_and_package_graph_is_acyclic(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        source_path = str(repository_root / "src")
        environment["PYTHONPATH"] = source_path
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import story_writing_agents as facade; "
            "from story_writing_agents import interactive_controller as module; "
            "assert module.__all__ == ['InteractiveController']; "
            "assert 'InteractiveController' not in facade.__all__; "
            "assert not hasattr(facade, 'InteractiveController'); "
            "print('ok')"
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

        package = repository_root / "src" / "story_writing_agents"
        modules = {path.stem for path in package.glob("*.py")}
        graph: dict[str, set[str]] = {name: set() for name in modules}
        for path in package.glob("*.py"):
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(parsed):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.level == 1
                    and node.module in modules
                ):
                    graph[path.stem].add(node.module)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(module_name: str) -> None:
            """Reject one back-edge while traversing internal imports."""

            if module_name in visiting:
                self.fail(f"internal import cycle reaches {module_name}")
            if module_name in visited:
                return
            visiting.add(module_name)
            for dependency in graph[module_name]:
                visit(dependency)
            visiting.remove(module_name)
            visited.add(module_name)

        for module_name in graph:
            visit(module_name)
        self.assertEqual(len(modules), 19)


class ControllerStartupTests(ControllerTestCase):
    """Verify explicit, selected, confirmed, and failed startup paths."""

    def test_unselected_lobby_is_read_only(self) -> None:
        workspace = self.make_workspace()
        before = _tree_snapshot(workspace.root)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(callbacks.prompts, ["story[lobby]> "])
        self.assertEqual(len(callbacks.records), 1)
        self.assertIn("Session: lobby\n", callbacks.records[0])

    def test_explicit_sessions_create_attach_and_resume_without_confirmation(
        self,
    ) -> None:
        cases = (
            ("missing", None, "created"),
            ("active", True, "attached"),
            ("inactive", False, "resumed"),
        )
        for session_id, initial_active, attachment in cases:
            with self.subTest(session=session_id):
                workspace = self.make_workspace()
                if initial_active is not None:
                    self.seed_session(
                        workspace,
                        session_id,
                        active=initial_active,
                    )
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    "/quit",
                )
                self.assertEqual(
                    controller.run_session_lobby(
                        requested_session=session_id
                    ),
                    0,
                )
                self.assertEqual(
                    load_cli_context(workspace).selected_session,
                    session_id,
                )
                self.assertTrue(
                    workspace.read_json(_manifest_relative(session_id))["active"]
                )
                self.assertFalse(
                    workspace.path(_lease_relative(session_id)).exists()
                )
                self.assertFalse(
                    any("[Y/n]" in prompt for prompt in callbacks.prompts)
                )
                rendered = "".join(callbacks.records)
                self.assertIn(
                    f"Session: {session_id} ({attachment})\n",
                    rendered,
                )
                self.assertNotIn("History:", rendered)
                self.assertNotIn("Room messages", rendered)

    def test_saved_active_attaches_without_rewriting_selection(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "selected", active=True)
        select_session(workspace, "selected")
        context_path = workspace.path(_CONTEXT_PATH)
        context_before = (
            context_path.read_bytes(),
            context_path.stat().st_mtime_ns,
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            (context_path.read_bytes(), context_path.stat().st_mtime_ns),
            context_before,
        )
        self.assertIn("Session: selected (attached)\n", callbacks.records[0])
        self.assertEqual(callbacks.prompts, ["story[selected:lead]> "])

    def test_saved_inactive_confirmation_outcomes_are_distinct(self) -> None:
        cases = (
            ("default_yes", "", True, False, "Session: saved (resumed)"),
            ("no", "no", False, False, "Session resume cancelled."),
            (
                "interrupt",
                KeyboardInterrupt(),
                False,
                False,
                "Session resume cancelled.",
            ),
            ("eof", EOFError(), False, True, None),
        )
        for label, decision, becomes_active, exits_at_prompt, marker in cases:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(workspace, "saved", active=False)
                select_session(workspace, "saved")
                before = _tree_snapshot(workspace.root)
                reads = (decision,) if exits_at_prompt else (decision, "/quit")
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    *reads,
                )
                self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(
                    callbacks.prompts[0],
                    "Resume selected session saved? [Y/n] ",
                )
                self.assertEqual(
                    workspace.read_json(_manifest_relative("saved"))["active"],
                    becomes_active,
                )
                self.assertEqual(
                    load_cli_context(workspace).selected_session,
                    "saved",
                )
                if marker is not None:
                    self.assertIn(marker, "".join(callbacks.records))
                if not becomes_active:
                    self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertFalse(
                    workspace.path(_lease_relative("saved")).exists()
                )

    def test_startup_failures_use_typed_safe_mapping(self) -> None:
        cases: tuple[
            tuple[type[Exception], InteractiveErrorCode, bool], ...
        ] = (
            (SessionIdError, InteractiveErrorCode.SESSION_INVALID, True),
            (SessionExistsError, InteractiveErrorCode.SESSION_EXISTS, True),
            (SessionNotFoundError, InteractiveErrorCode.SESSION_NOT_FOUND, True),
            (SessionInUseError, InteractiveErrorCode.SESSION_IN_USE, True),
            (
                ConfigurationError,
                InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
                True,
            ),
            (
                BackendProtocolError,
                InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
                True,
            ),
            (
                WorkspaceSecurityError,
                InteractiveErrorCode.WORKSPACE_UNSAFE,
                False,
            ),
            (
                WorkspaceFormatError,
                InteractiveErrorCode.WORKSPACE_INVALID,
                False,
            ),
            (OSError, InteractiveErrorCode.WORKSPACE_INVALID, False),
            (RuntimeError, InteractiveErrorCode.INTERNAL_ERROR, False),
        )
        for failure_type, code, recoverable in cases:
            with self.subTest(failure=failure_type.__name__):
                workspace = self.make_workspace()
                backend = self.make_backend(workspace)
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    "/quit",
                    backend=backend,
                )
                failure = failure_type("secret-token-from-backend")
                with (
                    mock.patch.object(
                        backend,
                        "get_session_summary",
                        side_effect=failure,
                    ),
                    mock.patch.object(
                        backend,
                        "create_session",
                        side_effect=failure,
                    ),
                ):
                    result = controller.run_session_lobby(
                        requested_session="target"
                    )
                self.assertEqual(result, 0 if recoverable else 2)
                rendered = "".join(callbacks.records)
                self.assertIn(_error_record(code), rendered)
                self.assertNotIn("secret-token-from-backend", rendered)
                self.assertFalse(workspace.path(_CONTEXT_PATH).exists())
                if recoverable:
                    self.assertIn("Session: lobby\n", rendered)
                else:
                    self.assertNotIn("Session: lobby\n", rendered)

    def test_missing_saved_selection_and_inactive_attach_race_return_to_lobby(
        self,
    ) -> None:
        workspace = self.make_workspace()
        select_session(workspace, "missing")
        context_before = workspace.path(_CONTEXT_PATH).read_bytes()
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.SESSION_NOT_FOUND), rendered)
        self.assertIn("Session: lobby\n", rendered)
        self.assertEqual(workspace.path(_CONTEXT_PATH).read_bytes(), context_before)

        workspace = self.make_workspace()
        self.seed_session(workspace, "raced", active=True)
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/quit",
            backend=backend,
        )

        def deactivate_then_fail(session_id: str) -> SessionSummary:
            manifest = workspace.read_json(_manifest_relative(session_id))
            manifest["active"] = False
            workspace.write_json(_manifest_relative(session_id), manifest)
            raise BackendProtocolError("inactive-race-secret")

        with mock.patch.object(
            backend,
            "attach_session",
            side_effect=deactivate_then_fail,
        ):
            self.assertEqual(
                controller.run_session_lobby(requested_session="raced"),
                0,
            )
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.SESSION_INACTIVE), rendered)
        self.assertNotIn("inactive-race-secret", rendered)
        self.assertIn("Session: lobby\n", rendered)
        self.assertFalse(workspace.path(_CONTEXT_PATH).exists())

    def test_acquisition_rejects_mismatched_or_unowned_summaries(self) -> None:
        mutations: tuple[
            tuple[str, Callable[[SessionSummary], object]], ...
        ] = (
            ("wrong_id", lambda value: replace(value, session_id="other")),
            ("inactive", lambda value: replace(value, active=False)),
            (
                "available",
                lambda value: replace(
                    value,
                    lease_state=SessionLeaseState.AVAILABLE,
                ),
            ),
            ("wrong_type", lambda _value: object()),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(workspace, "target", active=True)
                backend = self.make_backend(workspace)
                original_attach = backend.attach_session

                def invalid_attach(session_id: str) -> object:
                    acquired = original_attach(session_id)
                    return mutate(acquired)

                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    backend=backend,
                )
                with mock.patch.object(
                    backend,
                    "attach_session",
                    side_effect=invalid_attach,
                ):
                    self.assertEqual(
                        controller.run_session_lobby(
                            requested_session="target"
                        ),
                        2,
                    )
                self.assertIn(
                    _error_record(InteractiveErrorCode.CLEANUP_FAILED),
                    callbacks.records,
                )
                self.assertFalse(workspace.path(_CONTEXT_PATH).exists())
                self.assertFalse(
                    workspace.path(_lease_relative("target")).exists()
                )


class ControllerCommandTests(ControllerTestCase):
    """Verify the intentionally limited lobby command dispatcher."""

    def test_help_parser_interrupt_and_unsupported_domains_are_nonmutating(
        self,
    ) -> None:
        workspace = self.make_workspace()
        before = _tree_snapshot(workspace.root)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            " ",
            KeyboardInterrupt(),
            "/? session",
            "/sesion list",
            "ordinary provider message",
            "/run create artifact",
            "/inspect last",
            "/to plot",
            "/session list",
            "/session end",
            "/exit",
        )
        with (
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(_tree_snapshot(workspace.root), before)

        rendered = "".join(callbacks.records)
        self.assertIn("[cancelled] No action was taken.\n", rendered)
        self.assertIn("Syntax: /session list | new NAME", rendered)
        self.assertIn("[error command_invalid] unknown command: /sesion\n", rendered)
        self.assertIn("[hint] Try /session, or type /help.\n", rendered)
        unavailable = (
            "[error command_invalid] This command is not available in the "
            "session lobby.\n"
        )
        self.assertEqual(rendered.count(unavailable), 4)
        self.assertIn("[session] No story sessions found.\n", rendered)
        self.assertIn(_error_record(InteractiveErrorCode.SESSION_INACTIVE), rendered)

    def test_session_list_preserves_order_and_sanitizes_lease_facts(self) -> None:
        workspace = self.make_workspace()
        backend = self.make_backend(workspace)
        summaries = (
            _summary("beta", active=False),
            _summary(
                "alpha",
                selected=True,
                lease_state=SessionLeaseState.IN_USE,
                hostname="remote\x1bhost",
                pid=321,
            ),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session list",
            "/quit",
            backend=backend,
        )
        before = _tree_snapshot(workspace.root)
        with mock.patch.object(
            backend,
            "list_sessions",
            return_value=summaries,
        ) as list_sessions:
            self.assertEqual(controller.run_session_lobby(), 0)
        list_sessions.assert_called_once_with(selected_session=None)
        self.assertEqual(_tree_snapshot(workspace.root), before)
        session_records = [
            record
            for record in callbacks.records
            if record.startswith("[session]")
        ]
        self.assertEqual(len(session_records), 2)
        beta, alpha = session_records
        for fact in ("beta", "inactive", "not selected", "available"):
            with self.subTest(session="beta", fact=fact):
                self.assertIn(fact, beta)
        for fact in (
            "alpha",
            "active",
            "selected",
            "in_use",
            "321",
            "remote\\x1bhost",
        ):
            with self.subTest(session="alpha", fact=fact):
                self.assertIn(fact, alpha)
        self.assertEqual(
            [record.split(":", 1)[0] for record in session_records],
            ["[session] beta", "[session] alpha"],
        )

    def test_resume_without_name_and_same_session_noop(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume",
            "/session new action_session",
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.SESSION_NOT_FOUND), rendered)
        self.assertIn("Session: action_session (created)\n", rendered)
        self.assertEqual(
            load_cli_context(workspace).selected_session,
            "action_session",
        )

        workspace = self.make_workspace()
        self.seed_session(workspace, "source", active=True)
        select_session(workspace, "source")
        context_path = workspace.path(_CONTEXT_PATH)
        context_before = (
            context_path.read_bytes(),
            context_path.stat().st_mtime_ns,
        )
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume",
            "/quit",
            backend=backend,
        )
        with (
            mock.patch.object(
                backend,
                "attach_session",
                wraps=backend.attach_session,
            ) as attach,
            mock.patch.object(
                backend,
                "detach_session",
                wraps=backend.detach_session,
            ) as detach,
            mock.patch.object(
                controller_module,
                "select_session",
                wraps=select_session,
            ) as persist,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        attach.assert_called_once_with("source")
        detach.assert_called_once_with("source")
        persist.assert_not_called()
        self.assertEqual(
            (context_path.read_bytes(), context_path.stat().st_mtime_ns),
            context_before,
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("Session: source (attached)\n"), 1)
        self.assertIn("[session] Already attached to source.\n", rendered)

    def test_declined_saved_session_can_be_resumed_by_omitted_command(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "saved", active=False)
        select_session(workspace, "saved")
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "n",
            "/session resume",
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertTrue(
            workspace.read_json(_manifest_relative("saved"))["active"]
        )
        rendered = "".join(callbacks.records)
        self.assertIn("[cancelled] Session resume cancelled.\n", rendered)
        self.assertIn("Session: saved (resumed)\n", rendered)
        self.assertEqual(
            callbacks.prompts.count("Resume selected session saved? [Y/n] "),
            1,
        )


class ControllerSwitchTests(ControllerTestCase):
    """Verify destination-first switching and bounded compensation."""

    def prepare_switch(
        self,
    ) -> tuple[
        Workspace,
        AgentBackend,
        InteractiveController,
        ScriptedTerminalCallbacks,
    ]:
        """Create selected active source and destination sessions."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "source", active=True)
        self.seed_session(workspace, "destination", active=True)
        select_session(workspace, "source")
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume destination",
            "/quit",
            backend=backend,
        )
        return workspace, backend, controller, callbacks

    def test_successful_switch_orders_acquire_select_detach_and_cleanup(self) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        events: list[str] = []
        original_get = backend.get_session_summary
        original_attach = backend.attach_session
        original_detach = backend.detach_session
        original_close = backend.close

        def record_get(session_id: str, *, selected_session: str | None = None):
            events.append(f"get:{session_id}")
            return original_get(session_id, selected_session=selected_session)

        def record_attach(session_id: str):
            events.append(f"attach:{session_id}")
            return original_attach(session_id)

        def record_detach(session_id: str):
            events.append(f"detach:{session_id}")
            return original_detach(session_id)

        def record_select(workspace_value: Workspace, session_id: str):
            events.append(f"select:{session_id}")
            return select_session(workspace_value, session_id)

        def record_close() -> None:
            events.append("close")
            original_close()

        with (
            mock.patch.object(backend, "get_session_summary", side_effect=record_get),
            mock.patch.object(backend, "attach_session", side_effect=record_attach),
            mock.patch.object(backend, "detach_session", side_effect=record_detach),
            mock.patch.object(backend, "close", side_effect=record_close),
            mock.patch.object(
                controller_module,
                "select_session",
                side_effect=record_select,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            events,
            [
                "get:source",
                "attach:source",
                "get:destination",
                "attach:destination",
                "select:destination",
                "detach:source",
                "detach:destination",
                "close",
            ],
        )
        self.assertEqual(
            load_cli_context(workspace).selected_session,
            "destination",
        )
        rendered = "".join(callbacks.records)
        self.assertLess(
            rendered.index("Session: source (attached)"),
            rendered.index("Session: destination (attached)"),
        )

    def test_destination_failure_preserves_source_selection_and_files(self) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        before = _tree_snapshot(workspace.root)
        original_attach = backend.attach_session
        original_detach = backend.detach_session
        events: list[str] = []

        def attach(session_id: str):
            events.append(f"attach:{session_id}")
            if session_id == "destination":
                raise SessionInUseError("secret-owner-token")
            return original_attach(session_id)

        def detach(session_id: str):
            events.append(f"detach:{session_id}")
            return original_detach(session_id)

        with (
            mock.patch.object(backend, "attach_session", side_effect=attach),
            mock.patch.object(backend, "detach_session", side_effect=detach),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            events,
            ["attach:source", "attach:destination", "detach:source"],
        )
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(load_cli_context(workspace).selected_session, "source")
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.SESSION_IN_USE), rendered)
        self.assertNotIn("secret-owner-token", rendered)
        self.assertEqual(rendered.count("Session: source (attached)\n"), 1)

    def test_selection_failure_releases_destination_before_source_cleanup(self) -> None:
        cases = (
            (SessionIdError("selection-secret"), 0),
            (OSError("selection-secret"), 2),
        )
        for failure, expected_code in cases:
            with self.subTest(failure=type(failure).__name__):
                workspace, backend, controller, callbacks = self.prepare_switch()
                original_detach = backend.detach_session
                events: list[str] = []

                def fail_destination_selection(
                    workspace_value: Workspace,
                    session_id: str,
                ) -> CliContext:
                    events.append(f"select:{session_id}")
                    raise failure

                def record_detach(session_id: str):
                    events.append(f"detach:{session_id}")
                    return original_detach(session_id)

                with (
                    mock.patch.object(
                        controller_module,
                        "select_session",
                        side_effect=fail_destination_selection,
                    ),
                    mock.patch.object(
                        backend,
                        "detach_session",
                        side_effect=record_detach,
                    ),
                ):
                    self.assertEqual(
                        controller.run_session_lobby(),
                        expected_code,
                    )
                self.assertEqual(
                    events,
                    [
                        "select:destination",
                        "detach:destination",
                        "detach:source",
                    ],
                )
                self.assertEqual(
                    load_cli_context(workspace).selected_session,
                    "source",
                )
                self.assertFalse(
                    workspace.path(_lease_relative("destination")).exists()
                )
                rendered = "".join(callbacks.records)
                expected_error = (
                    InteractiveErrorCode.SESSION_INVALID
                    if expected_code == 0
                    else InteractiveErrorCode.WORKSPACE_INVALID
                )
                self.assertIn(_error_record(expected_error), rendered)
                self.assertNotIn("selection-secret", rendered)

    def test_failed_destination_release_is_fatal_and_defers_to_close(self) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        original_detach = backend.detach_session
        events: list[str] = []

        def fail_selection(
            _workspace: Workspace,
            session_id: str,
        ) -> CliContext:
            events.append(f"select:{session_id}")
            raise SessionIdError("selection failed")

        def fail_destination_detach(session_id: str):
            events.append(f"detach:{session_id}")
            if session_id == "destination":
                raise OSError("destination cleanup secret")
            return original_detach(session_id)

        with (
            mock.patch.object(
                controller_module,
                "select_session",
                side_effect=fail_selection,
            ),
            mock.patch.object(
                backend,
                "detach_session",
                side_effect=fail_destination_detach,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        self.assertEqual(events, ["select:destination", "detach:destination"])
        self.assertEqual(load_cli_context(workspace).selected_session, "source")
        self.assertFalse(workspace.path(_lease_relative("source")).exists())
        self.assertFalse(workspace.path(_lease_relative("destination")).exists())
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count("[error cleanup_failed]"),
            1,
        )
        self.assertNotIn("destination cleanup secret", rendered)

    def test_source_detach_failure_after_selection_is_fatal_without_rollback(
        self,
    ) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        original_detach = backend.detach_session
        events: list[str] = []

        def record_select(workspace_value: Workspace, session_id: str):
            events.append(f"select:{session_id}")
            return select_session(workspace_value, session_id)

        def fail_source_detach(session_id: str):
            events.append(f"detach:{session_id}")
            if session_id == "source":
                raise OSError("source cleanup secret")
            return original_detach(session_id)

        with (
            mock.patch.object(
                controller_module,
                "select_session",
                side_effect=record_select,
            ),
            mock.patch.object(
                backend,
                "detach_session",
                side_effect=fail_source_detach,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        self.assertEqual(events, ["select:destination", "detach:source"])
        self.assertEqual(
            load_cli_context(workspace).selected_session,
            "destination",
        )
        self.assertFalse(workspace.path(_lease_relative("source")).exists())
        self.assertFalse(workspace.path(_lease_relative("destination")).exists())
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("source cleanup secret", rendered)
        self.assertNotIn("Session: destination (attached)", rendered)


class ControllerEndAndCleanupTests(ControllerTestCase):
    """Verify end confirmation, state verification, and exit precedence."""

    def prepared_attached_controller(
        self,
        *reads: object,
    ) -> tuple[
        Workspace,
        AgentBackend,
        InteractiveController,
        ScriptedTerminalCallbacks,
    ]:
        """Create a selected active session and controller with scripted input."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "session", active=True)
        select_session(workspace, "session")
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            *reads,
            backend=backend,
        )
        return workspace, backend, controller, callbacks

    def test_end_cancellation_and_eof_preserve_active_session(self) -> None:
        cases = (
            ("blank", "", False),
            ("no", "NO", False),
            ("interrupt", KeyboardInterrupt(), False),
            ("eof", EOFError(), True),
        )
        for label, decision, exits in cases:
            with self.subTest(case=label):
                reads = ("/session end", decision)
                if not exits:
                    reads += ("/quit",)
                workspace, backend, controller, callbacks = (
                    self.prepared_attached_controller(*reads)
                )
                with mock.patch.object(
                    backend,
                    "end_session",
                    wraps=backend.end_session,
                ) as end:
                    self.assertEqual(controller.run_session_lobby(), 0)
                end.assert_not_called()
                self.assertTrue(
                    workspace.read_json(_manifest_relative("session"))["active"]
                )
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )
                self.assertIn(
                    "End session session? [y/N] ",
                    callbacks.prompts,
                )
                rendered = "".join(callbacks.records)
                if exits:
                    self.assertNotIn("Session end cancelled.", rendered)
                else:
                    self.assertIn(
                        "[cancelled] Session end cancelled.\n",
                        rendered,
                    )

    def test_successful_end_retains_selection_and_returns_to_lobby(self) -> None:
        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller(
                "/session end",
                "yes",
                "/quit",
            )
        )
        with mock.patch.object(
            backend,
            "detach_session",
            wraps=backend.detach_session,
        ) as detach:
            self.assertEqual(controller.run_session_lobby(), 0)
        detach.assert_not_called()
        self.assertFalse(
            workspace.read_json(_manifest_relative("session"))["active"]
        )
        self.assertEqual(load_cli_context(workspace).selected_session, "session")
        rendered = "".join(callbacks.records)
        self.assertIn("[session] Ended session; selection retained.\n", rendered)
        self.assertIn("Session: lobby\n", rendered)

    def test_confirmed_active_owned_end_failure_is_recoverable_and_safe(self) -> None:
        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller(
                "/session end",
                "yes",
                "/quit",
            )
        )
        with (
            mock.patch.object(
                backend,
                "end_session",
                side_effect=OSError("end-secret"),
            ),
            mock.patch.object(
                backend,
                "get_session_summary",
                wraps=backend.get_session_summary,
            ) as summary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(summary.call_count, 2)
        self.assertTrue(
            workspace.read_json(_manifest_relative("session"))["active"]
        )
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.WORKSPACE_INVALID), rendered)
        self.assertNotIn("end-secret", rendered)
        self.assertEqual(rendered.count("Session: session (attached)\n"), 1)

    def test_unconfirmed_end_state_is_fatal_cleanup_uncertainty(self) -> None:
        def raise_summary_read(_value: SessionSummary) -> object:
            """Simulate a public summary read that cannot confirm ownership."""

            raise WorkspaceFormatError("summary-read-secret")

        cases: tuple[tuple[str, Callable[[SessionSummary], object]], ...] = (
            ("inactive", lambda value: replace(value, active=False)),
            (
                "available",
                lambda value: replace(
                    value,
                    lease_state=SessionLeaseState.AVAILABLE,
                ),
            ),
            ("foreign", lambda value: replace(value, session_id="other")),
            ("malformed", lambda _value: object()),
            ("read_failure", raise_summary_read),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                workspace, backend, controller, callbacks = (
                    self.prepared_attached_controller(
                        "/session end",
                        "yes",
                    )
                )
                original_get = backend.get_session_summary
                calls = 0

                def changing_summary(
                    session_id: str,
                    *,
                    selected_session: str | None = None,
                ) -> object:
                    nonlocal calls
                    calls += 1
                    value = original_get(
                        session_id,
                        selected_session=selected_session,
                    )
                    return value if calls == 1 else mutate(value)

                with (
                    mock.patch.object(
                        backend,
                        "get_session_summary",
                        side_effect=changing_summary,
                    ),
                    mock.patch.object(
                        backend,
                        "end_session",
                        side_effect=OSError("uncertain-secret"),
                    ),
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)
                rendered = "".join(callbacks.records)
                self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
                self.assertNotIn("uncertain-secret", rendered)
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )

    def test_quit_exit_and_eof_detach_before_close_without_ending(self) -> None:
        for terminal_exit in ("/quit", "/exit", EOFError()):
            with self.subTest(exit=repr(terminal_exit)):
                workspace, backend, controller, _callbacks = (
                    self.prepared_attached_controller(terminal_exit)
                )
                events: list[str] = []
                original_detach = backend.detach_session
                original_close = backend.close

                def detach(session_id: str):
                    events.append(f"detach:{session_id}")
                    return original_detach(session_id)

                def close() -> None:
                    events.append("close")
                    original_close()

                with (
                    mock.patch.object(backend, "detach_session", side_effect=detach),
                    mock.patch.object(backend, "close", side_effect=close),
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(events, ["detach:session", "close"])
                self.assertTrue(
                    workspace.read_json(_manifest_relative("session"))["active"]
                )

    def test_cleanup_failure_overrides_normal_exit_and_not_primary_error(self) -> None:
        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller("/quit")
        )
        with mock.patch.object(
            backend,
            "detach_session",
            side_effect=OSError("detach-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("detach-secret", rendered)
        self.assertFalse(workspace.path(_lease_relative("session")).exists())

        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller(RuntimeError("primary-secret"))
        )
        with mock.patch.object(
            backend,
            "detach_session",
            side_effect=OSError("cleanup-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        rendered = "".join(callbacks.records)
        self.assertLess(
            rendered.index("[error internal_error]"),
            rendered.index("[error cleanup_failed]"),
        )
        self.assertNotIn("primary-secret", rendered)
        self.assertNotIn("cleanup-secret", rendered)

        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller("/quit")
        )
        with mock.patch.object(
            backend,
            "close",
            side_effect=RuntimeError("close-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("close-secret", rendered)
        self.assertFalse(workspace.path(_lease_relative("session")).exists())


class ControllerContentionTests(ControllerTestCase):
    """Verify two real controllers cannot own one session concurrently."""

    def test_second_controller_enters_lobby_while_first_retains_lease(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "shared", active=True)
        select_session(workspace, "shared")
        context_before = workspace.path(_CONTEXT_PATH).read_bytes()
        nested: dict[str, object] = {}

        def run_contender(_prompt: str) -> str:
            """Run a second controller while the first owns the shared lease."""

            lease_path = workspace.path(_lease_relative("shared"))
            nested["lease_before"] = lease_path.read_bytes()
            inner, _backend, inner_callbacks = self.make_controller(
                workspace,
                "/quit",
            )
            nested["exit_code"] = inner.run_session_lobby()
            nested["records"] = "".join(inner_callbacks.records)
            nested["lease_after"] = lease_path.read_bytes()
            nested["context_after"] = workspace.path(_CONTEXT_PATH).read_bytes()
            return "/quit"

        outer, _backend, outer_callbacks = self.make_controller(
            workspace,
            run_contender,
        )
        self.assertEqual(outer.run_session_lobby(), 0)
        self.assertEqual(nested["exit_code"], 0)
        self.assertEqual(nested["lease_before"], nested["lease_after"])
        self.assertEqual(nested["context_after"], context_before)
        self.assertIn(
            _error_record(InteractiveErrorCode.SESSION_IN_USE),
            nested["records"],
        )
        self.assertIn("Session: lobby\n", nested["records"])
        self.assertIn("Session: shared (attached)\n", "".join(outer_callbacks.records))
        self.assertFalse(workspace.path(_lease_relative("shared")).exists())


if __name__ == "__main__":
    unittest.main()
