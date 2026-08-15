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
    load_conversation_selection,
    save_conversation_selection,
    select_session,
)
from story_writing_agents.conversation_search import (
    ConversationCitation,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    ConversationTranscriptCursor,
    ConversationTranscriptPage,
)
from story_writing_agents.conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationCatalog,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
)
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationExistsError,
    ConversationNotFoundError,
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
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    Workspace,
)

from tests.helpers import initialized_workspace


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
_CONVERSATION_SELECTION_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_selections"
)
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"
_COLLIDING_A = "c_0123456789" + ("a" * 22)
_COLLIDING_B = "c_0123456789" + ("b" * 22)
_UNIQUE_C = "c_fedcba9876" + ("c" * 22)


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


def _selection_relative(session_id: str) -> str:
    """Return one per-session conversation-selection path for assertions."""

    return f"{_CONVERSATION_SELECTION_DIRECTORY}/{session_id}.json"


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


def _conversation_metadata(
    conversation_id: str,
    subject: str,
    *,
    session_id: str = "session",
    status: ConversationStatus = ConversationStatus.ACTIVE,
    subject_revision: int = 1,
    created_at: str = _CREATED,
    updated_at: str = _UPDATED,
) -> ConversationMetadata:
    """Build one deterministic validated conversation metadata value."""

    return ConversationMetadata(
        schema_version=CONVERSATION_METADATA_SCHEMA_VERSION,
        session_id=session_id,
        conversation_id=conversation_id,
        subject=subject,
        subject_revision=subject_revision,
        status=status,
        created_at=created_at,
        updated_at=updated_at,
    )


def _stored_transcript_event(
    conversation_id: str,
    sequence: int,
    *,
    session_id: str = "session",
    content: str | None = None,
) -> ConversationEvent:
    """Build one valid stored event for controller page-validation tests."""

    return ConversationEvent(
        schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"turn_{sequence}",
        sequence=sequence,
        timestamp=_CREATED,
        sender="user",
        recipient="lead_orchestrator",
        kind=ConversationEventKind.USER_MESSAGE,
        content=f"event {sequence}" if content is None else content,
    )


def _transcript_page(
    conversation_id: str,
    events: tuple[ConversationEvent, ...],
    *,
    requested_start: int,
    requested_end: int,
    snapshot_last: int,
    cursor_next: int | None = None,
    cursor_limit: int = 24,
) -> ConversationTranscriptPage:
    """Build one structurally valid fresh page with an optional continuation."""

    cursor = (
        None
        if cursor_next is None
        else ConversationTranscriptCursor(
            "session",
            conversation_id,
            cursor_next,
            requested_end,
            snapshot_last,
            None,
            cursor_limit,
            None,
        )
    )
    return ConversationTranscriptPage(
        "session",
        conversation_id,
        events,
        requested_start,
        requested_end,
        snapshot_last,
        sum(len(event.content.encode("utf-8")) for event in events),
        cursor,
        cursor is not None,
    )


