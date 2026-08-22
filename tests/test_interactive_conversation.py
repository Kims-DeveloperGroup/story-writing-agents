"""Interactive shell routing and retained conversation-runtime contracts."""

from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest import mock

from story_writing_agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    SessionDurableState,
)
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    CliContext,
    load_cli_context,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversations import (
    CONVERSATION_EVENT_SCHEMA_VERSION,
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
)
from story_writing_agents.conversation_retrieval import (
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalResult,
    ConversationRetrievalProgressEvent,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalSource,
)
from story_writing_agents.conversation_search import ConversationCitation
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationNotFoundError,
    ConversationRetrievalBudgetError,
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
    SessionExistsError,
    SessionIdError,
    SessionAccessBlockedError,
    SessionNotFoundError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.interactive_commands import (
    ActionKind,
    InteractiveAction,
)
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_terminal._render import render_plain
from story_writing_agents.models import (
    AgentDefinition,
    ConversationReply,
)
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
)

from tests.helpers import core_snapshot, initialized_workspace


_TIMESTAMP = "2026-01-01T00:00:00+00:00"
_REAL_RUNTIME = object()


class ScriptedTerminalCallbacks:
    """Record exact terminal traffic and return scripted lines or events."""

    def __init__(self, *reads: object) -> None:
        self.reads = list(reads)
        self.prompts: list[str] = []
        self.records: list[str] = []

    def read_line(self, prompt: str) -> str:
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
        self.records.append(text)


OutcomeFactory = Callable[[str, str, str, str], object]


class ScriptedRuntime:
    """Retained async runtime double with loop and exact-call observability."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str, str, str]] = []
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.tasks: list[asyncio.Task[object] | None] = []
        self.memory_services: list[object] = []
        self.progress_sinks: list[object] = []
        self.on_call: Callable[[], None] | None = None

    async def converse_with_retrieval(
        self,
        session_id: str,
        conversation_id: str,
        agent_id: str,
        text: str,
        *,
        memory_service: object,
        progress_sink: object = None,
    ) -> object:
        """Return one scripted retrieval outcome and record selected scope."""

        self.calls.append((session_id, conversation_id, agent_id, text))
        self.loops.append(asyncio.get_running_loop())
        self.tasks.append(asyncio.current_task())
        self.memory_services.append(memory_service)
        self.progress_sinks.append(progress_sink)
        if self.on_call is not None:
            self.on_call()
        outcome = self.outcomes.pop(0) if self.outcomes else "reply"
        if callable(outcome):
            outcome = outcome(session_id, conversation_id, agent_id, text)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return _conversation_result(
                session_id,
                conversation_id,
                agent_id,
                outcome,
                turn_id=f"turn_{len(self.calls)}",
            )
        return outcome


ConversationHandler = Callable[
    [ConversationRetrievalRequest],
    ConversationReply | BaseException | Awaitable[ConversationReply],
]


class RecordingConversationBound:
    """Deterministic real-runtime binding that never invokes a provider."""

    def __init__(
        self,
        definition: AgentDefinition,
        handler: ConversationHandler,
        requests: list[ConversationRetrievalRequest],
        loops: list[asyncio.AbstractEventLoop],
        tasks: list[asyncio.Task[object] | None],
    ) -> None:
        self.definition = definition
        self.handler = handler
        self.requests = requests
        self.loops = loops
        self.tasks = tasks

    async def invoke(self, _request: object) -> object:
        raise AssertionError("consensus invocation is outside this test boundary")

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        action_handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        """Translate one scripted terminal answer through the real handler."""

        if request.agent_id != self.definition.agent_id:
            raise AssertionError("conversation used the wrong bound agent")
        self.requests.append(request)
        self.loops.append(asyncio.get_running_loop())
        self.tasks.append(asyncio.current_task())
        outcome = self.handler(request)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if isinstance(outcome, BaseException):
            raise outcome
        if type(outcome) is not ConversationReply:
            raise AssertionError("scripted bound handler returned an invalid reply")
        result = action_handler.handle_conversation_retrieval_action(
            ConversationRetrievalAnswerAction(
                kind=ConversationRetrievalActionKind.ANSWER,
                session_id=request.session_id,
                conversation_id=request.conversation_id,
                turn_id=request.turn_id,
                action_index=1,
                content=outcome.content,
                citation_ids=(),
            )
        )
        if type(result) is not ConversationRetrievalReply:
            raise AssertionError("direct answer did not produce a retrieval reply")
        return result


def _error_record(code: InteractiveErrorCode) -> str:
    """Return one exact semantic error callback at this suite's width."""

    spec = INTERACTIVE_ERROR_SPECS[code]
    component = terminal_module.Panel(
        "Interactive error",
        (
            terminal_module.Notice(
                spec.summary,
                label=f"error {code.value}",
                role=terminal_module.SemanticRole.ERROR,
                state=terminal_module.ViewState.ERROR,
            ),
            terminal_module.Notice(spec.hint, label="hint"),
            terminal_module.Notice(
                "If available, workspace-relative: debug_logs/runtime.jsonl",
                label="debug log",
            ),
        ),
        state=terminal_module.ViewState.ERROR,
    )
    return f"{render_plain(component, width=240).text}\n"


