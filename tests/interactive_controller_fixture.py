"""Shared provider-free fixtures for retained interactive controller tests."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    load_cli_context,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.conversation_storage import (
    ConversationStorageComponentBinding,
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)
from story_writing_agents.conversations import ConversationMetadata
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents.interactive_terminal._render import render_plain
from story_writing_agents.session_lifecycle import SessionDurableState
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    Workspace,
)

from tests.helpers import pristine_initialized_workspace


REAL_RUNTIME = object()


class ScriptedTerminalCallbacks:
    """Record terminal traffic and return scripted input values."""

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


def error_record(code: InteractiveErrorCode) -> str:
    """Render one exact semantic error using the controller-test width."""

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


class InteractiveControllerTestCase(unittest.TestCase):
    """Build real retained management controllers without execution doubles."""

    def make_workspace(self) -> Workspace:
        return pristine_initialized_workspace(self)

    def seed_session(
        self,
        workspace: Workspace,
        session_id: str,
        *,
        durable_state: SessionDurableState = SessionDurableState.READY,
    ) -> None:
        backend = AgentBackend(workspace, executable=sys.executable)
        try:
            backend.provision_session_resources(session_id)
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
        runtime: object = REAL_RUNTIME,
        width: int = 240,
    ) -> tuple[
        InteractiveController,
        AgentBackend,
        ScriptedTerminalCallbacks,
        FilesystemCollaborationEventStore,
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
        storage_services = compose_filesystem_conversation_storage(workspace)
        if catalog is not None:
            storage_services = ConversationStorageServices(
                catalog_binding=ConversationStorageComponentBinding(
                    storage_services.provenance,
                    catalog,
                ),
                event_store_binding=storage_services.event_store_binding,
                search_binding=storage_services.search_binding,
                mutation_coordinator=storage_services.mutation_coordinator,
            )
        keywords["conversation_storage_services"] = storage_services

        if runtime is REAL_RUNTIME:
            controller = InteractiveController(
                workspace,
                load_cli_context(workspace),
                selected_backend,
                terminal,
                **keywords,
            )
            return (
                controller,
                selected_backend,
                callbacks,
                storage_services.event_store,
                None,
            )

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
        store = storage_services.event_store
        return controller, selected_backend, callbacks, store, runtime_factory

    def prepared_selected_controller(
        self,
        *reads: object,
        runtime: object = REAL_RUNTIME,
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
        FilesystemCollaborationEventStore,
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
        return workspace, catalog, entries, controller, backend, callbacks, store
