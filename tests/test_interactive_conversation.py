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
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
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
from story_writing_agents.conversation_storage import (
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
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
_REPLY_ACCENTS = {
    LEAD_ORCHESTRATOR: terminal_module.SpeakerAccent.CYAN,
    PLOT_ARCHITECT: terminal_module.SpeakerAccent.YELLOW,
    CHARACTER_CUSTODIAN: terminal_module.SpeakerAccent.GREEN,
    EXECUTION_SPECIALIST: terminal_module.SpeakerAccent.BLUE,
}


def _reply_header(agent_id: str) -> str:
    return f"{AGENT_SPECS[agent_id].display_name} → You [completed]\n"


def _expected_reply_message(
    agent_id: str,
    content: str,
    *,
    metadata: str | None = None,
) -> terminal_module.Message:
    """Return one exact speaker-rail reply component."""

    return terminal_module.Message(
        f"{AGENT_SPECS[agent_id].display_name} → You",
        content,
        role=terminal_module.SemanticRole.AGENT,
        state=terminal_module.ViewState.COMPLETED,
        presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
        metadata=metadata,
        accent=_REPLY_ACCENTS[agent_id],
    )


def _capture_semantic_method(
    method_name: str,
    components: list[object],
) -> Any:
    """Patch one slotted semantic-output method while retaining real behavior."""

    method = getattr(terminal_module.InteractiveSemanticOutput, method_name)

    def capture(output: object, component: object) -> object:
        components.append(component)
        return method(output, component)

    return mock.patch.object(
        terminal_module.InteractiveSemanticOutput,
        method_name,
        autospec=True,
        side_effect=capture,
    )


class ScriptedTerminalCallbacks:
    """Record exact terminal traffic and return scripted lines or events."""

    def __init__(self, *reads: object) -> None:
        self.reads = list(reads)
        self.prompts: list[str] = []
        self.records: list[str] = []

    def read_line(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Manuscript output" in prompt:
            return f"part-01/interactive-{len(self.prompts)}.md"
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
        self.rag_queries: list[str | None] = []
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
        rag_query: str | None = None,
    ) -> object:
        """Return one scripted retrieval outcome and record selected scope."""

        self.calls.append((session_id, conversation_id, agent_id, text))
        self.loops.append(asyncio.get_running_loop())
        self.tasks.append(asyncio.current_task())
        self.memory_services.append(memory_service)
        self.progress_sinks.append(progress_sink)
        self.rag_queries.append(rag_query)
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
    reference_operation_id: str | None = None,
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
        reference_operation_id=reference_operation_id,
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
        storage_services: ConversationStorageServices | None = None
        if catalog is not None:
            keywords["conversation_catalog"] = catalog
        elif runtime is not _REAL_RUNTIME:
            storage_services = compose_filesystem_conversation_storage(workspace)
            keywords["conversation_storage_services"] = storage_services

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
        if storage_services is not None:
            self.assertIs(
                call.kwargs["conversation_storage_services"],
                storage_services,
            )
            self.assertIs(
                controller._conversation_storage_services,
                storage_services,
            )
            store = storage_services.event_store
        else:
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
        before = tuple(workspace.root.rglob("*"))
        controller = InteractiveController(
            workspace,
            CliContext(),
            backend,
            terminal,
        )
        storage_services = controller._conversation_storage_services
        self.assertIsInstance(storage_services, ConversationStorageServices)
        assert storage_services is not None
        self.assertIs(controller._conversation_catalog, storage_services.catalog)
        self.assertIs(
            controller._conversation_event_store,
            storage_services.event_store,
        )
        self.assertIs(
            controller._runtime.conversation_storage_services,
            storage_services,
        )
        self.assertEqual(tuple(workspace.root.rglob("*")), before)

        injected = FilesystemConversationCatalog(workspace)
        runtime = ScriptedRuntime()
        with mock.patch.object(
            controller_core_module,
            "StoryRuntime",
            return_value=runtime,
        ) as runtime_factory:
            controller = InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
                conversation_catalog=injected,
            )
        runtime_factory.assert_called_once()
        injected_store = runtime_factory.call_args.kwargs[
            "conversation_event_store"
        ]
        self.assertIs(injected_store.catalog, injected)
        self.assertIsNone(controller._conversation_storage_services)
        self.assertIs(controller._conversation_catalog, injected)
        self.assertIs(controller._conversation_event_store, injected_store)
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
        accepted_components: list[object] = []
        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            mock.patch.object(backend, "validate_provider_configuration") as validate_configuration,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
            ) as run_boundary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(runtime.calls, [])
        self.assertEqual(accepted_components, [])
        run_boundary.assert_not_called()
        validate_configuration.assert_not_called()
        bind.assert_not_called()
        rendered = "".join(callbacks.records)
        for agent_id in ROSTER:
            self.assertEqual(rendered.count(f"[{agent_id}] "), 1)
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
        self.assertEqual(
            roster.count(
                "Execution [info]: Codex [built in default] · "
                "model provider default [provider default] · "
                "effort provider default [provider default]"
            ),
            4,
        )
        self.assertEqual(roster.count("Confirmation [warning]: pending"), 4)
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
        accepted_components: list[object] = []
        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
                side_effect=real_run,
            ) as run_boundary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(runtime.calls, [])
        self.assertEqual(accepted_components, [])
        run_boundary.assert_not_called()
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error conversation_required]"), 3)
        for agent_id in ROSTER:
            self.assertEqual(rendered.count(f"[{agent_id}] "), 1)
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