def _empty_context() -> ConversationContextWindow:
    return ConversationContextWindow(
        events=(),
        total_complete_turns=0,
        retained_turns=0,
        omitted_turns=0,
        retained_utf8_bytes=0,
        truncated=False,
    )


def _conversation_result(
    session_id: str,
    conversation_id: str,
    agent_id: str,
    content: str,
    *,
    turn_id: str = "current_turn",
    context: ConversationContextWindow | None = None,
) -> ConversationRetrievalResult:
    """Build one direct-answer retrieval result for controller fixtures."""

    return ConversationRetrievalResult(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        agent_id=agent_id,
        content=content,
        context=_empty_context() if context is None else context,
        sources=(),
    )


def _truncated_context(
    session_id: str,
    conversation_id: str,
) -> ConversationContextWindow:
    events: list[ConversationEvent] = []
    contents = (
        ("prior question one", "prior reply one"),
        ("prior question two", "prior reply two"),
    )
    for turn_number, (question, reply) in enumerate(contents, start=1):
        turn_id = f"prior_turn_{turn_number}"
        sequence = (turn_number - 1) * 2 + 1
        events.extend(
            (
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    session_id,
                    conversation_id,
                    turn_id,
                    sequence,
                    _TIMESTAMP,
                    "user",
                    LEAD_ORCHESTRATOR,
                    ConversationEventKind.USER_MESSAGE,
                    question,
                ),
                ConversationEvent(
                    CONVERSATION_EVENT_SCHEMA_VERSION,
                    session_id,
                    conversation_id,
                    turn_id,
                    sequence + 1,
                    _TIMESTAMP,
                    LEAD_ORCHESTRATOR,
                    "user",
                    ConversationEventKind.AGENT_REPLY,
                    reply,
                ),
            )
        )
    retained_bytes = sum(
        len(event.content.encode("utf-8")) for event in events
    )
    return ConversationContextWindow(
        events=tuple(events),
        total_complete_turns=5,
        retained_turns=2,
        omitted_turns=3,
        retained_utf8_bytes=retained_bytes,
        truncated=True,
    )


def _transcript_relative(session_id: str, conversation_id: str) -> str:
    return (
        f"discussion_logs/{session_id}/interactive/conversations/"
        f"{conversation_id}/transcript.jsonl"
    )


class InteractiveConversationTestCase(unittest.TestCase):
    """Provide real lifecycle, catalog, and scripted-runtime construction."""

    def make_workspace(self) -> Workspace:
        return initialized_workspace(self)

    def seed_session(
        self,
        workspace: Workspace,
        session_id: str,
        *,
        durable_state: SessionDurableState = SessionDurableState.READY,
    ) -> None:
        backend = AgentBackend(workspace, executable=sys.executable)
        try:
            backend.create_session_with_access(session_id)
            if durable_state is SessionDurableState.PAUSED:
                backend.pause_session_and_release_access(session_id)
        finally:
            backend.close()

    def make_backend(self, workspace: Workspace) -> AgentBackend:
        backend = AgentBackend(workspace, executable=sys.executable)
        self.addCleanup(backend.close)
        return backend

    def make_controller(
        self,
        workspace: Workspace,
        *reads: object,
        backend: AgentBackend | None = None,
        catalog: FilesystemConversationCatalog | None = None,
        runtime: ScriptedRuntime | object = _REAL_RUNTIME,
        width: int = 240,
    ) -> tuple[
        InteractiveController,
        AgentBackend,
        ScriptedTerminalCallbacks,
        FilesystemConversationEventStore | None,
        mock.Mock | None,
    ]:
        selected_backend = backend or self.make_backend(workspace)
        callbacks = ScriptedTerminalCallbacks(*reads)
        terminal = InteractiveTerminal(
            callbacks.read_line,
            callbacks.write_text,
            width=width,
        )
        keywords: dict[str, object] = {}
        if catalog is not None:
            keywords["conversation_catalog"] = catalog

        if runtime is _REAL_RUNTIME:
            controller = InteractiveController(
                workspace,
                load_cli_context(workspace),
                selected_backend,
                terminal,
                **keywords,
            )
            return controller, selected_backend, callbacks, None, None

        with mock.patch.object(
            controller_core_module,
            "StoryRuntime",
            return_value=runtime,
        ) as runtime_factory:
            controller = InteractiveController(
                workspace,
                load_cli_context(workspace),
                selected_backend,
                terminal,
                **keywords,
            )
        call = runtime_factory.call_args
        assert call is not None
        store = call.kwargs["conversation_event_store"]
        return controller, selected_backend, callbacks, store, runtime_factory

    def prepared_selected_controller(
        self,
        *reads: object,
        runtime: ScriptedRuntime | object,
        session_id: str = "session",
        subjects: tuple[str, ...] = ("Selected subject",),
        selected_index: int = 0,
    ) -> tuple[
        Workspace,
        FilesystemConversationCatalog,
        tuple[ConversationMetadata, ...],
        InteractiveController,
        AgentBackend,
        ScriptedTerminalCallbacks,
        FilesystemConversationEventStore | None,
    ]:
        workspace = self.make_workspace()
        self.seed_session(workspace, session_id)
        save_default_session(workspace, session_id)
        catalog = FilesystemConversationCatalog(workspace)
        entries = tuple(catalog.create(session_id, subject) for subject in subjects)
        save_default_conversation(
            workspace,
            session_id,
            entries[selected_index].conversation_id,
        )
        controller, backend, callbacks, store, _factory = self.make_controller(
            workspace,
            *reads,
            catalog=catalog,
            runtime=runtime,
        )
        return (
            workspace,
            catalog,
            entries,
            controller,
            backend,
            callbacks,
            store,
        )