class ScriptedConversationMemoryService:
    """Return scripted search/pages while recording exact controller calls."""

    def __init__(
        self,
        *,
        search_outcomes: tuple[object, ...] = (),
        transcript_outcomes: tuple[object, ...] = (),
    ) -> None:
        """Store deterministic outcomes and call logs without resources."""

        self.search_outcomes = list(search_outcomes)
        self.transcript_outcomes = list(transcript_outcomes)
        self.search_calls: list[ConversationSearchQuery] = []
        self.transcript_calls: list[tuple[str, int, int]] = []

    def search(self, query: ConversationSearchQuery) -> object:
        """Record and return one search outcome, raising scripted failures."""

        self.search_calls.append(query)
        if not self.search_outcomes:
            raise AssertionError("unexpected conversation search")
        outcome = self.search_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def read_transcript(
        self,
        conversation_id: str,
        *,
        start_sequence: int,
        max_events: int,
    ) -> object:
        """Record and return one fresh-page outcome or scripted failure."""

        self.transcript_calls.append(
            (conversation_id, start_sequence, max_events)
        )
        if not self.transcript_outcomes:
            raise AssertionError("unexpected transcript read")
        outcome = self.transcript_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MemoryConversationCatalog:
    """Deterministic storage-neutral catalog double with observable calls."""

    def __init__(
        self,
        entries: tuple[ConversationMetadata, ...] = (),
    ) -> None:
        self.entries = list(entries)
        self.events: list[str] = []

    def create(self, session_id: str, subject: str) -> ConversationMetadata:
        self.events.append(f"create:{session_id}:{subject}")
        conversation_id = f"c_{len(self.entries) + 1:032x}"
        metadata = _conversation_metadata(
            conversation_id,
            subject,
            session_id=session_id,
            created_at=_UPDATED,
            updated_at=_UPDATED,
        )
        self.entries.append(metadata)
        return metadata

    def get(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        self.events.append(f"get:{session_id}:{conversation_id}")
        for entry in self.entries:
            if (
                entry.session_id == session_id
                and entry.conversation_id == conversation_id
            ):
                return entry
        raise ConversationNotFoundError(
            f"missing conversation: {session_id}/{conversation_id}"
        )

    def list(
        self,
        session_id: str,
        *,
        include_archived: bool = False,
    ) -> tuple[ConversationMetadata, ...]:
        self.events.append(f"list:{session_id}:{include_archived}")
        return tuple(
            entry
            for entry in self.entries
            if entry.session_id == session_id
            and (
                include_archived
                or entry.status is ConversationStatus.ACTIVE
            )
        )

    def rename(
        self,
        session_id: str,
        conversation_id: str,
        subject: str,
    ) -> ConversationMetadata:
        self.events.append(f"rename:{session_id}:{conversation_id}:{subject}")
        current = self.get(session_id, conversation_id)
        return self._replace(
            replace(
                current,
                subject=subject,
                subject_revision=current.subject_revision + 1,
            )
        )

    def archive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        self.events.append(f"archive:{session_id}:{conversation_id}")
        current = self.get(session_id, conversation_id)
        if current.status is ConversationStatus.ARCHIVED:
            return current
        return self._replace(
            replace(current, status=ConversationStatus.ARCHIVED)
        )

    def unarchive(
        self,
        session_id: str,
        conversation_id: str,
    ) -> ConversationMetadata:
        self.events.append(f"unarchive:{session_id}:{conversation_id}")
        current = self.get(session_id, conversation_id)
        if current.status is ConversationStatus.ACTIVE:
            return current
        return self._replace(replace(current, status=ConversationStatus.ACTIVE))

    def _replace(self, metadata: ConversationMetadata) -> ConversationMetadata:
        """Replace one exact identity without changing catalog order."""

        for index, current in enumerate(self.entries):
            if (
                current.session_id == metadata.session_id
                and current.conversation_id == metadata.conversation_id
            ):
                self.entries[index] = metadata
                return metadata
        raise AssertionError("replacement metadata was not present")


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
        conversation_catalog: ConversationCatalog | None = None,
        terminal_width: int = 80,
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
            width=terminal_width,
        )
        controller_keywords = (
            {}
            if conversation_catalog is None
            else {"conversation_catalog": conversation_catalog}
        )
        return (
            InteractiveController(
                workspace,
                selected_context,
                selected_backend,
                terminal,
                **controller_keywords,
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
            (
                "workspace",
                "context",
                "backend",
                "terminal",
                "conversation_catalog",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                and parameter.default is inspect.Parameter.empty
                for parameter in tuple(constructor.parameters.values())[:4]
            )
        )
        catalog_parameter = constructor.parameters["conversation_catalog"]
        self.assertIs(
            catalog_parameter.kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(catalog_parameter.default)
        self.assertEqual(
            get_type_hints(InteractiveController.__init__),
            {
                "workspace": Workspace,
                "context": CliContext,
                "backend": AgentBackend,
                "terminal": InteractiveTerminal,
                "conversation_catalog": ConversationCatalog | None,
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

        with self.assertRaisesRegex(TypeError, "ConversationCatalog"):
            InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
                conversation_catalog=object(),  # type: ignore[arg-type]
            )

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
                "conversation_search",
                "conversations",
                "errors",
                "interactive_commands",
                "interactive_terminal",
                "models",
                "orchestrator",
                "run_inputs",
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
            "from story_writing_agents import run_inputs; "
            "assert module.__all__ == ['InteractiveController']; "
            "assert run_inputs.__all__ == ['read_draft_file']; "
            "assert 'read_draft_file' not in facade.__all__; "
            "assert not hasattr(facade, 'read_draft_file'); "
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
        self.assertEqual(len(modules), 20)


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


class ControllerConversationStartupTests(ControllerTestCase):
    """Verify saved conversation restoration and nested session switching."""

    def test_missing_null_active_missing_and_archived_saved_state(self) -> None:
        cases = (
            ("missing_file", None, None, False),
            ("explicit_null", None, None, True),
            (
                "active",
                _conversation_metadata(_UNIQUE_C, "Opening scene"),
                _UNIQUE_C,
                True,
            ),
            ("missing_record", None, _UNIQUE_C, True),
            (
                "archived",
                _conversation_metadata(
                    _UNIQUE_C,
                    "Old opening",
                    status=ConversationStatus.ARCHIVED,
                ),
                _UNIQUE_C,
                True,
            ),
        )
        for label, entry, selected_id, persist_file in cases:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(workspace, "session", active=True)
                select_session(workspace, "session")
                catalog = MemoryConversationCatalog(
                    () if entry is None else (entry,)
                )
                if persist_file:
                    save_conversation_selection(
                        workspace,
                        "session",
                        selected_id,
                    )
                    selection_path = workspace.path(
                        _selection_relative("session")
                    )
                    selection_before = (
                        selection_path.read_bytes(),
                        selection_path.stat().st_mtime_ns,
                    )
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    "/quit",
                    conversation_catalog=catalog,
                )
                self.assertEqual(controller.run_session_lobby(), 0)
                scopes = [
                    record
                    for record in callbacks.records
                    if record.startswith("[context]")
                ]
                if label == "active":
                    self.assertEqual(len(scopes), 1)
                    for fact in (
                        "[context] Session: session",
                        "Conversation: c_fedcba9876",
                        "Subject: Opening scene",
                        "Agent: lead",
                    ):
                        with self.subTest(case=label, fact=fact):
                            self.assertIn(fact, scopes[0])
                else:
                    self.assertEqual(
                        scopes,
                        [
                            "[context] Session: session | Conversation: "
                            "none | Agent: lead\n"
                        ],
                    )
                if persist_file:
                    self.assertEqual(
                        (
                            selection_path.read_bytes(),
                            selection_path.stat().st_mtime_ns,
                        ),
                        selection_before,
                    )
                else:
                    self.assertFalse(
                        workspace.path(_selection_relative("session")).exists()
                    )

    def test_malformed_or_unsafe_initial_selection_aborts_and_detaches(self) -> None:
        cases = (
            (
                "malformed",
                InteractiveErrorCode.WORKSPACE_INVALID,
                False,
            ),
            (
                "symlink",
                InteractiveErrorCode.WORKSPACE_UNSAFE,
                True,
            ),
        )
        for label, expected_code, symlinked in cases:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(workspace, "session", active=True)
                select_session(workspace, "session")
                selection_path = workspace.root / _selection_relative("session")
                selection_path.parent.mkdir(parents=True, exist_ok=True)
                if symlinked:
                    external = Path(tempfile.mkdtemp()) / "selection.json"
                    self.addCleanup(
                        lambda path=external.parent: path.rmdir()
                        if path.exists()
                        else None
                    )
                    external.write_text("{}\n", encoding="utf-8")
                    self.addCleanup(
                        lambda path=external: path.unlink()
                        if path.exists()
                        else None
                    )
                    selection_path.symlink_to(external)
                else:
                    selection_path.write_text("{not-json}\n", encoding="utf-8")
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    conversation_catalog=MemoryConversationCatalog(),
                )
                self.assertEqual(controller.run_session_lobby(), 2)
                self.assertIn(_error_record(expected_code), callbacks.records)
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )
                self.assertEqual(callbacks.prompts, [])

    def test_injected_startup_metadata_tampering_is_internal_and_fatal(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session", active=True)
        select_session(workspace, "session")
        save_conversation_selection(workspace, "session", _UNIQUE_C)
        catalog = MemoryConversationCatalog(
            (_conversation_metadata(_UNIQUE_C, "Opening scene"),)
        )
        foreign = _conversation_metadata(
            _UNIQUE_C,
            "Foreign",
            session_id="other_session",
        )
        controller, backend, callbacks = self.make_controller(
            workspace,
            conversation_catalog=catalog,
        )
        with (
            mock.patch.object(catalog, "get", return_value=foreign),
            mock.patch.object(backend, "close", wraps=backend.close) as close,
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        close.assert_called_once_with()
        self.assertIn(
            _error_record(InteractiveErrorCode.INTERNAL_ERROR),
            callbacks.records,
        )
        self.assertFalse(workspace.path(_lease_relative("session")).exists())

    def test_switch_validates_destination_conversation_before_session_commit(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(workspace, session_id, active=True)
        source = _conversation_metadata(
            _COLLIDING_A,
            "Source subject",
            session_id="source",
        )
        destination = _conversation_metadata(
            _UNIQUE_C,
            "Destination subject",
            session_id="destination",
        )
        catalog = MemoryConversationCatalog((source, destination))
        select_session(workspace, "source")
        save_conversation_selection(workspace, "source", source.conversation_id)
        save_conversation_selection(
            workspace,
            "destination",
            destination.conversation_id,
        )
        selection_snapshots = {
            session_id: workspace.path(_selection_relative(session_id)).read_bytes()
            for session_id in ("source", "destination")
        }
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume destination",
            "/quit",
            backend=backend,
            conversation_catalog=catalog,
        )
        events: list[str] = []
        original_attach = backend.attach_session
        original_detach = backend.detach_session
        original_load = controller_module.load_conversation_selection
        original_select = controller_module.select_session
        original_get = catalog.get

        def attach(session_id: str):
            events.append(f"attach:{session_id}")
            return original_attach(session_id)

        def detach(session_id: str):
            events.append(f"detach:{session_id}")
            return original_detach(session_id)

        def load_selection(workspace_value: Workspace, session_id: str):
            events.append(f"load-conversation:{session_id}")
            return original_load(workspace_value, session_id)

        def select_story_session(workspace_value: Workspace, session_id: str):
            events.append(f"select-session:{session_id}")
            return original_select(workspace_value, session_id)

        def get_conversation(session_id: str, conversation_id: str):
            events.append(f"get-conversation:{session_id}")
            return original_get(session_id, conversation_id)

        with (
            mock.patch.object(backend, "attach_session", side_effect=attach),
            mock.patch.object(backend, "detach_session", side_effect=detach),
            mock.patch.object(
                controller_module,
                "load_conversation_selection",
                side_effect=load_selection,
            ),
            mock.patch.object(
                controller_module,
                "select_session",
                side_effect=select_story_session,
            ),
            mock.patch.object(catalog, "get", side_effect=get_conversation),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertLess(
            events.index("attach:destination"),
            events.index("load-conversation:destination"),
        )
        self.assertLess(
            events.index("load-conversation:destination"),
            events.index("get-conversation:destination"),
        )
        self.assertLess(
            events.index("get-conversation:destination"),
            events.index("select-session:destination"),
        )
        self.assertLess(
            events.index("select-session:destination"),
            events.index("detach:source"),
        )
        rendered = "".join(callbacks.records)
        self.assertIn("Subject: Source subject", rendered)
        self.assertIn("Subject:", rendered)
        self.assertIn("Destination subject", rendered)
        self.assertGreaterEqual(rendered.count("Agent: lead"), 2)
        self.assertEqual(
            load_cli_context(workspace).selected_session,
            "destination",
        )
        for session_id, expected in selection_snapshots.items():
            self.assertEqual(
                workspace.path(_selection_relative(session_id)).read_bytes(),
                expected,
            )

        restarted, _backend, restarted_callbacks = self.make_controller(
            workspace,
            "/quit",
            conversation_catalog=catalog,
        )
        self.assertEqual(restarted.run_session_lobby(), 0)
        restarted_rendered = "".join(restarted_callbacks.records)
        self.assertIn("Subject:", restarted_rendered)
        self.assertIn("Destination subject", restarted_rendered)
        self.assertIn("Agent: lead", restarted_rendered)

    def test_malformed_destination_releases_it_and_continues_source(self) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(workspace, session_id, active=True)
        source = _conversation_metadata(
            _UNIQUE_C,
            "Source subject",
            session_id="source",
        )
        catalog = MemoryConversationCatalog((source,))
        select_session(workspace, "source")
        save_conversation_selection(workspace, "source", source.conversation_id)
        malformed = workspace.root / _selection_relative("destination")
        malformed.parent.mkdir(parents=True, exist_ok=True)
        malformed.write_text("{not-json}\n", encoding="utf-8")
        source_selection = workspace.path(
            _selection_relative("source")
        ).read_bytes()
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume destination",
            "/quit",
            backend=backend,
            conversation_catalog=catalog,
        )
        events: list[str] = []
        original_detach = backend.detach_session

        def detach(session_id: str):
            events.append(session_id)
            return original_detach(session_id)

        with mock.patch.object(backend, "detach_session", side_effect=detach):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(events, ["destination", "source"])
        self.assertEqual(load_cli_context(workspace).selected_session, "source")
        self.assertEqual(
            workspace.path(_selection_relative("source")).read_bytes(),
            source_selection,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID),
            rendered,
        )
        self.assertEqual(rendered.count("Subject: Source subject"), 2)

    def test_archived_destination_selection_switches_to_lobby_without_rewrite(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(workspace, session_id, active=True)
        source = _conversation_metadata(
            _COLLIDING_A,
            "Source subject",
            session_id="source",
        )
        destination = _conversation_metadata(
            _UNIQUE_C,
            "Archived destination",
            session_id="destination",
            status=ConversationStatus.ARCHIVED,
        )
        catalog = MemoryConversationCatalog((source, destination))
        select_session(workspace, "source")
        for session_id, selected_id in (
            ("source", source.conversation_id),
            ("destination", destination.conversation_id),
        ):
            save_conversation_selection(workspace, session_id, selected_id)
        selection_snapshots = {
            session_id: (
                workspace.path(_selection_relative(session_id)).read_bytes(),
                workspace.path(_selection_relative(session_id)).stat().st_mtime_ns,
            )
            for session_id in ("source", "destination")
        }
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume destination",
            "/quit",
            backend=backend,
            conversation_catalog=catalog,
            terminal_width=200,
        )

        self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            load_cli_context(workspace).selected_session,
            "destination",
        )
        for session_id, snapshot in selection_snapshots.items():
            path = workspace.path(_selection_relative(session_id))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)
        rendered = "".join(callbacks.records)
        self.assertIn("Subject: Source subject", rendered)
        self.assertIn(
            "[context] Session: destination | Conversation: none | "
            "Agent: lead\n",
            rendered,
        )
        self.assertNotIn("[error conversation_archived]", rendered)