class ControllerRagWorkflowTests(InteractiveConversationTestCase):
    """Verify private one-turn RAG arming, scope, and consumption lifecycle."""

    def test_rag_arm_requires_attachment_and_active_conversation(self) -> None:
        secret = "PRIVATE_RAG_QUERY_MUST_NOT_RENDER"

        workspace = self.make_workspace()
        runtime = ScriptedRuntime()
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            f"/rag {secret}",
            "/quit",
            runtime=runtime,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.rag_queries, [])
        self.assertIsNone(controller._pending_rag_query)
        rendered = "".join(callbacks.records)
        self.assertIn("[error session_not_attached]", rendered)
        self.assertNotIn(secret, rendered)

        workspace = self.make_workspace()
        self.seed_session(workspace, "session")
        save_default_session(workspace, "session")
        catalog = FilesystemConversationCatalog(workspace)
        runtime = ScriptedRuntime()
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            f"/rag {secret}",
            "/quit",
            catalog=catalog,
            runtime=runtime,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.rag_queries, [])
        self.assertIsNone(controller._pending_rag_query)
        rendered = "".join(callbacks.records)
        self.assertIn("[error conversation_required]", rendered)
        self.assertNotIn(secret, rendered)

    def test_rag_rearm_is_private_with_details_and_each_turn_consumes_once(
        self,
    ) -> None:
        cases = (
            ("ordinary", "visible ordinary turn", LEAD_ORCHESTRATOR),
            ("say", "/say visible literal turn", LEAD_ORCHESTRATOR),
            ("routed", "@plot visible routed turn", PLOT_ARCHITECT),
        )
        for label, eligible, expected_agent in cases:
            with self.subTest(kind=label):
                private = f"PRIVATE_RAG_{label.upper()}_SENTINEL"
                superseded = f"SUPERSEDED_RAG_{label.upper()}_SENTINEL"
                script = (
                    f"/rag {superseded}",
                    "/status",
                    f"/rag {private}",
                    "/help",
                    eligible,
                    "visible follow-up turn",
                    "/quit",
                )
                runtime = ScriptedRuntime("first reply", "second reply")
                (
                    workspace,
                    _catalog,
                    entries,
                    controller,
                    _backend,
                    callbacks,
                    _store,
                ) = self.prepared_selected_controller(
                    *script,
                    runtime=runtime,
                )
                controller._show_message_details = True

                self.assertEqual(controller.run_session_lobby(), 0)

                self.assertTrue(controller._show_message_details)
                self.assertEqual(runtime.rag_queries, [private, None])
                self.assertEqual(len(runtime.calls), 2)
                self.assertEqual(runtime.calls[0][2], expected_agent)
                self.assertEqual(runtime.calls[1][2], LEAD_ORCHESTRATOR)
                self.assertTrue(
                    all(
                        private not in text and superseded not in text
                        for _session, _conversation, _agent, text in runtime.calls
                    )
                )
                self.assertIsNone(controller._pending_rag_query)
                rendered = "".join(callbacks.records)
                prompts = "".join(callbacks.prompts)
                self.assertEqual(
                    rendered.count(
                        "Conversation RAG is armed for the next ordinary, /say, "
                        "or routed story turn in this conversation."
                    ),
                    2,
                )
                for sentinel in (private, superseded):
                    self.assertNotIn(sentinel, rendered)
                    self.assertNotIn(sentinel, prompts)
                    sentinel_bytes = sentinel.encode("utf-8")
                    self.assertFalse(
                        any(
                            sentinel_bytes in path.read_bytes()
                            for path in workspace.root.rglob("*")
                            if path.is_file()
                        )
                    )
                self.assertIn(private, script[2])
                self.assertEqual(runtime.calls[0][1], entries[0].conversation_id)

    def test_rag_query_is_consumed_by_entered_failure_before_next_turn(
        self,
    ) -> None:
        private = "PRIVATE_RAG_FAILURE_SENTINEL"
        runtime = ScriptedRuntime(
            ConversationRetrievalSemanticError(),
            "recovered reply",
        )
        (
            _workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            f"/rag {private}",
            "first visible turn",
            "second visible turn",
            "/quit",
            runtime=runtime,
        )

        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.rag_queries, [private, None])
        self.assertIsNone(controller._pending_rag_query)
        rendered = "".join(callbacks.records)
        self.assertIn("[error retrieval_invalid]", rendered)
        self.assertNotIn(private, rendered)

    def test_rag_query_clears_when_attachment_scope_changes(self) -> None:
        private = "PRIVATE_RAG_SCOPE_SENTINEL"
        workspace = self.make_workspace()
        self.seed_session(workspace, "source")
        self.seed_session(workspace, "destination")
        save_default_session(workspace, "source")
        catalog = FilesystemConversationCatalog(workspace)
        source = catalog.create("source", "Source focus")
        destination = catalog.create("destination", "Destination focus")
        save_default_conversation(workspace, "source", source.conversation_id)
        save_default_conversation(
            workspace,
            "destination",
            destination.conversation_id,
        )
        runtime = ScriptedRuntime("destination reply")
        controller, _backend, callbacks, _store, _factory = self.make_controller(
            workspace,
            f"/rag {private}",
            "/session resume destination",
            "visible destination turn",
            "/quit",
            catalog=catalog,
            runtime=runtime,
        )

        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.rag_queries, [None])
        self.assertEqual(runtime.calls[0][0], "destination")
        self.assertIsNone(controller._pending_rag_query)
        self.assertNotIn(private, "".join(callbacks.records))

    def test_rag_query_clears_when_attached_session_is_paused(self) -> None:
        private = "PRIVATE_RAG_PAUSE_SENTINEL"
        runtime = ScriptedRuntime()
        retained: dict[str, InteractiveController] = {}

        def verify_clear(_prompt: str) -> str:
            self.assertIsNone(retained["controller"]._pending_rag_query)
            return "/quit"

        (
            _workspace,
            _catalog,
            _entries,
            controller,
            _backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            f"/rag {private}",
            "/session pause",
            "yes",
            verify_clear,
            runtime=runtime,
        )
        retained["controller"] = controller

        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(runtime.rag_queries, [])
        self.assertNotIn(private, "".join(callbacks.records))