class RuntimeWiringAndGateTests(InteractiveConversationTestCase):
    """Verify retained runtime wiring and provider-free state gates."""

    def test_default_and_injected_catalogs_are_the_event_store_authority(
        self,
    ) -> None:
        workspace = self.make_workspace()
        backend = self.make_backend(workspace)
        callbacks = ScriptedTerminalCallbacks("/quit")
        terminal = InteractiveTerminal(callbacks.read_line, callbacks.write_text)
        default_catalog = FilesystemConversationCatalog(workspace)
        runtime = ScriptedRuntime()
        before = tuple(workspace.root.rglob("*"))
        with (
            mock.patch.object(
                controller_core_module,
                "FilesystemConversationCatalog",
                return_value=default_catalog,
            ) as catalog_factory,
            mock.patch.object(
                controller_core_module,
                "StoryRuntime",
                return_value=runtime,
            ) as runtime_factory,
        ):
            InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
            )
        catalog_factory.assert_called_once_with(workspace)
        runtime_factory.assert_called_once()
        default_store = runtime_factory.call_args.kwargs[
            "conversation_event_store"
        ]
        self.assertIsInstance(default_store, FilesystemConversationEventStore)
        self.assertIs(default_store.catalog, default_catalog)
        self.assertEqual(tuple(workspace.root.rglob("*")), before)

        injected = FilesystemConversationCatalog(workspace)
        runtime = ScriptedRuntime()
        with (
            mock.patch.object(
                controller_core_module,
                "FilesystemConversationCatalog",
            ) as catalog_factory,
            mock.patch.object(
                controller_core_module,
                "StoryRuntime",
                return_value=runtime,
            ) as runtime_factory,
        ):
            InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
                conversation_catalog=injected,
            )
        catalog_factory.assert_not_called()
        runtime_factory.assert_called_once()
        injected_store = runtime_factory.call_args.kwargs[
            "conversation_event_store"
        ]
        self.assertIs(injected_store.catalog, injected)
        self.assertEqual(
            runtime_factory.call_args.args,
            (workspace, backend),
        )

    def test_global_roster_and_unattached_routing_are_provider_free(self) -> None:
        workspace = self.make_workspace()
        runtime = ScriptedRuntime()
        controller, backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/agents",
            "/to plot",
            "ordinary",
            "@plot routed",
            "/say /quit",
            "/quit",
            runtime=runtime,
        )
        with (
            mock.patch.object(backend, "validate_provider_configuration") as validate_configuration,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
            ) as run_boundary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(runtime.calls, [])
        run_boundary.assert_not_called()
        validate_configuration.assert_not_called()
        bind.assert_not_called()
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[agent] "), 4)
        roster_records = [
            record for record in callbacks.records if record.startswith("Agents (4)")
        ]
        self.assertEqual(len(roster_records), 1)
        roster = roster_records[0]
        ordered_ids = (
            "lead_orchestrator",
            "plot_architect",
            "character_custodian",
            "execution_specialist",
        )
        positions = [roster.index(f"ID: {agent_id}") for agent_id in ordered_ids]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(rendered.count("[error session_not_attached]"), 4)
        self.assertNotIn("[error conversation_required]", rendered)

    def test_attached_target_changes_without_a_default_conversation(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        runtime = ScriptedRuntime()
        controller, backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/to character",
            "/agent plot",
            "/agents",
            "ordinary",
            "@execution routed",
            "/say /run literal",
            "/quit",
            catalog=catalog,
            runtime=runtime,
        )
        real_run = asyncio.run
        with mock.patch.object(
            controller_core_module.asyncio,
            "run",
            side_effect=real_run,
        ) as run_boundary:
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(runtime.calls, [])
        run_boundary.assert_not_called()
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error conversation_required]"), 3)
        self.assertEqual(rendered.count("[agent] "), 4)
        main_prompts = [
            prompt for prompt in callbacks.prompts if prompt.startswith("story[")
        ]
        self.assertEqual(main_prompts[0], "story[session:lead]> ")
        self.assertEqual(main_prompts[1], "story[session:character]> ")
        self.assertTrue(
            all(prompt == "story[session:plot]> " for prompt in main_prompts[2:])
        )
        self.assertFalse(
            workspace.path(
                "discussion_logs/session/interactive"
            ).exists()
        )