class ControllerCommandTests(ControllerTestCase):
    """Verify global commands and intentionally unavailable shell domains."""

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
        self.assertEqual(rendered.count(unavailable), 1)
        self.assertEqual(rendered.count("[error session_inactive]"), 4)
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


class ControllerSearchAndTranscriptTests(ControllerTestCase):
    """Verify provider-free memory commands and attachment-scoped wiring."""

    def prepared_attached_controller(
        self,
        *reads: object,
        entries: tuple[ConversationMetadata, ...] = (),
        selected_id: str | None = None,
    ) -> tuple[
        Workspace,
        AgentBackend,
        MemoryConversationCatalog,
        InteractiveController,
        ScriptedTerminalCallbacks,
    ]:
        """Create one attached session with an injected deterministic catalog."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "session", active=True)
        select_session(workspace, "session")
        if selected_id is not None:
            save_conversation_selection(workspace, "session", selected_id)
        catalog = MemoryConversationCatalog(entries)
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            *reads,
            backend=backend,
            conversation_catalog=catalog,
            terminal_width=240,
        )
        return workspace, backend, catalog, controller, callbacks

    def test_unselected_search_uses_default_scope_order_and_exact_status(
        self,
    ) -> None:
        exact = _conversation_metadata(_COLLIDING_A, "Council")
        prefix = _conversation_metadata(_UNIQUE_C, "Council vessel")
        query = ConversationSearchQuery("council")
        result = ConversationSearchResult(
            query,
            "council",
            (
                ConversationSearchHit(
                    ConversationCitation(
                        "session",
                        exact.conversation_id,
                        None,
                        None,
                        None,
                    ),
                    exact.subject,
                    exact.subject_revision,
                    exact.status,
                    exact.updated_at,
                    ConversationSearchMatchKind.EXACT_SUBJECT,
                    "council",
                ),
                ConversationSearchHit(
                    ConversationCitation(
                        "session",
                        prefix.conversation_id,
                        None,
                        None,
                        None,
                    ),
                    prefix.subject,
                    prefix.subject_revision,
                    prefix.status,
                    prefix.updated_at,
                    ConversationSearchMatchKind.SUBJECT_PREFIX,
                    "council vessel",
                ),
            ),
            2,
            2,
            0,
            True,
            True,
        )
        memory = ScriptedConversationMemoryService(
            search_outcomes=(result,)
        )
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_attached_controller(
                "/search council",
                "/quit",
                entries=(exact, prefix),
            )
        )

        with mock.patch.object(
            controller_module,
            "ConversationMemoryService",
            return_value=memory,
        ) as memory_factory:
            self.assertEqual(controller.run_session_lobby(), 0)

        memory_factory.assert_called_once()
        scope, bound_catalog, bound_store = memory_factory.call_args.args
        self.assertEqual(scope, ConversationSearchScope("session"))
        self.assertIs(bound_catalog, catalog)
        self.assertIs(bound_store, controller._conversation_event_store)
        self.assertIs(
            controller._runtime.conversation_event_store,
            bound_store,
        )
        self.assertEqual(memory.search_calls, [query])
        self.assertEqual(query.limit, 10)
        self.assertFalse(query.include_archived)
        rendered = "".join(callbacks.records)
        self.assertLess(
            rendered.index(f"[search {exact.conversation_id[:12]}]"),
            rendered.index(f"[search {prefix.conversation_id[:12]}]"),
        )
        self.assertIn(
            "[result] Search returned 2 of 2 matching conversations.\n",
            rendered,
        )
        self.assertIn(
            "[hint] Search was truncated by fixed scan or result limits; "
            "refine the query.\n",
            rendered,
        )
        self.assertIn("Conversation: none", rendered)
        self.assertFalse(
            workspace.path(_selection_relative("session")).exists()
        )
        self.assertIsNone(controller._conversation_memory_service)

    def test_search_failures_and_cancellation_preserve_routing_and_selection(
        self,
    ) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Council")
        memory = ScriptedConversationMemoryService(
            search_outcomes=(KeyboardInterrupt(), object()),
            transcript_outcomes=(KeyboardInterrupt(),),
        )
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_attached_controller(
                "/to plot",
                "/search invalid\x00query",
                "/search council",
                "/search valid",
                f"/transcript {entry.conversation_id}",
                "/quit",
                entries=(entry,),
            )
        )
        context_before = workspace.path(_CONTEXT_PATH).read_bytes()

        with (
            mock.patch.object(
                controller_module,
                "ConversationMemoryService",
                return_value=memory,
            ),
            mock.patch.object(controller_module.asyncio, "run") as async_run,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        async_run.assert_not_called()
        self.assertEqual(
            memory.search_calls,
            [ConversationSearchQuery("council"), ConversationSearchQuery("valid")],
        )
        self.assertEqual(
            memory.transcript_calls,
            [(entry.conversation_id, 1, 24)],
        )
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.COMMAND_INVALID), rendered)
        self.assertIn(
            "[cancelled] Search cancelled; attached state is unchanged.\n",
            rendered,
        )
        self.assertIn(
            "[cancelled] Transcript read cancelled; attached state is "
            "unchanged.\n",
            rendered,
        )
        self.assertIn(_error_record(InteractiveErrorCode.WORKSPACE_INVALID), rendered)
        self.assertEqual(workspace.path(_CONTEXT_PATH).read_bytes(), context_before)
        self.assertFalse(
            workspace.path(_selection_relative("session")).exists()
        )
        self.assertEqual(controller._target_agent_id, "plot_architect")
        self.assertIsNone(controller._conversation_memory_service)

    def test_memory_recovery_interrupt_fails_closed_for_search_and_transcript(
        self,
    ) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Council")
        cases = (
            (
                "search_workspace_invalid",
                "/search council",
                OSError("search-secret"),
                InteractiveErrorCode.WORKSPACE_INVALID,
                "Search cancelled; attached state is unchanged.",
            ),
            (
                "transcript_workspace_unsafe",
                f"/transcript {entry.conversation_id}",
                WorkspaceSecurityError("transcript-secret"),
                InteractiveErrorCode.WORKSPACE_UNSAFE,
                "Transcript read cancelled; attached state is unchanged.",
            ),
        )
        for label, command, service_failure, typed_code, cancelled in cases:
            with self.subTest(case=label):
                memory = ScriptedConversationMemoryService(
                    search_outcomes=(service_failure,)
                    if command.startswith("/search")
                    else (),
                    transcript_outcomes=(service_failure,)
                    if command.startswith("/transcript")
                    else (),
                )
                workspace, backend, catalog, controller, callbacks = (
                    self.prepared_attached_controller(
                        command,
                        entries=(entry,),
                    )
                )
                context_path = workspace.path(_CONTEXT_PATH)
                context_before = (
                    context_path.read_bytes(),
                    context_path.stat().st_mtime_ns,
                )
                original_summary = backend.get_session_summary
                summary_calls = 0

                def interrupt_recovery_proof(
                    session_id: str,
                    *,
                    selected_session: str | None = None,
                ) -> SessionSummary:
                    """Allow startup, then interrupt only the recovery proof."""

                    nonlocal summary_calls
                    summary_calls += 1
                    if summary_calls == 1:
                        return original_summary(
                            session_id,
                            selected_session=selected_session,
                        )
                    raise KeyboardInterrupt

                with (
                    mock.patch.object(
                        controller_module,
                        "ConversationMemoryService",
                        return_value=memory,
                    ),
                    mock.patch.object(
                        backend,
                        "get_session_summary",
                        side_effect=interrupt_recovery_proof,
                    ),
                    mock.patch.object(
                        backend,
                        "detach_session",
                        wraps=backend.detach_session,
                    ) as detach,
                    mock.patch.object(
                        controller_module.asyncio,
                        "run",
                    ) as async_run,
                    mock.patch.object(
                        controller._runtime,
                        "converse",
                    ) as converse,
                    mock.patch.object(
                        controller_module,
                        "select_session",
                    ) as persist_session,
                    mock.patch.object(
                        controller_module,
                        "save_conversation_selection",
                    ) as persist_conversation,
                    mock.patch.object(
                        controller._conversation_event_store,
                        "append",
                    ) as append,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                self.assertEqual(summary_calls, 2)
                detach.assert_not_called()
                for untouched in (
                    async_run,
                    converse,
                    persist_session,
                    persist_conversation,
                    append,
                ):
                    untouched.assert_not_called()
                self.assertEqual(callbacks.prompts, ["story[session:lead]> "])
                rendered = "".join(callbacks.records)
                self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
                self.assertNotIn(f"[error {typed_code.value}]", rendered)
                self.assertNotIn(f"[cancelled] {cancelled}\n", rendered)
                self.assertNotIn("[error internal_error]", rendered)
                self.assertNotIn(str(service_failure), rendered)
                self.assertIsNone(controller._attached_session_id)
                self.assertIsNone(controller._selected_conversation)
                self.assertIsNone(controller._conversation_memory_service)
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )
                self.assertFalse(
                    workspace.path(_selection_relative("session")).exists()
                )
                self.assertEqual(catalog.entries, [entry])
                self.assertEqual(
                    (
                        context_path.read_bytes(),
                        context_path.stat().st_mtime_ns,
                    ),
                    context_before,
                )

    def test_attachment_proof_does_not_swallow_system_exit(self) -> None:
        _workspace, backend, _catalog, controller, _callbacks = (
            self.prepared_attached_controller()
        )
        with mock.patch.object(
            backend,
            "get_session_summary",
            side_effect=SystemExit(17),
        ) as summary:
            with self.assertRaises(SystemExit) as raised:
                controller._attachment_is_safely_retained("session")

        self.assertEqual(raised.exception.code, 17)
        summary.assert_called_once_with(
            "session",
            selected_session="session",
        )

    def test_fresh_transcript_pages_accept_complete_and_bounded_shapes(
        self,
    ) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Subject")
        first = _stored_transcript_event(entry.conversation_id, 1)
        second = _stored_transcript_event(entry.conversation_id, 2)
        outcomes = (
            _transcript_page(
                entry.conversation_id,
                (),
                requested_start=1,
                requested_end=0,
                snapshot_last=0,
            ),
            _transcript_page(
                entry.conversation_id,
                (),
                requested_start=3,
                requested_end=2,
                snapshot_last=2,
            ),
            _transcript_page(
                entry.conversation_id,
                (first,),
                requested_start=1,
                requested_end=2,
                snapshot_last=2,
                cursor_next=2,
                cursor_limit=1,
            ),
            _transcript_page(
                entry.conversation_id,
                (first,),
                requested_start=1,
                requested_end=2,
                snapshot_last=2,
                cursor_next=2,
            ),
            _transcript_page(
                entry.conversation_id,
                (first, second),
                requested_start=1,
                requested_end=2,
                snapshot_last=2,
            ),
        )
        memory = ScriptedConversationMemoryService(
            transcript_outcomes=outcomes
        )
        handle = entry.conversation_id[:12]
        _workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_attached_controller(
                f"/transcript {entry.conversation_id}",
                f"/transcript {handle} --from 3",
                f"/transcript {handle} 1",
                f"/transcript {handle}",
                f"/transcript {handle}",
                "/quit",
                entries=(entry,),
            )
        )

        with mock.patch.object(
            controller_module,
            "ConversationMemoryService",
            return_value=memory,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            memory.transcript_calls,
            [
                (entry.conversation_id, 1, 24),
                (entry.conversation_id, 3, 24),
                (entry.conversation_id, 1, 1),
                (entry.conversation_id, 1, 24),
                (entry.conversation_id, 1, 24),
            ],
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count("[result] Transcript page returned 0 events.\n"),
            2,
        )
        self.assertEqual(
            rendered.count("[result] Transcript page returned 1 events.\n"),
            2,
        )
        self.assertIn(
            "[result] Transcript page returned 2 events.\n",
            rendered,
        )
        self.assertIn(
            f"[hint] Continue with /transcript {handle} 1 --from 2.\n",
            rendered,
        )
        self.assertIn(
            f"[hint] Continue with /transcript {handle} 24 --from 2.\n",
            rendered,
        )
        self.assertNotIn("[error transcript_invalid]", rendered)

    def test_malformed_fresh_transcript_pages_are_transcript_invalid(self) -> None:
        conversation_id = _UNIQUE_C
        first = _stored_transcript_event(conversation_id, 1)
        second = _stored_transcript_event(conversation_id, 2)
        third = _stored_transcript_event(conversation_id, 3)
        malformed_cases = (
            ("wrong_type", object()),
            ("value_failure", ValueError("private transcript detail")),
            (
                "shortened_end",
                _transcript_page(
                    conversation_id,
                    (first,),
                    requested_start=1,
                    requested_end=1,
                    snapshot_last=2,
                ),
            ),
            (
                "skipped_first",
                _transcript_page(
                    conversation_id,
                    (second,),
                    requested_start=1,
                    requested_end=2,
                    snapshot_last=2,
                ),
            ),
            (
                "noncontiguous",
                _transcript_page(
                    conversation_id,
                    (first, third),
                    requested_start=1,
                    requested_end=3,
                    snapshot_last=3,
                ),
            ),
            (
                "cursor_skip",
                _transcript_page(
                    conversation_id,
                    (first,),
                    requested_start=1,
                    requested_end=3,
                    snapshot_last=3,
                    cursor_next=3,
                ),
            ),
            (
                "short_noncursor",
                _transcript_page(
                    conversation_id,
                    (first,),
                    requested_start=1,
                    requested_end=2,
                    snapshot_last=2,
                ),
            ),
            (
                "invalid_empty",
                _transcript_page(
                    conversation_id,
                    (),
                    requested_start=1,
                    requested_end=1,
                    snapshot_last=1,
                ),
            ),
            (
                "cursor_count_changed",
                _transcript_page(
                    conversation_id,
                    (first,),
                    requested_start=1,
                    requested_end=2,
                    snapshot_last=2,
                    cursor_next=2,
                    cursor_limit=1,
                ),
            ),
        )
        entry = _conversation_metadata(conversation_id, "Subject")

        for label, outcome in malformed_cases:
            with self.subTest(case=label):
                memory = ScriptedConversationMemoryService(
                    transcript_outcomes=(outcome,)
                )
                _workspace, _backend, _catalog, controller, callbacks = (
                    self.prepared_attached_controller(
                        f"/transcript {conversation_id}",
                        "/quit",
                        entries=(entry,),
                    )
                )
                with mock.patch.object(
                    controller_module,
                    "ConversationMemoryService",
                    return_value=memory,
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    rendered.count("[error transcript_invalid]"),
                    1,
                )
                self.assertNotIn("private transcript detail", rendered)
                self.assertIsNone(controller._conversation_memory_service)

    def test_conversation_mutations_do_not_rebind_the_shared_memory_service(
        self,
    ) -> None:
        first = _conversation_metadata(_COLLIDING_A, "First")
        second = _conversation_metadata(_UNIQUE_C, "Second")
        memory = ScriptedConversationMemoryService()
        _workspace, _backend, catalog, controller, _callbacks = (
            self.prepared_attached_controller(
                f"/conversation use {second.conversation_id}",
                "/conversation subject Revised",
                "/conversation archive",
                "yes",
                "/conversation new New subject",
                "/quit",
                entries=(first, second),
                selected_id=first.conversation_id,
            )
        )

        with mock.patch.object(
            controller_module,
            "ConversationMemoryService",
            return_value=memory,
        ) as memory_factory:
            self.assertEqual(controller.run_session_lobby(), 0)

        memory_factory.assert_called_once()
        bound_store = memory_factory.call_args.args[2]
        self.assertIs(bound_store, controller._conversation_event_store)
        self.assertIs(controller._runtime.conversation_event_store, bound_store)
        self.assertEqual(len(catalog.entries), 3)
        self.assertEqual(catalog.entries[1].subject, "Revised")
        self.assertIs(catalog.entries[1].status, ConversationStatus.ARCHIVED)
        self.assertIsNone(controller._conversation_memory_service)

    def test_destination_scope_precedes_detach_and_failed_build_keeps_source(
        self,
    ) -> None:
        for fails_destination in (False, True):
            with self.subTest(fails_destination=fails_destination):
                workspace = self.make_workspace()
                for session_id in ("source", "destination"):
                    self.seed_session(workspace, session_id, active=True)
                select_session(workspace, "source")
                backend = self.make_backend(workspace)
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    "/session resume destination",
                    "/quit",
                    backend=backend,
                    conversation_catalog=MemoryConversationCatalog(),
                    terminal_width=240,
                )
                events: list[str] = []
                source_memory = ScriptedConversationMemoryService()
                destination_memory = ScriptedConversationMemoryService()

                def build_memory_service(
                    scope: ConversationSearchScope,
                    _catalog: object,
                    _store: object,
                ) -> ScriptedConversationMemoryService:
                    """Record scope construction and optionally reject destination."""

                    events.append(f"service:{scope.session_id}")
                    if fails_destination and scope.session_id == "destination":
                        raise RuntimeError("destination service secret")
                    return (
                        source_memory
                        if scope.session_id == "source"
                        else destination_memory
                    )

                original_detach = backend.detach_session

                def record_detach(session_id: str):
                    """Record and delegate one concrete session detach."""

                    events.append(f"detach:{session_id}")
                    return original_detach(session_id)

                with (
                    mock.patch.object(
                        controller_module,
                        "ConversationMemoryService",
                        side_effect=build_memory_service,
                    ),
                    mock.patch.object(
                        backend,
                        "detach_session",
                        side_effect=record_detach,
                    ),
                ):
                    self.assertEqual(
                        controller.run_session_lobby(),
                        2 if fails_destination else 0,
                    )

                if fails_destination:
                    self.assertEqual(
                        events,
                        [
                            "service:source",
                            "service:destination",
                            "detach:destination",
                            "detach:source",
                        ],
                    )
                    self.assertEqual(
                        load_cli_context(workspace).selected_session,
                        "source",
                    )
                    self.assertNotIn(
                        "destination service secret",
                        "".join(callbacks.records),
                    )
                else:
                    self.assertEqual(
                        events,
                        [
                            "service:source",
                            "service:destination",
                            "detach:source",
                            "detach:destination",
                        ],
                    )
                    self.assertEqual(
                        load_cli_context(workspace).selected_session,
                        "destination",
                    )
                self.assertIsNone(controller._conversation_memory_service)

    def test_successful_end_clears_memory_scope_before_lobby_input(self) -> None:
        memory = ScriptedConversationMemoryService()
        holder: dict[str, InteractiveController] = {}

        def observe_cleared_scope(prompt: str) -> str:
            """Assert successful end cleared the service before lobby input."""

            self.assertEqual(prompt, "story[lobby]> ")
            self.assertIsNone(
                holder["controller"]._conversation_memory_service
            )
            return "/quit"

        _workspace, _backend, _catalog, controller, _callbacks = (
            self.prepared_attached_controller(
                "/session end",
                "yes",
                observe_cleared_scope,
            )
        )
        holder["controller"] = controller
        with mock.patch.object(
            controller_module,
            "ConversationMemoryService",
            return_value=memory,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertIsNone(controller._conversation_memory_service)


class ControllerConversationCommandTests(ControllerTestCase):
    """Verify provider-free subject conversation lifecycle commands."""

    def prepared_controller(
        self,
        *reads: object,
        entries: tuple[ConversationMetadata, ...] = (),
        selected_id: str | None = None,
        catalog: MemoryConversationCatalog | None = None,
    ) -> tuple[
        Workspace,
        AgentBackend,
        MemoryConversationCatalog,
        InteractiveController,
        ScriptedTerminalCallbacks,
    ]:
        """Create one selected active story session and injected catalog."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "session", active=True)
        select_session(workspace, "session")
        selected_catalog = catalog or MemoryConversationCatalog(entries)
        if selected_id is not None:
            save_conversation_selection(
                workspace,
                "session",
                selected_id,
            )
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            *reads,
            backend=backend,
            conversation_catalog=selected_catalog,
            terminal_width=200,
        )
        return (
            workspace,
            backend,
            selected_catalog,
            controller,
            callbacks,
        )

    def test_bare_create_prompts_once_and_duplicate_subjects_stay_distinct(
        self,
    ) -> None:
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation new",
                "Opening scene",
                "/conversation new Opening scene",
                "/quit",
            )
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

        self.assertEqual(callbacks.prompts.count("conversation[subject]> "), 1)
        self.assertEqual(len(catalog.entries), 2)
        self.assertEqual(
            tuple(entry.subject for entry in catalog.entries),
            ("Opening scene", "Opening scene"),
        )
        self.assertNotEqual(
            catalog.entries[0].conversation_id,
            catalog.entries[1].conversation_id,
        )
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            catalog.entries[1].conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("Created and selected"), 2)
        self.assertIn("Subject: Opening scene | Agent: lead", rendered)
        self.assertEqual(list(workspace.root.rglob("transcript.jsonl")), [])
        self.assertEqual(list(workspace.root.rglob("*.result.json")), [])
        self.assertEqual(list(workspace.root.rglob("*.state.json")), [])
        self.assertEqual(
            [path.name for path in workspace.path("manuscript").iterdir()],
            [".gitkeep"],
        )

    def test_create_subject_interrupt_and_eof_cancel_without_mutation(self) -> None:
        cases = (
            ("interrupt", KeyboardInterrupt(), True),
            ("eof", EOFError(), False),
        )
        for label, event, continues in cases:
            with self.subTest(case=label):
                reads: tuple[object, ...] = ("/conversation new", event)
                if continues:
                    reads += ("/quit",)
                workspace, backend, catalog, controller, callbacks = (
                    self.prepared_controller(*reads)
                )
                with mock.patch.object(
                    backend,
                    "detach_session",
                    wraps=backend.detach_session,
                ) as detach:
                    self.assertEqual(controller.run_session_lobby(), 0)
                detach.assert_called_once_with("session")
                self.assertEqual(catalog.entries, [])
                self.assertFalse(
                    workspace.path(_selection_relative("session")).exists()
                )
                self.assertEqual(
                    callbacks.prompts.count("conversation[subject]> "),
                    1,
                )
                rendered = "".join(callbacks.records)
                if continues:
                    self.assertIn("creation cancelled", rendered.lower())

    def test_create_invalid_exhausted_and_selection_write_failures_are_typed(
        self,
    ) -> None:
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation new \u00a0",
                "/quit",
            )
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(catalog.entries, [])
        self.assertFalse(workspace.path(_selection_relative("session")).exists())
        self.assertIn(
            _error_record(InteractiveErrorCode.CONVERSATION_INVALID),
            callbacks.records,
        )

        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation new Subject",
                "/quit",
            )
        )
        with mock.patch.object(
            catalog,
            "create",
            side_effect=ConversationExistsError("allocation-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertIn(
            _error_record(InteractiveErrorCode.CONVERSATION_CREATE_FAILED),
            callbacks.records,
        )
        self.assertNotIn("allocation-secret", "".join(callbacks.records))

        workspace, backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation new Subject",
                "/quit",
            )
        )
        with (
            mock.patch.object(
                controller_module,
                "save_conversation_selection",
                side_effect=OSError("selection-secret"),
            ),
            mock.patch.object(
                backend,
                "get_session_summary",
                wraps=backend.get_session_summary,
            ) as summary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertGreaterEqual(summary.call_count, 2)
        self.assertEqual(len(catalog.entries), 1)
        self.assertFalse(workspace.path(_selection_relative("session")).exists())
        rendered = "".join(callbacks.records)
        self.assertIn("was created but was not selected", rendered)
        self.assertIn("[error workspace_invalid]", rendered)
        self.assertNotIn("selection-secret", rendered)
        self.assertIn("Conversation: none | Agent: lead", rendered)

    def test_list_normalizes_queries_preserves_order_and_uses_global_collisions(
        self,
    ) -> None:
        entries = (
            _conversation_metadata(
                _COLLIDING_A,
                "Ｍara   Motivation",
            ),
            _conversation_metadata(
                _COLLIDING_B,
                "Archived motivation",
                status=ConversationStatus.ARCHIVED,
            ),
            _conversation_metadata(_UNIQUE_C, "Opening Scene"),
        )
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation list",
                "/conversation list --all motivation",
                "/conversation list  OPENING  ",
                "/quit",
                entries=entries,
                selected_id=_COLLIDING_A,
            )
        )
        selection_path = workspace.path(_selection_relative("session"))
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rows = [
            record
            for record in callbacks.records
            if record.startswith("[conversation]")
        ]
        self.assertEqual(
            rows,
            [
                f"[conversation] {_COLLIDING_A} | active | selected | "
                "Subject: Ｍara   Motivation\n",
                "[conversation] c_fedcba9876 | active | not selected | "
                "Subject: Opening Scene\n",
                f"[conversation] {_COLLIDING_A} | active | selected | "
                "Subject: Ｍara   Motivation\n",
                f"[conversation] {_COLLIDING_B} | archived | not selected | "
                "Subject: Archived motivation\n",
                "[conversation] c_fedcba9876 | active | not selected | "
                "Subject: Opening Scene\n",
            ],
        )
        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )

    def test_use_resolves_full_and_unique_prefixes_across_archived_entries(
        self,
    ) -> None:
        entries = (
            _conversation_metadata(_COLLIDING_A, "First"),
            _conversation_metadata(
                _COLLIDING_B,
                "Archived",
                status=ConversationStatus.ARCHIVED,
            ),
            _conversation_metadata(_UNIQUE_C, "Unique"),
        )
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                f"/conversation use {_UNIQUE_C[:11]}",
                "/conversation use c_0123456789",
                "/conversation use c_9999999999",
                f"/conversation use {_COLLIDING_B}",
                f"/conversation use {_COLLIDING_A}",
                f"/conversation use {_UNIQUE_C[:12]}",
                "/quit",
                entries=entries,
                selected_id=_UNIQUE_C,
            )
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        for code in (
            InteractiveErrorCode.CONVERSATION_INVALID,
            InteractiveErrorCode.CONVERSATION_AMBIGUOUS,
            InteractiveErrorCode.CONVERSATION_NOT_FOUND,
            InteractiveErrorCode.CONVERSATION_ARCHIVED,
        ):
            with self.subTest(code=code):
                self.assertIn(_error_record(code), rendered)
        self.assertIn(f"Selected {_COLLIDING_A} | Subject: First", rendered)
        self.assertIn("Selected c_fedcba9876 | Subject: Unique", rendered)
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            _UNIQUE_C,
        )

    def test_use_selection_write_failure_preserves_prior_effective_choice(
        self,
    ) -> None:
        prior = _conversation_metadata(_COLLIDING_A, "Prior")
        target = _conversation_metadata(_UNIQUE_C, "Target")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                f"/conversation use {target.conversation_id}",
                "/quit",
                entries=(prior, target),
                selected_id=prior.conversation_id,
            )
        )
        selection_path = workspace.path(_selection_relative("session"))
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )
        with mock.patch.object(
            controller_module,
            "save_conversation_selection",
            side_effect=OSError("selection-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )
        self.assertFalse(any(event.startswith("rename:") for event in catalog.events))
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID),
            rendered,
        )
        self.assertGreaterEqual(rendered.count("Subject: Prior"), 2)
        self.assertNotIn("selection-secret", rendered)

    def test_external_missing_or_archived_selection_enters_lobby_in_memory_only(
        self,
    ) -> None:
        cases = (
            ("missing", InteractiveErrorCode.CONVERSATION_NOT_FOUND),
            ("archived", InteractiveErrorCode.CONVERSATION_ARCHIVED),
        )
        for change, expected_code in cases:
            with self.subTest(change=change):
                selected = _conversation_metadata(_UNIQUE_C, "Selected")
                catalog = MemoryConversationCatalog((selected,))

                def change_catalog(_prompt: str) -> str:
                    if change == "missing":
                        catalog.entries.clear()
                    else:
                        catalog.entries[0] = replace(
                            catalog.entries[0],
                            status=ConversationStatus.ARCHIVED,
                        )
                    return "/conversation subject Revised"

                workspace, _backend, _catalog, controller, callbacks = (
                    self.prepared_controller(
                        change_catalog,
                        "/quit",
                        catalog=catalog,
                        selected_id=selected.conversation_id,
                    )
                )
                selection_path = workspace.path(_selection_relative("session"))
                selection_before = (
                    selection_path.read_bytes(),
                    selection_path.stat().st_mtime_ns,
                )

                self.assertEqual(controller.run_session_lobby(), 0)

                self.assertEqual(
                    (
                        selection_path.read_bytes(),
                        selection_path.stat().st_mtime_ns,
                    ),
                    selection_before,
                )
                self.assertFalse(
                    any(event.startswith("rename:") for event in catalog.events)
                )
                rendered = "".join(callbacks.records)
                self.assertIn(_error_record(expected_code), rendered)
                self.assertIn(
                    "[context] Session: session | Conversation: none | "
                    "Agent: lead\n",
                    rendered,
                )

    def test_selected_archive_selection_failure_is_compensated_or_fatal(
        self,
    ) -> None:
        selected = _conversation_metadata(_UNIQUE_C, "Selected")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation archive",
                "yes",
                "/quit",
                entries=(selected,),
                selected_id=selected.conversation_id,
            )
        )
        with mock.patch.object(
            controller_module,
            "save_conversation_selection",
            side_effect=OSError("selection-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertIs(catalog.entries[0].status, ConversationStatus.ACTIVE)
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            selected.conversation_id,
        )
        self.assertIn(
            f"unarchive:session:{selected.conversation_id}",
            catalog.events,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID),
            rendered,
        )
        self.assertIn("original conversation remains selected", rendered)
        self.assertNotIn("selection-secret", rendered)

        selected = _conversation_metadata(_UNIQUE_C, "Selected")
        workspace, backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation archive",
                "yes",
                entries=(selected,),
                selected_id=selected.conversation_id,
            )
        )
        with (
            mock.patch.object(
                controller_module,
                "save_conversation_selection",
                side_effect=OSError("selection-secret"),
            ),
            mock.patch.object(
                catalog,
                "unarchive",
                side_effect=OSError("compensation-secret"),
            ),
            mock.patch.object(
                backend,
                "detach_session",
                wraps=backend.detach_session,
            ) as detach,
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        detach.assert_not_called()
        self.assertIs(catalog.entries[0].status, ConversationStatus.ARCHIVED)
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            selected.conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("selection-secret", rendered)
        self.assertNotIn("compensation-secret", rendered)
        self.assertFalse(workspace.path(_lease_relative("session")).exists())

        restarted, _backend, restarted_callbacks = self.make_controller(
            workspace,
            "/quit",
            conversation_catalog=catalog,
            terminal_width=200,
        )
        self.assertEqual(restarted.run_session_lobby(), 0)
        self.assertIn(
            "[context] Session: session | Conversation: none | Agent: lead\n",
            restarted_callbacks.records,
        )
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            selected.conversation_id,
        )

    def test_command_storage_failure_requires_public_owned_lease_proof(
        self,
    ) -> None:
        for retained in (True, False):
            with self.subTest(retained=retained):
                workspace, backend, catalog, controller, callbacks = (
                    self.prepared_controller(
                        "/conversation list",
                        "/quit",
                    )
                )
                original_summary = backend.get_session_summary
                summary_calls = 0

                def summarize(
                    session_id: str,
                    *,
                    selected_session: str | None = None,
                ) -> SessionSummary:
                    nonlocal summary_calls
                    summary_calls += 1
                    if retained or summary_calls == 1:
                        return original_summary(
                            session_id,
                            selected_session=selected_session,
                        )
                    return _summary(
                        session_id,
                        active=True,
                        selected=True,
                        lease_state=SessionLeaseState.AVAILABLE,
                    )

                with (
                    mock.patch.object(
                        catalog,
                        "list",
                        side_effect=OSError("catalog-secret"),
                    ),
                    mock.patch.object(
                        backend,
                        "get_session_summary",
                        side_effect=summarize,
                    ),
                    mock.patch.object(
                        backend,
                        "detach_session",
                        wraps=backend.detach_session,
                    ) as detach,
                ):
                    self.assertEqual(
                        controller.run_session_lobby(),
                        0 if retained else 2,
                    )

                self.assertEqual(summary_calls, 2)
                rendered = "".join(callbacks.records)
                self.assertNotIn("catalog-secret", rendered)
                if retained:
                    detach.assert_called_once_with("session")
                    self.assertIn(
                        _error_record(InteractiveErrorCode.WORKSPACE_INVALID),
                        rendered,
                    )
                else:
                    detach.assert_not_called()
                    self.assertEqual(
                        rendered.count("[error cleanup_failed]"),
                        1,
                    )
                    self.assertNotIn("[error workspace_invalid]", rendered)
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )

    def test_injected_catalog_collection_tampering_is_fatal_and_safe(self) -> None:
        valid = _conversation_metadata(_UNIQUE_C, "Safe subject")
        foreign = _conversation_metadata(
            _UNIQUE_C,
            "foreign-secret",
            session_id="foreign",
        )
        cases: tuple[tuple[str, object], ...] = (
            ("non_tuple", [valid]),
            ("foreign", (foreign,)),
            ("duplicate", (valid, valid)),
        )
        for label, returned in cases:
            with self.subTest(case=label):
                workspace, backend, catalog, controller, callbacks = (
                    self.prepared_controller("/conversation list")
                )
                with (
                    mock.patch.object(
                        catalog,
                        "list",
                        return_value=returned,
                    ),
                    mock.patch.object(
                        backend,
                        "detach_session",
                        wraps=backend.detach_session,
                    ) as detach,
                    mock.patch.object(
                        backend,
                        "close",
                        wraps=backend.close,
                    ) as close,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)
                detach.assert_called_once_with("session")
                close.assert_called_once_with()
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                    rendered,
                )
                self.assertNotIn("foreign-secret", rendered)
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )

    def test_later_feature_allowlists_are_provider_and_artifact_free(
        self,
    ) -> None:
        conversation_actions = (
            "/conversation new Subject",
            "/conversation list",
            "/conversation list --all",
            f"/conversation use {_UNIQUE_C}",
            "/conversation subject Revised",
            "/conversation archive",
        )
        future_actions = (
            "/compose",
            "/send",
            "/cancel",
            "/inspect last",
            "/history 20",
            "/status",
            "/language ko-KR",
            "/persona plot meticulous.v2",
            "/reset",
        )

        def protected_outputs(workspace: Workspace) -> dict[str, bytes]:
            """Snapshot provider-owned and story-content output surfaces."""

            outputs: dict[str, bytes] = {}
            for path in workspace.root.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(workspace.root)
                parts = relative.parts
                if (
                    parts[0]
                    in {"core_documents", "discussion_logs", "manuscript"}
                    or "debates" in parts
                    or any("search" in part.casefold() for part in parts)
                    or path.name == "transcript.jsonl"
                    or path.name.endswith((".result.json", ".state.json"))
                ):
                    outputs[relative.as_posix()] = path.read_bytes()
            return outputs

        journey_snapshots: list[
            tuple[Workspace, dict[str, bytes]]
        ] = []
        provider_patches = (
            mock.patch.object(subprocess, "Popen"),
            mock.patch.object(subprocess, "run"),
            mock.patch.object(socket, "create_connection"),
        )
        with provider_patches[0] as popen, provider_patches[1] as run, (
            provider_patches[2]
        ) as connect:
            workspace = self.make_workspace()
            catalog = MemoryConversationCatalog()
            controller, _backend, callbacks = self.make_controller(
                workspace,
                *conversation_actions,
                "/quit",
                conversation_catalog=catalog,
                terminal_width=200,
            )
            journey_snapshots.append((workspace, protected_outputs(workspace)))
            self.assertEqual(controller.run_session_lobby(), 0)
            self.assertEqual(catalog.events, [])
            rendered = "".join(callbacks.records)
            self.assertEqual(
                rendered.count("[error session_inactive]"),
                len(conversation_actions),
            )

            workspace, _backend, catalog, controller, callbacks = (
                self.prepared_controller(*future_actions, "/quit")
            )
            journey_snapshots.append((workspace, protected_outputs(workspace)))
            self.assertEqual(controller.run_session_lobby(), 0)
            self.assertEqual(catalog.events, [])
            rendered = "".join(callbacks.records)
            self.assertEqual(
                rendered.count("[error conversation_required]"),
                len(future_actions),
            )

            workspace, _backend, catalog, controller, callbacks = (
                self.prepared_controller(
                    "/conversation subject Revised",
                    "/conversation archive",
                    "/conversation list",
                    f"/conversation use {_UNIQUE_C}",
                    f"/conversation archive {_UNIQUE_C}",
                    "/conversation new Allowed",
                    "/quit",
                )
            )
            journey_snapshots.append((workspace, protected_outputs(workspace)))
            self.assertEqual(controller.run_session_lobby(), 0)
            rendered = "".join(callbacks.records)
            self.assertEqual(
                rendered.count("[error conversation_required]"),
                2,
            )
            self.assertIn("No conversations found", rendered)
            self.assertEqual(
                rendered.count("[error conversation_not_found]"),
                2,
            )
            self.assertEqual(len(catalog.entries), 1)

            selected = _conversation_metadata(_UNIQUE_C, "Selected")
            workspace, _backend, _catalog, controller, callbacks = (
                self.prepared_controller(
                    *future_actions,
                    "/quit",
                    entries=(selected,),
                    selected_id=selected.conversation_id,
                )
            )
            journey_snapshots.append((workspace, protected_outputs(workspace)))
            self.assertEqual(controller.run_session_lobby(), 0)
            rendered = "".join(callbacks.records)
            self.assertEqual(
                rendered.count(
                    "[error command_invalid] This command is not available "
                    "in the session lobby."
                ),
                len(future_actions),
            )

        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        for journey_workspace, before in journey_snapshots:
            self.assertEqual(protected_outputs(journey_workspace), before)

    def test_rename_preserves_identity_and_rejects_invalid_subject(self) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Original")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation subject \u00a0",
                "/conversation subject Revised subject",
                "/quit",
                entries=(entry,),
                selected_id=entry.conversation_id,
            )
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        renamed = catalog.entries[0]
        self.assertEqual(renamed.conversation_id, entry.conversation_id)
        self.assertEqual(renamed.created_at, entry.created_at)
        self.assertEqual(renamed.subject_revision, 2)
        self.assertEqual(renamed.subject, "Revised subject")
        self.assertIs(renamed.status, ConversationStatus.ACTIVE)
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            entry.conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.CONVERSATION_INVALID),
            rendered,
        )
        self.assertIn("Renamed c_fedcba9876 | Subject: Revised subject", rendered)

    def test_selected_archive_chooses_first_active_successor_or_persists_null(
        self,
    ) -> None:
        for has_successor in (True, False):
            with self.subTest(has_successor=has_successor):
                selected = _conversation_metadata(_COLLIDING_A, "Selected")
                successor = _conversation_metadata(_UNIQUE_C, "Successor")
                entries = (
                    (selected, successor)
                    if has_successor
                    else (selected,)
                )
                workspace, _backend, catalog, controller, callbacks = (
                    self.prepared_controller(
                        "/conversation archive",
                        "yes",
                        "/quit",
                        entries=entries,
                        selected_id=selected.conversation_id,
                    )
                )
                self.assertEqual(controller.run_session_lobby(), 0)
                self.assertIs(
                    catalog.entries[0].status,
                    ConversationStatus.ARCHIVED,
                )
                expected = successor.conversation_id if has_successor else None
                self.assertEqual(
                    load_conversation_selection(workspace, "session"),
                    expected,
                )
                self.assertIn(
                    'Archive current conversation "Selected"',
                    callbacks.prompts[1],
                )
                rendered = "".join(callbacks.records)
                if has_successor:
                    self.assertIn("selected c_fedcba9876", rendered)
                else:
                    self.assertIn("no conversation is selected", rendered)

    def test_nonselected_and_already_archived_explicit_archive_do_not_reselect(
        self,
    ) -> None:
        selected = _conversation_metadata(_COLLIDING_A, "Selected")
        other = _conversation_metadata(_UNIQUE_C, "Other")
        archived = _conversation_metadata(
            _COLLIDING_B,
            "Archived",
            status=ConversationStatus.ARCHIVED,
        )
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                f"/conversation archive {archived.conversation_id}",
                f"/conversation archive {other.conversation_id}",
                "/quit",
                entries=(selected, other, archived),
                selected_id=selected.conversation_id,
            )
        )
        archived_before = archived.to_dict()
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(catalog.entries[2].to_dict(), archived_before)
        self.assertIs(catalog.entries[1].status, ConversationStatus.ARCHIVED)
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            selected.conversation_id,
        )
        self.assertNotIn("Archive current conversation", callbacks.prompts)
        rendered = "".join(callbacks.records)
        self.assertIn("Already archived", rendered)
        self.assertIn("Archived c_fedcba9876 | Subject: Other", rendered)

    def test_selected_archive_no_blank_interrupt_and_eof_preserve_state(
        self,
    ) -> None:
        cases = (
            ("no", "no", True),
            ("blank", "", True),
            ("interrupt", KeyboardInterrupt(), True),
            ("eof", EOFError(), False),
        )
        for label, decision, continues in cases:
            with self.subTest(case=label):
                selected = _conversation_metadata(_UNIQUE_C, "Selected")
                reads: tuple[object, ...] = (
                    "/conversation archive",
                    decision,
                )
                if continues:
                    reads += ("/quit",)
                workspace, backend, catalog, controller, callbacks = (
                    self.prepared_controller(
                        *reads,
                        entries=(selected,),
                        selected_id=selected.conversation_id,
                    )
                )
                with mock.patch.object(
                    backend,
                    "detach_session",
                    wraps=backend.detach_session,
                ) as detach:
                    self.assertEqual(controller.run_session_lobby(), 0)
                detach.assert_called_once_with("session")
                self.assertIs(
                    catalog.entries[0].status,
                    ConversationStatus.ACTIVE,
                )
                self.assertEqual(
                    load_conversation_selection(workspace, "session"),
                    selected.conversation_id,
                )
                rendered = "".join(callbacks.records)
                if continues:
                    self.assertIn("archive cancelled", rendered.lower())

    def test_output_failure_after_each_durable_commit_does_not_roll_back(
        self,
    ) -> None:
        for operation in ("create", "use", "rename", "archive"):
            with self.subTest(operation=operation):
                if operation == "create":
                    workspace, backend, catalog, controller, callbacks = (
                        self.prepared_controller(
                            "/conversation new Created",
                        )
                    )
                elif operation == "use":
                    prior = _conversation_metadata(_COLLIDING_A, "Prior")
                    target = _conversation_metadata(_UNIQUE_C, "Target")
                    workspace, backend, catalog, controller, callbacks = (
                        self.prepared_controller(
                            f"/conversation use {target.conversation_id}",
                            entries=(prior, target),
                            selected_id=prior.conversation_id,
                        )
                    )
                elif operation == "rename":
                    selected = _conversation_metadata(_UNIQUE_C, "Original")
                    workspace, backend, catalog, controller, callbacks = (
                        self.prepared_controller(
                            "/conversation subject Revised",
                            entries=(selected,),
                            selected_id=selected.conversation_id,
                        )
                    )
                else:
                    selected = _conversation_metadata(_COLLIDING_A, "Selected")
                    successor = _conversation_metadata(_UNIQUE_C, "Successor")
                    workspace, backend, catalog, controller, callbacks = (
                        self.prepared_controller(
                            "/conversation archive",
                            "yes",
                            entries=(selected, successor),
                            selected_id=selected.conversation_id,
                        )
                    )

                with (
                    mock.patch.object(
                        InteractiveTerminal,
                        "write_status",
                        side_effect=RuntimeError("writer-secret"),
                    ),
                    mock.patch.object(
                        backend,
                        "detach_session",
                        wraps=backend.detach_session,
                    ) as detach,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                detach.assert_called_once_with("session")
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                    rendered,
                )
                self.assertNotIn("writer-secret", rendered)
                self.assertFalse(
                    workspace.path(_lease_relative("session")).exists()
                )
                if operation == "create":
                    self.assertEqual(len(catalog.entries), 1)
                    self.assertEqual(
                        load_conversation_selection(workspace, "session"),
                        catalog.entries[0].conversation_id,
                    )
                elif operation == "use":
                    self.assertEqual(
                        load_conversation_selection(workspace, "session"),
                        target.conversation_id,
                    )
                elif operation == "rename":
                    self.assertEqual(catalog.entries[0].subject, "Revised")
                    self.assertEqual(
                        load_conversation_selection(workspace, "session"),
                        selected.conversation_id,
                    )
                else:
                    self.assertIs(
                        catalog.entries[0].status,
                        ConversationStatus.ARCHIVED,
                    )
                    self.assertEqual(
                        load_conversation_selection(workspace, "session"),
                        successor.conversation_id,
                    )

    def test_session_end_and_resume_restore_the_same_durable_conversation(
        self,
    ) -> None:
        selected = _conversation_metadata(_UNIQUE_C, "Persistent subject")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/session end",
                "yes",
                "/session resume",
                "/quit",
                entries=(selected,),
                selected_id=selected.conversation_id,
            )
        )
        selection_path = workspace.path(_selection_relative("session"))
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )

        self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )
        self.assertEqual(
            load_conversation_selection(workspace, "session"),
            selected.conversation_id,
        )
        self.assertTrue(workspace.read_json(_manifest_relative("session"))["active"])
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("Subject: Persistent subject"), 2)
        self.assertIn("Ended session; selection retained", rendered)
        self.assertIn("Session: session (resumed)", rendered)
        self.assertTrue(
            all(
                event.startswith(("get:", "list:"))
                for event in catalog.events
            )
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