class ControllerRoutingTests(InteractiveConversationTestCase):
    """Verify exact routing, sticky state, output, and parse categories."""

    def test_runtime_input_header_tracks_only_an_exact_leading_one_shot_route(
        self,
    ) -> None:
        workspace = self.make_workspace()
        agent_configs = workspace.read_json("config/agent_configs.json")
        agent_configs[PLOT_ARCHITECT].update(
            {
                "backend": "antigravity",
                "antigravity_model": "agy-live",
                "antigravity_effort": "high",
            }
        )
        workspace.write_json("config/agent_configs.json", agent_configs)
        backend = self.make_backend(workspace)
        controller, _backend, _callbacks, _store, _factory = self.make_controller(
            workspace,
            "/quit",
            backend=backend,
        )

        self.assertEqual(
            controller._main_runtime_header("", 0),
            terminal_module.Notice(
                "Attach a story session to activate an agent execution profile.",
                label="active runtime",
                role=terminal_module.SemanticRole.MUTED,
                state=terminal_module.ViewState.EMPTY,
            ),
        )
        controller._attached_session_id = "session"
        active = terminal_module.Notice(
            "Lead Orchestrator",
            label="active runtime",
            role=terminal_module.SemanticRole.INFO,
            state=terminal_module.ViewState.COMPLETED,
        )
        one_shot = terminal_module.Notice(
            "Plot & Structure Architect",
            label="one-shot runtime",
            role=terminal_module.SemanticRole.INFO,
            state=terminal_module.ViewState.COMPLETED,
        )
        with (
            mock.patch.object(backend, "execution_profile") as profile,
            mock.patch.object(
                backend,
                "execution_profile_confirmation",
            ) as confirmation,
        ):
            for buffer in (
                "",
                "@",
                "@p",
                "@pl",
                "@plo",
                "@plot_",
                "prefix @plot",
                "/say @plot",
            ):
                with self.subTest(buffer=buffer, route="sticky", details=False):
                    self.assertEqual(
                        controller._main_runtime_header(buffer, len(buffer)),
                        active,
                    )
            for buffer in ("@plot", "@plot message", "@plot\tmessage"):
                with self.subTest(
                    buffer=buffer,
                    route="one-shot",
                    details=False,
                ):
                    self.assertEqual(
                        controller._main_runtime_header(buffer, len(buffer)),
                        one_shot,
                    )
            profile.assert_not_called()
            confirmation.assert_not_called()

        controller._show_message_details = True
        self.assertEqual(
            controller._main_runtime_header("", 0),
            terminal_module.Notice(
                "Lead Orchestrator · Codex · model provider default · "
                "effort provider default · pending",
                label="active runtime",
                role=terminal_module.SemanticRole.WARNING,
                state=terminal_module.ViewState.LOADING,
            ),
        )
        self.assertEqual(
            controller._main_runtime_header("@plot message", 5),
            terminal_module.Notice(
                "Plot & Structure Architect · Antigravity · model agy-live · "
                "effort high · pending",
                label="one-shot runtime",
                role=terminal_module.SemanticRole.WARNING,
                state=terminal_module.ViewState.LOADING,
            ),
        )

        with mock.patch.object(
            backend,
            "execution_profile_confirmation",
            return_value="confirmed",
        ):
            confirmed = controller._main_runtime_header("@plot", 5)
        self.assertEqual(confirmed.label, "one-shot runtime")
        self.assertIn(" · confirmed", confirmed.text)
        self.assertIs(confirmed.role, terminal_module.SemanticRole.INFO)
        self.assertIs(confirmed.state, terminal_module.ViewState.COMPLETED)

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
            "ordinary ${core_documents/global_outline.md}",
            "@plot routed ${core_documents/character_sheets.md}#L2-L2",
            "/to character",
            "sticky ${core_documents/narrative_rules.md}",
            "/agent lead",
            "/say /run is literal ${manuscript/part-01/seed.md}",
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
        accepted_components: list[object] = []
        semantic_components: list[object] = []
        scope_calls: list[dict[str, object]] = []
        real_write_scope = terminal_module.InteractiveTerminal.write_conversation_scope

        def capture_scope(output: object, **kwargs: object) -> None:
            scope_calls.append(dict(kwargs))
            real_write_scope(output, **kwargs)

        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            _capture_semantic_method(
                "write_component",
                semantic_components,
            ),
            mock.patch.object(
                terminal_module.InteractiveTerminal,
                "write_conversation_scope",
                autospec=True,
                side_effect=capture_scope,
            ),
            mock.patch.object(store, "read_context") as read_context,
            mock.patch.object(store, "append") as append,
            mock.patch.object(
                backend,
                "validate_provider_configuration",
                wraps=backend.validate_provider_configuration,
            ) as validate_configuration,
            mock.patch.object(backend, "execution_profile") as execution_profile,
            mock.patch.object(
                backend,
                "execution_profile_confirmation",
            ) as profile_confirmation,
            mock.patch.object(
                backend,
                "execution_profile_thread_status",
            ) as profile_thread,
            mock.patch.object(backend, "bind") as bind,
            mock.patch.object(
                controller_core_module.asyncio,
                "run",
                side_effect=real_run,
            ) as run_boundary,
        ):
            accepted_counts_at_runtime: list[int] = []
            runtime.on_call = lambda: accepted_counts_at_runtime.append(
                len(accepted_components)
            )
            self.assertEqual(controller.run_session_lobby(), 0)

        conversation_id = entries[0].conversation_id
        scope = {
            "session_id": "session",
            "conversation_handle": conversation_id[:12],
            "conversation_subject": "Selected subject",
        }
        self.assertEqual(
            scope_calls,
            [
                {**scope, "agent_id": LEAD_ORCHESTRATOR},
                {**scope, "agent_id": CHARACTER_CUSTODIAN},
                {**scope, "agent_id": LEAD_ORCHESTRATOR},
            ],
        )
        self.assertEqual(
            runtime.calls,
            [
                (
                    "session",
                    conversation_id,
                    LEAD_ORCHESTRATOR,
                    "ordinary ${core_documents/global_outline.md}",
                ),
                (
                    "session",
                    conversation_id,
                    PLOT_ARCHITECT,
                    "routed ${core_documents/character_sheets.md}#L2-L2",
                ),
                (
                    "session",
                    conversation_id,
                    CHARACTER_CUSTODIAN,
                    "sticky ${core_documents/narrative_rules.md}",
                ),
                (
                    "session",
                    conversation_id,
                    LEAD_ORCHESTRATOR,
                    "/run is literal ${manuscript/part-01/seed.md}",
                ),
            ],
        )
        self.assertEqual(accepted_counts_at_runtime, [1, 2, 3, 4])
        self.assertEqual(
            accepted_components,
            [
                terminal_module.Message(
                    "You → Lead Orchestrator",
                    "ordinary ${core_documents/global_outline.md}",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
                terminal_module.Message(
                    "You → Plot & Structure Architect",
                    "routed ${core_documents/character_sheets.md}#L2-L2",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
                terminal_module.Message(
                    "You → Character & World Custodian",
                    "sticky ${core_documents/narrative_rules.md}",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
                terminal_module.Message(
                    "You → Lead Orchestrator",
                    "/run is literal ${manuscript/part-01/seed.md}",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                ),
            ],
        )
        replies = [
            component
            for component in semantic_components
            if type(component) is terminal_module.Message
            and component.role is terminal_module.SemanticRole.AGENT
        ]
        self.assertEqual(
            [message.sender for message in replies],
            [
                "Lead Orchestrator → You",
                "Plot & Structure Architect → You",
                "Character & World Custodian → You",
                "Lead Orchestrator → You",
            ],
        )
        self.assertEqual(
            [message.content for message in replies],
            [
                "lead reply\x1b",
                "plot reply",
                "character reply",
                "literal reply",
            ],
        )
        for record, agent_id in zip(
            replies,
            (
                LEAD_ORCHESTRATOR,
                PLOT_ARCHITECT,
                CHARACTER_CUSTODIAN,
                LEAD_ORCHESTRATOR,
            ),
            strict=True,
        ):
            with self.subTest(agent_id=agent_id):
                self.assertEqual(
                    record,
                    _expected_reply_message(agent_id, record.content),
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
        execution_profile.assert_not_called()
        profile_confirmation.assert_not_called()
        profile_thread.assert_not_called()
        bind.assert_not_called()
        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )
        self.assertFalse(
            workspace.path(_transcript_relative("session", conversation_id)).exists()
        )

        rendered = "".join(callbacks.records)
        self.assertNotIn("You →", rendered)
        self.assertNotIn("[routing] One-shot route", rendered)
        self.assertNotIn("[waiting] ", rendered)
        self.assertIn(_reply_header(LEAD_ORCHESTRATOR), rendered)
        self.assertIn("│ lead reply\\x1b\n", rendered)
        for diagnostic in (
            "Provider:",
            "Model:",
            "Effort:",
            "Confirmation:",
            "Thread:",
        ):
            self.assertNotIn(diagnostic, rendered)
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

    def test_details_on_reply_has_exact_five_diagnostics_and_full_body(
        self,
    ) -> None:
        raw_reply = '{"answer":"界","items":[1,true]}'
        runtime = ScriptedRuntime(raw_reply)
        (
            _workspace,
            _catalog,
            _entries,
            controller,
            backend,
            callbacks,
            _store,
        ) = self.prepared_selected_controller(
            "question body",
            "/quit",
            runtime=runtime,
        )
        controller._show_message_details = True
        accepted_components: list[object] = []
        semantic_components: list[object] = []
        real_profile = backend.execution_profile
        real_confirmation = backend.execution_profile_confirmation
        real_thread = backend.execution_profile_thread_status
        with (
            _capture_semantic_method(
                "write_accepted_input",
                accepted_components,
            ),
            _capture_semantic_method(
                "write_component",
                semantic_components,
            ),
            mock.patch.object(
                backend,
                "execution_profile",
                wraps=real_profile,
            ) as profile,
            mock.patch.object(
                backend,
                "execution_profile_confirmation",
                wraps=real_confirmation,
            ) as confirmation,
            mock.patch.object(
                backend,
                "execution_profile_thread_status",
                wraps=real_thread,
            ) as thread,
        ):
            runtime.on_call = lambda: (
                self.assertEqual(len(accepted_components), 1),
                confirmation.assert_not_called(),
                thread.assert_not_called(),
            )
            self.assertEqual(controller.run_session_lobby(), 0)

        expected_body = (
            "{\n"
            '  "answer": "界",\n'
            '  "items": [\n'
            "    1,\n"
            "    true\n"
            "  ]\n"
            "}"
        )
        expected_metadata = (
            "Provider: Codex [built in default] · "
            "Model: provider default [provider default] · "
            "Effort: provider default [provider default] · "
            "Confirmation: pending · Thread: not observed"
        )
        self.assertEqual(
            accepted_components,
            [
                terminal_module.Message(
                    "You → Lead Orchestrator",
                    "question body",
                    state=terminal_module.ViewState.COMPLETED,
                    presentation=terminal_module.MessagePresentation.CHAT_BLOCK,
                    accent=terminal_module.SpeakerAccent.MAGENTA,
                )
            ],
        )
        replies = [
            component
            for component in semantic_components
            if type(component) is terminal_module.Message
            and component.role is terminal_module.SemanticRole.AGENT
        ]
        self.assertEqual(
            replies,
            [
                _expected_reply_message(
                    LEAD_ORCHESTRATOR,
                    expected_body,
                    metadata=expected_metadata,
                )
            ],
        )
        self.assertEqual(expected_metadata.split(" · "), [
            "Provider: Codex [built in default]",
            "Model: provider default [provider default]",
            "Effort: provider default [provider default]",
            "Confirmation: pending",
            "Thread: not observed",
        ])
        profile.assert_called_once_with(LEAD_ORCHESTRATOR)
        confirmation.assert_called_once_with(LEAD_ORCHESTRATOR)
        thread.assert_called_once_with(LEAD_ORCHESTRATOR)
        rendered = "".join(callbacks.records)
        self.assertNotIn("You → Lead Orchestrator", rendered)
        self.assertIn('│   "answer": "界",\n', rendered)
        self.assertIn("│     true\n", rendered)
        for hidden in ("Sequence:", "Turn:", "Kind:", "IDs:"):
            self.assertNotIn(hidden, expected_metadata)

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
        reply_index = rendered.index(_reply_header(LEAD_ORCHESTRATOR))
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
            '│   "answer": "界",\n'
            '│   "items": [\n'
            "│     1,\n"
            "│     true\n",
            rendered,
        )
        self.assertNotIn(
            '│ {"answer":"界", "items":[1, true]}',
            rendered,
        )
        self.assertNotIn("\x1b", rendered)

    def test_reference_operation_identity_is_validated_but_not_presented(self) -> None:
        operation_id = "rr_07_session_" + "a" * 32

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
                "safe reply",
                reference_operation_id=operation_id,
            )

        runtime = ScriptedRuntime(result)
        (_workspace, _catalog, _entries, controller, _backend, callbacks, _store) = (
            self.prepared_selected_controller("message", "/quit", runtime=runtime)
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        self.assertIn(_reply_header(LEAD_ORCHESTRATOR), rendered)
        self.assertIn("│ safe reply\n", rendered)
        self.assertNotIn(operation_id, rendered)

    def test_retrieval_progress_sources_and_selected_scope_render_exactly(self) -> None:
        runtime = ScriptedRuntime()
        turn_id = "retrieval_turn"
        sources = (
            ConversationRetrievalSource(
                subject="Conversation source",
                citation=ConversationCitation(
                    "session",
                    "source_conversation",
                    None,
                    None,
                    None,
                ),
            ),
            ConversationRetrievalSource(
                subject="Event source",
                citation=ConversationCitation(
                    "session",
                    "source_conversation",
                    "source_turn",
                    7,
                    ConversationEventKind.AGENT_REPLY,
                ),
            ),
        )

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
                sources=sources,
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
        semantic_components: list[object] = []
        with _capture_semantic_method(
            "write_component",
            semantic_components,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
            rendered = "".join(callbacks.records)
            on_start = len(semantic_components)
            controller._show_message_details = True
            controller._render_conversation_retrieval_sources(sources)

        expected_records = (
            "[context] Retrieval search action 1 started.\n",
            "[context] [completed]\n"
            "  Retrieval search action 1 completed: 2 hits; "
            "321 observation bytes; truncated: yes.\n",
            "[context] Retrieval read action 2 started.\n",
            "[context] [completed]\n"
            "  Retrieval read action 2 completed: 2 turns; 4 events; "
            "654 observation bytes; truncated: no.\n",
            '│   "answer": "界",\n'
            '│   "ok": true\n'
            "│ }\n",
            "[context] Source 1 [completed]\n"
            "  Subject: Conversation source\n"
            "  Conversation: source_conversation\n",
            "[context] Source 2 [completed]\n"
            "  Subject: Event source\n"
            "  Conversation: source_conversation\n"
            "  Sequence: 7\n",
        )
        for record in expected_records:
            self.assertIn(record, rendered)
        self.assertEqual(
            [rendered.index(record) for record in expected_records],
            sorted(rendered.index(record) for record in expected_records),
        )
        source_records = [
            component
            for component in semantic_components[:on_start]
            if type(component) is terminal_module.Record
            and component.title is not None
            and component.title.startswith("Source ")
        ]
        self.assertEqual(
            source_records,
            [
                terminal_module.Record(
                    "context",
                    title="Source 1",
                    fields=(
                        terminal_module.Field("Subject", "Conversation source"),
                        terminal_module.Field(
                            "Conversation",
                            "source_conversation",
                        ),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
                terminal_module.Record(
                    "context",
                    title="Source 2",
                    fields=(
                        terminal_module.Field("Subject", "Event source"),
                        terminal_module.Field(
                            "Conversation",
                            "source_conversation",
                        ),
                        terminal_module.Field("Sequence", "7"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
            ],
        )
        self.assertEqual(
            semantic_components[on_start:],
            [
                source_records[0],
                terminal_module.Record(
                    "context",
                    title="Source 2",
                    fields=(
                        terminal_module.Field("Subject", "Event source"),
                        terminal_module.Field(
                            "Conversation",
                            "source_conversation",
                        ),
                        terminal_module.Field("Turn", "source_turn"),
                        terminal_module.Field("Sequence", "7"),
                        terminal_module.Field("Kind", "agent_reply"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
            ],
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
                accepted_components: list[object] = []
                with _capture_semantic_method(
                    "write_accepted_input",
                    accepted_components,
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)

                rendered = "".join(callbacks.records)
                self.assertIn(_error_record(expected_code), rendered)
                self.assertNotIn("secret", rendered)
                self.assertIn(_reply_header(LEAD_ORCHESTRATOR), rendered)
                self.assertIn("│ recovered reply\n", rendered)
                self.assertEqual(len(runtime.calls), 2)
                self.assertEqual(
                    [component.content for component in accepted_components],
                    ["first", "second"],
                )
                self.assertTrue(
                    all(
                        component.sender == "You → Lead Orchestrator"
                        for component in accepted_components
                    )
                )
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

        def foreign_reference(
            session_id: str,
            conversation_id: str,
            agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            return _conversation_result(
                session_id,
                conversation_id,
                agent_id,
                "foreign",
                reference_operation_id=(
                    "rr_11_different_session_" + "a" * 32
                ),
            )

        def malformed_reference(
            session_id: str,
            conversation_id: str,
            agent_id: str,
            _text: str,
        ) -> ConversationRetrievalResult:
            return _conversation_result(
                session_id,
                conversation_id,
                agent_id,
                "foreign",
                reference_operation_id="rr_malformed",
            )

        cases: tuple[tuple[str, object], ...] = (
            ("wrong type", object()),
            ("wrong session", wrong_session),
            ("wrong conversation", wrong_conversation),
            ("wrong agent", wrong_agent),
            ("foreign reference", foreign_reference),
            ("malformed reference", malformed_reference),
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
                accepted_components: list[object] = []
                with (
                    _capture_semantic_method(
                        "write_accepted_input",
                        accepted_components,
                    ),
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
                self.assertEqual(
                    [component.content for component in accepted_components],
                    ["message"],
                )
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(InteractiveErrorCode.INTERNAL_ERROR),
                    rendered,
                )
                self.assertNotIn("│ foreign", rendered)
                self.assertNotIn("rr_", rendered)
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
        semantic_components: list[object] = []
        manuscript_before = {
            path.relative_to(workspace.root).as_posix(): path.read_bytes()
            for path in workspace.path("manuscript").rglob("*")
            if path.is_file()
        }
        with (
            _capture_semantic_method(
                "write_component",
                semantic_components,
            ),
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
            _reply_header(EXECUTION_SPECIALIST),
            rendered,
        )
        self.assertIn(
            f"│ reply:{EXECUTION_SPECIALIST}:/run literal\n",
            rendered,
        )
        self.assertNotIn("[run ", rendered)
        replies = [
            component
            for component in semantic_components
            if type(component) is terminal_module.Message
            and component.role is terminal_module.SemanticRole.AGENT
        ]
        self.assertEqual(
            [(message.sender, message.accent) for message in replies],
            [
                (
                    "Lead Orchestrator → You",
                    terminal_module.SpeakerAccent.CYAN,
                ),
                (
                    "Plot & Structure Architect → You",
                    terminal_module.SpeakerAccent.YELLOW,
                ),
                (
                    "Character & World Custodian → You",
                    terminal_module.SpeakerAccent.GREEN,
                ),
                (
                    "Tone & Execution Specialist → You",
                    terminal_module.SpeakerAccent.BLUE,
                ),
            ],
        )
        self.assertTrue(
            all(
                message.presentation
                is terminal_module.MessagePresentation.CHAT_BLOCK
                and message.metadata is None
                for message in replies
            )
        )

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
        self.assertIn(_reply_header(LEAD_ORCHESTRATOR), rendered)
        self.assertIn("│ recovered\n", rendered)
        self.assertEqual(len({id(loop) for loop in loops}), 1)
        self.assertTrue(all(loop.is_closed() for loop in loops))
        self.assertTrue(all(task is not None and task.done() for task in tasks))


if __name__ == "__main__":
    unittest.main()