class ControllerRoutingTests(InteractiveConversationTestCase):
    """Verify exact routing, sticky state, output, and parse categories."""

    def test_default_one_shot_sticky_handback_and_say_use_one_runtime_call(
        self,
    ) -> None:
        runtime = ScriptedRuntime(
            "lead reply\x1b",
            "plot reply",
            "character reply",
            "literal reply",
        )
        (
            workspace,
            _catalog,
            entries,
            controller,
            backend,
            callbacks,
            store,
        ) = self.prepared_selected_controller(
            "ordinary",
            "@plot routed",
            "/to character",
            "sticky",
            "/agent lead",
            "/say /run is literal",
            "/quit",
            runtime=runtime,
        )
        selection_path = workspace.path(
            "agent_states/orchestrator_state/conversation_defaults/session.json"
        )
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )
        assert store is not None
        real_run = asyncio.run
        with (
            mock.patch.object(store, "read_context") as read_context,
            mock.patch.object(store, "append") as append,
            mock.patch.object(
                backend,
                "validate_provider_configuration",
                wraps=backend.validate_provider_configuration,
            ) as validate_configuration,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
                side_effect=real_run,
            ) as run_boundary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        conversation_id = entries[0].conversation_id
        self.assertEqual(
            runtime.calls,
            [
                ("session", conversation_id, LEAD_ORCHESTRATOR, "ordinary"),
                ("session", conversation_id, PLOT_ARCHITECT, "routed"),
                ("session", conversation_id, CHARACTER_CUSTODIAN, "sticky"),
                (
                    "session",
                    conversation_id,
                    LEAD_ORCHESTRATOR,
                    "/run is literal",
                ),
            ],
        )
        self.assertEqual(len(runtime.memory_services), 4)
        self.assertEqual(len({id(service) for service in runtime.memory_services}), 1)
        self.assertEqual(runtime.memory_services[0].scope.session_id, "session")
        self.assertTrue(all(callable(sink) for sink in runtime.progress_sinks))
        run_boundary.assert_not_called()
        self.assertEqual(len({id(loop) for loop in runtime.loops}), 1)
        self.assertTrue(all(loop.is_closed() for loop in runtime.loops))
        self.assertTrue(all(task is not None and task.done() for task in runtime.tasks))
        read_context.assert_not_called()
        append.assert_not_called()
        validate_configuration.assert_called_once_with("session")
        bind.assert_not_called()
        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )
        self.assertFalse(
            workspace.path(_transcript_relative("session", conversation_id)).exists()
        )

        rendered = "".join(callbacks.records)
        self.assertNotIn("[routing] One-shot route", rendered)
        self.assertNotIn("[waiting] ", rendered)
        self.assertIn("lead [completed]> lead reply\\x1b\n", rendered)
        self.assertNotIn("lead reply\x1b", rendered)
        main_prompts = [
            prompt for prompt in callbacks.prompts if prompt.startswith("story[")
        ]
        self.assertEqual(
            main_prompts,
            [
                "story[session:lead]> ",
                "story[session:lead]> ",
                "story[session:lead]> ",
                "story[session:character]> ",
                "story[session:character]> ",
                "story[session:lead]> ",
                "story[session:lead]> ",
            ],
        )

    def test_disclosure_waiting_result_truncation_and_sanitization_are_ordered(
        self,
    ) -> None:
        raw_reply = '{"answer":"界", "items":[1, true]}'

        def result(
            session_id: str,
            conversation_id: str,
            agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            return _conversation_result(
                session_id,
                conversation_id,
                agent_id,
                raw_reply,
                context=_truncated_context(session_id, conversation_id),
            )

        runtime = ScriptedRuntime(result)
        (
            _workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            "message",
            "/quit",
            runtime=runtime,
        )
        records_at_call: list[tuple[str, ...]] = []
        runtime.on_call = lambda: records_at_call.append(tuple(callbacks.records))
        self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(len(records_at_call), 1)
        self.assertFalse(
            any(
                "[routing] " in record or "[waiting] " in record
                for record in records_at_call[0]
            )
        )
        rendered = "".join(callbacks.records)
        truncation_index = rendered.index("[context] [completed] [warning]")
        reply_index = rendered.index('lead [completed]> {')
        self.assertLess(truncation_index, reply_index)
        self.assertNotIn("[routing] ", rendered)
        self.assertNotIn("[waiting] ", rendered)
        self.assertIn(
            "[context] [completed] [warning]\n"
            "  Context truncated: 4 events retained across 2 turns; "
            "3 earlier turns omitted.\n",
            rendered,
        )
        self.assertIn(
            '"answer": "界",\n'
            '                    "items": [\n'
            '                      1,\n'
            '                      true\n',
            rendered,
        )
        self.assertNotIn(
            'lead [completed]> {"answer":"界", "items":[1, true]}',
            rendered,
        )
        self.assertNotIn("\x1b", rendered)

    def test_retrieval_progress_sources_and_selected_scope_render_exactly(self) -> None:
        runtime = ScriptedRuntime()
        turn_id = "retrieval_turn"

        def outcome(
            session_id: str,
            conversation_id: str,
            agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            """Emit one valid search/read sequence and return cited sources."""

            sink = runtime.progress_sinks[-1]
            assert callable(sink)
            common = {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "agent_id": agent_id,
            }
            sink(
                ConversationRetrievalProgressEvent(
                    **common,
                    action_index=1,
                    phase=ConversationRetrievalProgressPhase.SEARCH_STARTED,
                )
            )
            sink(
                ConversationRetrievalProgressEvent(
                    **common,
                    action_index=1,
                    phase=ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
                    search_hit_count=2,
                    observation_utf8_bytes=321,
                    truncated=True,
                )
            )
            sink(
                ConversationRetrievalProgressEvent(
                    **common,
                    action_index=2,
                    phase=ConversationRetrievalProgressPhase.READ_STARTED,
                )
            )
            sink(
                ConversationRetrievalProgressEvent(
                    **common,
                    action_index=2,
                    phase=ConversationRetrievalProgressPhase.READ_COMPLETED,
                    transcript_turn_count=2,
                    transcript_event_count=4,
                    observation_utf8_bytes=654,
                    truncated=False,
                )
            )
            return ConversationRetrievalResult(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                agent_id=agent_id,
                content='{"answer":"界","ok":true}',
                context=_empty_context(),
                sources=(
                    ConversationRetrievalSource(
                        subject="Conversation source",
                        citation=ConversationCitation(
                            session_id,
                            "source_conversation",
                            None,
                            None,
                            None,
                        ),
                    ),
                    ConversationRetrievalSource(
                        subject="Event source",
                        citation=ConversationCitation(
                            session_id,
                            "source_conversation",
                            "source_turn",
                            7,
                            ConversationEventKind.AGENT_REPLY,
                        ),
                    ),
                ),
            )

        runtime.outcomes.append(outcome)
        (
            _workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            "question",
            "/quit",
            runtime=runtime,
        )
        self.assertEqual(controller.run_session_lobby(), 0)

        rendered = "".join(callbacks.records)
        expected_records = (
            "[context] Retrieval search action 1 started.\n",
            "[context] [completed]\n"
            "  Retrieval search action 1 completed: 2 hits; "
            "321 observation bytes; truncated: yes.\n",
            "[context] Retrieval read action 2 started.\n",
            "[context] [completed]\n"
            "  Retrieval read action 2 completed: 2 turns; 4 events; "
            "654 observation bytes; truncated: no.\n",
            '"answer": "界",\n'
            '                    "ok": true\n'
            '                  }\n',
            "[context] Source 1 [completed]\n"
            "  Subject: Conversation source\n"
            "  Conversation: source_conversation\n",
            "[context] Source 2 [completed]\n"
            "  Subject: Event source\n"
            "  Conversation: source_conversation\n"
            "  Turn: source_turn\n"
            "  Sequence: 7\n"
            "  Kind: agent_reply\n",
        )
        for record in expected_records:
            self.assertIn(record, rendered)
        self.assertEqual(
            [rendered.index(record) for record in expected_records],
            sorted(rendered.index(record) for record in expected_records),
        )
        self.assertEqual(runtime.memory_services[0].scope.session_id, "session")

    def test_retrieval_progress_rejects_identity_phase_and_pair_order(self) -> None:
        runtime = ScriptedRuntime()
        (
            _workspace,
            _catalog,
            entries,
            controller,
            _backend,
            _callbacks,
            _store,
        ) = self.prepared_selected_controller(runtime=runtime)
        conversation_id = entries[0].conversation_id
        common = {
            "session_id": "session",
            "conversation_id": conversation_id,
            "turn_id": "progress_turn",
            "agent_id": LEAD_ORCHESTRATOR,
            "action_index": 1,
        }
        events: list[ConversationRetrievalProgressEvent] = []
        invalid_first = ConversationRetrievalProgressEvent(
            **common,
            phase=ConversationRetrievalProgressPhase.READ_STARTED,
        )
        with self.assertRaisesRegex(ValueError, "begin with search"):
            controller._accept_conversation_retrieval_progress(
                invalid_first,
                expected_session_id="session",
                expected_conversation_id=conversation_id,
                expected_agent_id=LEAD_ORCHESTRATOR,
                events=events,
            )
        wrong_identity = ConversationRetrievalProgressEvent(
            **(common | {"session_id": "foreign_session"}),
            phase=ConversationRetrievalProgressPhase.SEARCH_STARTED,
        )
        with self.assertRaisesRegex(ValueError, "identity"):
            controller._accept_conversation_retrieval_progress(
                wrong_identity,
                expected_session_id="session",
                expected_conversation_id=conversation_id,
                expected_agent_id=LEAD_ORCHESTRATOR,
                events=events,
            )
        started = ConversationRetrievalProgressEvent(
            **common,
            phase=ConversationRetrievalProgressPhase.SEARCH_STARTED,
        )
        controller._accept_conversation_retrieval_progress(
            started,
            expected_session_id="session",
            expected_conversation_id=conversation_id,
            expected_agent_id=LEAD_ORCHESTRATOR,
            events=events,
        )
        with self.assertRaisesRegex(ValueError, "pair"):
            controller._accept_conversation_retrieval_progress(
                started,
                expected_session_id="session",
                expected_conversation_id=conversation_id,
                expected_agent_id=LEAD_ORCHESTRATOR,
                events=events,
            )
        self.assertEqual(events, [started])

    def test_parse_error_category_uses_original_shape_not_error_prose(self) -> None:
        workspace = self.make_workspace()
        runtime = ScriptedRuntime()
        lines = (
            "@opaque",
            "/to",
            "/agent\tbad",
            "/to\u00a0bad",
            "/To bad",
            "/unknown",
            "/quit",
        )
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            *lines,
            runtime=runtime,
        )

        def opaque_parser(line: str) -> InteractiveAction:
            if line == "/quit":
                return InteractiveAction(ActionKind.QUIT)
            return InteractiveAction(
                ActionKind.PARSE_ERROR,
                error="identical opaque parser copy",
            )

        with mock.patch.object(
            controller_core_module,
            "parse_interactive_line",
            side_effect=opaque_parser,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error agent_invalid]"), 3)
        self.assertEqual(rendered.count("[error command_invalid]"), 3)
        self.assertEqual(rendered.count("identical opaque parser copy"), 6)
        self.assertEqual(runtime.calls, [])

    def test_conversation_changes_preserve_sticky_and_session_switch_resets_it(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(workspace, session_id)
        save_default_session(workspace, "source")
        catalog = FilesystemConversationCatalog(workspace)
        source_a = catalog.create("source", "Source A")
        source_b = catalog.create("source", "Source B")
        destination = catalog.create("destination", "Destination")
        save_default_conversation(
            workspace,
            "source",
            source_a.conversation_id,
        )
        save_default_conversation(
            workspace,
            "destination",
            destination.conversation_id,
        )
        runtime = ScriptedRuntime()
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "/to character",
            "/conversation new Created",
            "/conversation subject Renamed",
            f"/conversation resume {source_a.conversation_id}",
            "/conversation archive",
            "yes",
            "/session resume destination",
            "/quit",
            catalog=catalog,
            runtime=runtime,
        )
        self.assertEqual(controller.run_session_lobby(), 0)

        main_prompts = [
            prompt for prompt in callbacks.prompts if prompt.startswith("story[")
        ]
        self.assertEqual(
            main_prompts,
            [
                "story[source:lead]> ",
                "story[source:character]> ",
                "story[source:character]> ",
                "story[source:character]> ",
                "story[source:character]> ",
                "story[source:character]> ",
                "story[destination:lead]> ",
            ],
        )
        self.assertEqual(
            load_default_conversation(workspace, "source"),
            source_b.conversation_id,
        )
        self.assertEqual(
            load_default_conversation(workspace, "destination"),
            destination.conversation_id,
        )
        self.assertEqual(runtime.calls, [])
        rendered = "".join(callbacks.records)
        self.assertIn("Subject: Renamed", rendered)
        self.assertIn("Session: destination (opened)", rendered)
        self.assertIn("Target: Lead Orchestrator", rendered)


class FailureAndResultTests(InteractiveConversationTestCase):
    """Verify type-only recovery, invalid result fatality, and cleanup."""

    def test_expected_typed_failures_are_safe_and_recover(self) -> None:
        cases = (
            (SessionIdError("secret"), InteractiveErrorCode.SESSION_INVALID),
            (SessionExistsError("secret"), InteractiveErrorCode.SESSION_EXISTS),
            (
                SessionNotFoundError("secret"),
                InteractiveErrorCode.SESSION_NOT_FOUND,
            ),
            (SessionAccessBlockedError("secret"), InteractiveErrorCode.SESSION_ACCESS_BLOCKED),
            (
                ConversationNotFoundError("secret"),
                InteractiveErrorCode.CONVERSATION_NOT_FOUND,
            ),
            (
                WorkspaceSecurityError("secret"),
                InteractiveErrorCode.WORKSPACE_UNSAFE,
            ),
            (
                WorkspaceFormatError("secret"),
                InteractiveErrorCode.WORKSPACE_INVALID,
            ),
            (OSError("secret"), InteractiveErrorCode.WORKSPACE_INVALID),
            (
                ConfigurationError("secret"),
                InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
            ),
            (
                BackendProtocolError("provider exited and timed out secret"),
                InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR,
            ),
            (
                ConversationRetrievalSemanticError(),
                InteractiveErrorCode.RETRIEVAL_INVALID,
            ),
            (
                ConversationRetrievalBudgetError(),
                InteractiveErrorCode.RETRIEVAL_BUDGET_EXHAUSTED,
            ),
        )
        for failure, expected_code in cases:
            with self.subTest(failure=type(failure).__name__):
                runtime = ScriptedRuntime(failure, "recovered reply")
                workspace = self.make_workspace()
                self.seed_session(workspace, "session")
                save_default_session(workspace, "session")
                catalog = FilesystemConversationCatalog(workspace)
                selected = catalog.create("session", "Selected")
                save_default_conversation(
                    workspace,
                    "session",
                    selected.conversation_id,
                )
                reads: list[object] = ["first"]
                if isinstance(failure, ConversationNotFoundError):
                    reads.append(
                        f"/conversation resume {selected.conversation_id}"
                    )
                reads.extend(("second", "/quit"))
                controller, _backend, callbacks, _store, _factory = (
                    self.make_controller(
                        workspace,
                        *reads,
                        catalog=catalog,
                        runtime=runtime,
                    )
                )
                self.assertEqual(controller.run_session_lobby(), 0)

                rendered = "".join(callbacks.records)
                self.assertIn(_error_record(expected_code), rendered)
                self.assertNotIn("secret", rendered)
                self.assertIn("lead [completed]> recovered reply\n", rendered)
                self.assertEqual(len(runtime.calls), 2)
                if isinstance(failure, BackendProtocolError) and type(
                    failure
                ) is BackendProtocolError:
                    self.assertNotIn("[error provider_exited]", rendered)
                    self.assertNotIn("[error provider_timed_out]", rendered)

    def test_invalid_or_mismatched_results_are_fatal_and_cleanup(self) -> None:
        def wrong_session(
            _session_id: str,
            conversation_id: str,
            agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            return _conversation_result(
                "foreign_session",
                conversation_id,
                agent_id,
                "foreign",
            )

        def wrong_conversation(
            session_id: str,
            _conversation_id: str,
            agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            return _conversation_result(
                session_id,
                "foreign_conversation",
                agent_id,
                "foreign",
            )

        def wrong_agent(
            session_id: str,
            conversation_id: str,
            _agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            return _conversation_result(
                session_id,
                conversation_id,
                PLOT_ARCHITECT,
                "foreign",
            )

        cases: tuple[tuple[str, object], ...] = (
            ("wrong type", object()),
            ("wrong session", wrong_session),
            ("wrong conversation", wrong_conversation),
            ("wrong agent", wrong_agent),
            ("retrieval observer", ConversationRetrievalObserverError()),
        )
        for label, outcome in cases:
            with self.subTest(case=label):
                runtime = ScriptedRuntime(outcome)
                (
                    workspace,
                    _catalog,
                    _entries,
                    controller,
                    backend,
                    callbacks,
                    _store,
                ) = self.prepared_selected_controller(
                    "message",
                    "/quit",
                    runtime=runtime,
                )
                with (
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        wraps=backend.release_session_access,
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
                self.assertEqual(callbacks.reads, ["/quit"])
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                    rendered,
                )
                self.assertNotIn("lead [completed]> foreign", rendered)
                self.assertEqual(len(runtime.calls), 1)
                lease = workspace.path(
                    "agent_states/orchestrator_state/provider_sessions/"
                    "session.access.json"
                )
                self.assertFalse(lease.exists())


class RealRetainedRuntimeTests(InteractiveConversationTestCase):
    """Exercise real StoryRuntime persistence on one retained Runner loop."""

    def bind_recording_agents(
        self,
        backend: AgentBackend,
        handler: ConversationHandler,
        definitions: list[AgentDefinition],
        requests: list[ConversationRetrievalRequest],
        loops: list[asyncio.AbstractEventLoop],
        tasks: list[asyncio.Task[object] | None],
    ) -> Callable[[AgentDefinition], Awaitable[RecordingConversationBound]]:
        async def bind(definition: AgentDefinition) -> RecordingConversationBound:
            definitions.append(definition)
            return RecordingConversationBound(
                definition,
                handler,
                requests,
                loops,
                tasks,
            )

        return bind

    def test_repeated_turns_share_context_isolate_conversations_and_artifacts(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        first = catalog.create("session", "First room")
        second = catalog.create("session", "Second room")
        save_default_conversation(
            workspace,
            "session",
            first.conversation_id,
        )
        backend = self.make_backend(workspace)
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "first",
            "@plot second",
            "/conversation resume",
            "2",
            "@character third",
            f"/conversation resume {first.conversation_id}",
            "/to execution",
            "/say /run literal",
            "/quit",
            backend=backend,
            catalog=catalog,
            runtime=_REAL_RUNTIME,
        )
        definitions: list[AgentDefinition] = []
        requests: list[ConversationRetrievalRequest] = []
        loops: list[asyncio.AbstractEventLoop] = []
        tasks: list[asyncio.Task[object] | None] = []

        def handler(request: ConversationRetrievalRequest) -> ConversationReply:
            return ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                f"reply:{request.agent_id}:{request.content}",
            )

        bind = self.bind_recording_agents(
            backend,
            handler,
            definitions,
            requests,
            loops,
            tasks,
        )
        core_before = core_snapshot(workspace)
        manuscript_before = {
            path.relative_to(workspace.root).as_posix(): path.read_bytes()
            for path in workspace.path("manuscript").rglob("*")
            if path.is_file()
        }
        with (
            mock.patch.object(backend, "bind", new=bind),
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
            ) as run_boundary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        run_boundary.assert_not_called()
        self.assertEqual(len(requests), 4)
        self.assertEqual(len({id(loop) for loop in loops}), 1)
        self.assertTrue(all(loop.is_closed() for loop in loops))
        self.assertTrue(all(task is not None and task.done() for task in tasks))
        self.assertEqual(
            [
                (request.conversation_id, request.agent_id, request.content)
                for request in requests
            ],
            [
                (first.conversation_id, LEAD_ORCHESTRATOR, "first"),
                (first.conversation_id, PLOT_ARCHITECT, "second"),
                (second.conversation_id, CHARACTER_CUSTODIAN, "third"),
                (first.conversation_id, EXECUTION_SPECIALIST, "/run literal"),
            ],
        )
        self.assertEqual(requests[0].context.events, ())
        self.assertEqual(
            tuple(event.content for event in requests[1].context.events),
            (
                "first",
                f"reply:{LEAD_ORCHESTRATOR}:first",
            ),
        )
        self.assertEqual(requests[2].context.events, ())
        self.assertEqual(
            tuple(event.content for event in requests[3].context.events),
            (
                "first",
                f"reply:{LEAD_ORCHESTRATOR}:first",
                "second",
                f"reply:{PLOT_ARCHITECT}:second",
            ),
        )

        store = FilesystemConversationEventStore(workspace, catalog=catalog)
        first_events = store.read("session", first.conversation_id)
        second_events = store.read("session", second.conversation_id)
        self.assertEqual(
            tuple(event.sequence for event in first_events),
            (1, 2, 3, 4, 5, 6),
        )
        self.assertEqual(
            tuple(event.kind for event in first_events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            )
            * 3,
        )
        self.assertEqual(
            tuple(event.recipient for event in first_events[::2]),
            (LEAD_ORCHESTRATOR, PLOT_ARCHITECT, EXECUTION_SPECIALIST),
        )
        self.assertEqual(
            tuple(event.sequence for event in second_events),
            (1, 2),
        )
        for events in (first_events, second_events):
            for user, outcome in zip(events[::2], events[1::2], strict=True):
                self.assertEqual(user.turn_id, outcome.turn_id)

        self.assertEqual(core_snapshot(workspace), core_before)
        self.assertEqual(
            {
                path.relative_to(workspace.root).as_posix(): path.read_bytes()
                for path in workspace.path("manuscript").rglob("*")
                if path.is_file()
            },
            manuscript_before,
        )
        protected_unexpected = [
            path
            for path in workspace.root.rglob("*")
            if path.is_file()
            and (
                "debates" in path.parts
                or "search" in path.name.casefold()
                or path.name.endswith((".result.json", ".state.json"))
            )
        ]
        self.assertEqual(protected_unexpected, [])
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            first.conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            f"execution [completed]> reply:{EXECUTION_SPECIALIST}:/run literal",
            rendered,
        )
        self.assertNotIn("[run ", rendered)

    def test_real_runtime_persists_sanitized_failure_then_recovers(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        selected = catalog.create("session", "Failure room")
        save_default_conversation(
            workspace,
            "session",
            selected.conversation_id,
        )
        backend = self.make_backend(workspace)
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            "sensitive user text",
            "second",
            "/quit",
            backend=backend,
            catalog=catalog,
            runtime=_REAL_RUNTIME,
        )
        requests: list[ConversationRetrievalRequest] = []
        definitions: list[AgentDefinition] = []
        loops: list[asyncio.AbstractEventLoop] = []
        tasks: list[asyncio.Task[object] | None] = []

        def handler(
            request: ConversationRetrievalRequest,
        ) -> ConversationReply | BaseException:
            if request.content == "sensitive user text":
                return BackendProtocolError(
                    f"{workspace.root}\x1b provider diagnostic"
                )
            return ConversationReply(
                request.session_id,
                request.conversation_id,
                request.turn_id,
                "recovered",
            )

        bind = self.bind_recording_agents(
            backend,
            handler,
            definitions,
            requests,
            loops,
            tasks,
        )
        with mock.patch.object(backend, "bind", new=bind):
            self.assertEqual(controller.run_session_lobby(), 0)

        store = FilesystemConversationEventStore(workspace, catalog=catalog)
        events = store.read("session", selected.conversation_id)
        self.assertEqual(
            tuple(event.kind for event in events),
            (
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.RUNTIME_ERROR,
                ConversationEventKind.USER_MESSAGE,
                ConversationEventKind.AGENT_REPLY,
            ),
        )
        failure_text = events[1].content
        self.assertIn("BackendProtocolError", failure_text)
        self.assertIn("<workspace>", failure_text)
        self.assertNotIn(str(workspace.root), failure_text)
        self.assertNotIn("\x1b", failure_text)
        self.assertNotIn("sensitive user text", failure_text)
        self.assertEqual(
            tuple(event.content for event in requests[1].context.events),
            ("sensitive user text", failure_text),
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR),
            rendered,
        )
        self.assertNotIn("provider diagnostic", rendered)
        self.assertIn("lead [completed]> recovered\n", rendered)
        self.assertEqual(len({id(loop) for loop in loops}), 1)
        self.assertTrue(all(loop.is_closed() for loop in loops))
        self.assertTrue(all(task is not None and task.done() for task in tasks))


if __name__ == "__main__":
    unittest.main()
