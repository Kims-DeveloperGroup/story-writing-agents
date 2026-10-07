"""Session-lobby controller contracts, transitions, and cleanup tests."""

from __future__ import annotations
import story_writing_agents.lead_task_transitions as task
from story_writing_agents import conversations as collaboration
from story_writing_agents import lead_exploration as exploration
import hashlib

import ast
import asyncio
import importlib.util
import inspect
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import (
    AGENT_SPECS,
    PLOT_ARCHITECT,
    ROSTER,
)
from story_writing_agents import interactive_controller as controller_package
from story_writing_agents.backend import AgentBackend
from story_writing_agents.cli_context import (
    CliContext,
    load_cli_context,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from story_writing_agents.config import ConfigManager
from story_writing_agents.conversation_search import (
    ReferenceCollaborationHistorySearch,
    ConversationCitation,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchResult,
    ConversationSearchScope,
    ConversationTranscriptCursor,
    ConversationTranscriptPage,
)
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
    ConversationStorageBackend,
    ConversationStorageComponentBinding,
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)
from story_writing_agents.conversation_storage.provisioning import (
    ConversationStorageProvisioner,
)
from story_writing_agents.conversation_storage.selection import (
    ConversationStorageSelectionFailure,
    ConversationStorageSelectionReport,
)
from story_writing_agents.conversations import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationDecisionResult,
    CollaborationConfirmationDecision,
    CollaborationEventBatch,
    CollaborationNextAction,
    CollaborationReadableField,
    CollaborationReadableFieldKey,
    CollaborationStepState,
    CONVERSATION_EVENT_SCHEMA_VERSION,
    CONVERSATION_METADATA_SCHEMA_VERSION,
    ConversationCatalog,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
    LeadDecisionResult,
    LeadOutcomeKind,
    LeadTurnResult,
    StoryCommitDecision,
    StoryCommitDecisionResult,
)
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationExistsError,
    ConversationNotFoundError,
    SessionExistsError,
    SessionIdError,
    SessionAccessBlockedError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    SessionPausedError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_commands import ActionKind, InteractiveAction
from story_writing_agents.interactive_auth import (
    InteractiveAuthCoordinator,
    InteractiveAuthResult,
    InteractiveAuthResultKind,
)
from story_writing_agents.interactive_controller import (
    _conversation_policy as conversation_policy_module,
)
from story_writing_agents.interactive_controller import _core as controller_core_module
from story_writing_agents.interactive_controller import _lead_policy as lead_policy_module
from story_writing_agents.interactive_controller import (
    _runtime_policy as runtime_policy_module,
)
from story_writing_agents.interactive_controller import (
    _session_policy as session_policy_module,
)
from story_writing_agents import interactive_settings as settings_module
from story_writing_agents.interactive_terminal import (
    ControlOutcome,
    ControlOutcomeKind,
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
    ReferenceInputFailure,
    ReferenceInputFailureKind,
)
from story_writing_agents.provider_auth import (
    AUTH_PROVIDERS,
    ProviderAuthSnapshot,
    ProviderAuthState,
    ProviderAuthStatus,
)
from story_writing_agents.provider_catalog import (
    ProviderCatalog,
    ProviderCatalogDiscovery,
    ProviderCatalogService,
    ProviderCatalogState,
    ProviderEffort,
    ProviderModel,
)
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_terminal._render import render_plain
from story_writing_agents.session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
from story_writing_agents.session_recovery import (
    SessionRecoveryFailureCode,
    SessionRecoveryOwner,
)
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionAssessmentState,
    SessionTransitionNextAction,
    SessionTransitionOperation,
    SessionTransitionScope,
)
from story_writing_agents.orchestrator import RuntimeSession
from story_writing_agents.workspace import (
    Workspace,
)
from story_writing_agents.workspace.initialization import (
    SessionArtifactBindingError,
    require_ready_session_resources,
)

from tests.collaboration_contract_fixture import STEP_ID, contract_batches, make_proposal
from tests.helpers import pristine_initialized_workspace


_SESSION_DIRECTORY = "agent_states/orchestrator_state/provider_sessions"
_CONTEXT_PATH = "agent_states/orchestrator_state/cli_context.json"
_CONVERSATION_DEFAULT_DIRECTORY = (
    "agent_states/orchestrator_state/conversation_defaults"
)
_CREATED = "2026-01-01T00:00:00+00:00"
_UPDATED = "2026-01-02T00:00:00+00:00"
_COLLIDING_A = "c_0123456789" + ("a" * 22)
_COLLIDING_B = "c_0123456789" + ("b" * 22)
_UNIQUE_C = "c_fedcba9876" + ("c" * 22)






def _fresh_literal_object_none(target, attribute):
    return mock.patch.object(target, attribute)

def _message_visibility_controller_fixture(case, workspace):
    return case.make_controller(
        workspace,
        "/settings",
        "1",
        "2",
        "/cancel",
        "8",
        "1",
        "9",
        "1",
        "/quit",
        terminal_width=160,
    )

def _auth_cancellation_controller_fixture(case, workspace, coordinator):
    return case.make_controller(
        workspace,
        "/settings",
        "7",
        "2",
        "1",
        "1",
        "2",
        "1",
        "1",
        "/cancel",
        "/cancel",
        "9",
        "1",
        "/quit",
        auth_coordinator=coordinator,
        terminal_width=160,
    )

def _observed_picker_patch(original_picker):
    return mock.patch.object(
        terminal_module.InteractivePickerInput,
        "read_picker",
        autospec=True,
        side_effect=original_picker,
    )

def _catalog_settings_controller_fixture(case, workspace, catalog):
    return case.make_controller(
        workspace,
        provider_catalog=catalog,
    )

def _storage_selection_patch_fixture():
    return mock.patch.object(
        controller_core_module,
        "select_conversation_storage",
    )

def _loaded_settings_state_patch(state):
    return mock.patch.object(
        controller_core_module,
        "load_settings_state",
        return_value=state,
    )

def _settings_routes_authentication_patch():
    return mock.patch.object(
        InteractiveController,
        "_authenticate_settings_routes",
        autospec=True,
    )

def _prepared_settings_commit_patch():
    return mock.patch.object(
        controller_core_module,
        "commit_prepared_settings_save",
    )

def _cancel_picker_patch(cancel):
    return mock.patch.object(terminal_module.InteractivePickerInput, "read_picker", autospec=True, return_value=cancel)


def _control_commit_text(text):
    return terminal_module.ControlOutcome(terminal_module.ControlOutcomeKind.COMMIT, text=text)


def _observed_prompt_patch(original_prompt):
    return mock.patch.object(
        terminal_module.InteractivePromptInput,
        "read_prompt",
        autospec=True,
        side_effect=original_prompt,
    )

def _object_wrap_patch(target, attribute, value):
    return mock.patch.object(target, attribute, wraps=value)

def _autospec_effect_patch(target, attribute, value):
    return mock.patch.object(target, attribute, autospec=True, side_effect=value)

def _autospec_return_patch(target, attribute, value):
    return mock.patch.object(target, attribute, autospec=True, return_value=value)

def _object_effect_patch(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)

def _object_return_patch(target, attribute, value):
    return mock.patch.object(target, attribute, return_value=value)

def _object_new_patch(target, attribute, value):
    return mock.patch.object(target, attribute, new=value)

def _autospec_method_patch(target, attribute):
    return mock.patch.object(target, attribute, autospec=True)

def _availability_patch(target, value):
    return mock.patch(target, autospec=True, return_value=value)

class ScriptedTerminalCallbacks:
    """Record terminal traffic and supply scripted text, events, or callbacks."""

    def __init__(self, *reads: object) -> None:
        self.reads = list(reads)
        self.prompts: list[str] = []
        self.records: list[str] = []

    def read_line(self, prompt: str) -> str:
        """Return the next scripted line after recording its exact prompt."""

        self.prompts.append(prompt)
        if "Manuscript output" in prompt:
            return f"part-01/controller-{len(self.prompts)}.md"
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


def _auth_snapshot(
    codex: ProviderAuthState,
    antigravity: ProviderAuthState,
) -> ProviderAuthSnapshot:
    """Build one exact ordered provider-auth snapshot."""

    return ProviderAuthSnapshot(
        (
            ProviderAuthStatus("codex", codex),
            ProviderAuthStatus("antigravity", antigravity),
        )
    )


def _auth_result(
    current: ProviderAuthSnapshot,
    provider: str,
) -> InteractiveAuthResult:
    """Build one result correlated to the supplied provider status."""

    selected = current.status_for(provider)
    return InteractiveAuthResult(
        (
            InteractiveAuthResultKind.AUTHENTICATED
            if selected.authenticated
            else InteractiveAuthResultKind.NOT_AUTHENTICATED
        ),
        current,
        provider=provider,
        status=selected,
    )


class ScriptedAuthCoordinator:
    """Record controller auth traffic without native commands or credentials."""

    def __init__(
        self,
        *,
        snapshots: tuple[object, ...],
        results: tuple[object, ...] = (),
    ) -> None:
        self.snapshots = list(snapshots)
        self.results = list(results)
        self.events: list[tuple[str, object | None]] = []
        self.presentations: list[terminal_module.InputPresentation] = []

    def probe_all(self) -> ProviderAuthSnapshot:
        self.events.append(("probe_all", None))
        if not self.snapshots:
            raise AssertionError("unexpected controller auth probe")
        value = self.snapshots.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def choices(
        self,
        current: ProviderAuthSnapshot,
        *,
        selected_provider: str | None = None,
    ) -> tuple[terminal_module.Choice, ...]:
        self.events.append(("choices", selected_provider))
        choices = []
        for provider, label in zip(
            AUTH_PROVIDERS,
            ("Codex", "Antigravity"),
            strict=True,
        ):
            selected = current.status_for(provider)
            enabled = selected.state is not ProviderAuthState.UNAVAILABLE
            choices.append(
                terminal_module.Choice(
                    provider,
                    label,
                    description=selected.state.value,
                    enabled=enabled,
                    disabled_reason=(
                        None if enabled else "Install the provider CLI, then retry."
                    ),
                )
            )
        return tuple(choices)

    def run(
        self,
        current: ProviderAuthSnapshot,
        *,
        presentation: terminal_module.InputPresentation = (
            terminal_module.InputPresentation.STANDARD
        ),
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
        self.presentations.append(presentation)
        self.events.append(("run", selected_provider))
        if not self.results:
            raise AssertionError("unexpected controller auth picker")
        value = self.results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


class ScriptedProviderCatalog:
    """Return deterministic fresh catalog outcomes and record provider reads."""

    def __init__(self, *outcomes: object) -> None:
        if not outcomes:
            raise ValueError("at least one catalog outcome is required")
        self.outcomes = list(outcomes)
        self.events: list[str] = []

    def discover(self, provider: str) -> object:
        """Return the next outcome, repeating a sole retained outcome."""

        self.events.append(provider)
        value = self.outcomes[0]
        if len(self.outcomes) > 1:
            value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _ready_provider_catalog(
    provider: str,
    *models: ProviderModel,
) -> ProviderCatalogDiscovery:
    """Build one exact ready discovery for controller-only tests."""

    return ProviderCatalogDiscovery(
        provider,
        ProviderCatalogState.READY,
        ProviderCatalog(provider, models),
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture non-diagnostic files to detect silent domain rewrites."""

    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mode,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and "debug_logs" not in path.relative_to(root).parts
    }


def _manifest_relative(session_id: str) -> str:
    """Return the backend-owned manifest path for one safe test session."""

    return f"{_SESSION_DIRECTORY}/{session_id}.json"


def _access_relative(session_id: str) -> str:
    """Return the backend-owned access path for one safe test session."""

    return f"{_SESSION_DIRECTORY}/{session_id}.access.json"




def _error_record(code: InteractiveErrorCode, *, width: int = 80) -> str:
    """Return the exact default terminal record for one typed error code."""

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
    return f"{render_plain(component, width=width).text}\n"


def _conversation_scope_record(
    session_id: str,
    conversation_id: str | None,
    subject: str | None,
    *,
    width: int = 200,
) -> str:
    """Return one exact retained conversation-or-empty callback record."""

    del width
    if conversation_id is None:
        return (
            "[conversation] [empty]\n"
            f"  Session: {session_id}\n"
            "  Conversation: none\n"
            "  Subject: none\n"
        )
    return (
        "[conversation]\n"
        f"  Session: {session_id}\n"
        f"  Conversation: {conversation_id}\n"
        f"  Subject: {subject}\n"
    )


def _lifecycle(
    session_id: str,
    *,
    durable_state: SessionDurableState = SessionDurableState.READY,
    access_state: SessionAccessState = SessionAccessState.CLAIMABLE,
    hostname: str | None = None,
    pid: int | None = None,
) -> SessionLifecycleState:
    """Build one deterministic lifecycle value for formatting/fault tests."""

    return SessionLifecycleState(
        session_id=session_id,
        durable_state=durable_state,
        access_state=access_state,
        owner_hostname=hostname,
        owner_pid=pid,
        created_at=_CREATED,
        updated_at=_UPDATED,
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
            and (include_archived or entry.status is ConversationStatus.ACTIVE)
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
        return self._replace(replace(current, status=ConversationStatus.ARCHIVED))

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

        return pristine_initialized_workspace(self)

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
        durable_state: SessionDurableState,
    ) -> None:
        """Persist one ready or paused session without retaining access."""

        backend = AgentBackend(workspace, executable=sys.executable)
        try:
            backend.provision_session_resources(session_id)
            backend.create_session_with_access(session_id)
            if durable_state is SessionDurableState.PAUSED:
                backend.pause_session_and_release_access(session_id)
        finally:
            backend.close()

    def make_controller(
        self,
        workspace: Workspace,
        *reads: object,
        context: CliContext | None = None,
        backend: AgentBackend | None = None,
        conversation_catalog: ConversationCatalog | None = None,
        conversation_storage_services: ConversationStorageServices | None = None,
        auth_coordinator: object | None = None,
        provider_catalog: object | None = None,
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
        controller_keywords: dict[str, object] = {}
        if conversation_catalog is not None:
            if conversation_storage_services is not None:
                raise TypeError("catalog and storage services are mutually exclusive")
            base = compose_filesystem_conversation_storage(workspace)
            event_store = FilesystemCollaborationEventStore(
                workspace,
                catalog=conversation_catalog,
            )
            conversation_storage_services = ConversationStorageServices(
                catalog_binding=ConversationStorageComponentBinding(
                    base.provenance,
                    conversation_catalog,
                ),
                event_store_binding=ConversationStorageComponentBinding(
                    base.provenance,
                    event_store,
                ),
                search_binding=ConversationStorageComponentBinding(
                    base.provenance,
                    ReferenceCollaborationHistorySearch(
                        conversation_catalog,
                        event_store,
                    ),
                ),
                mutation_coordinator=base.mutation_coordinator,
            )
        if conversation_storage_services is not None:
            controller_keywords["conversation_storage_services"] = (
                conversation_storage_services
            )
        if auth_coordinator is not None:
            controller_keywords["auth_coordinator"] = auth_coordinator
        if provider_catalog is not None:
            controller_keywords["provider_catalog"] = provider_catalog
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
        self.assertEqual(controller_package.__all__, ["InteractiveController"])
        self.assertEqual(controller_core_module.__all__, ["InteractiveController"])
        self.assertIs(controller_package.InteractiveController, InteractiveController)
        self.assertIs(
            controller_core_module.InteractiveController,
            InteractiveController,
        )
        self.assertEqual(
            InteractiveController.__module__,
            "story_writing_agents.interactive_controller",
        )
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
                "conversation_storage_services",
                "auth_coordinator",
                "provider_catalog",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                and parameter.default is inspect.Parameter.empty
                for parameter in tuple(constructor.parameters.values())[:4]
            )
        )
        services_parameter = constructor.parameters["conversation_storage_services"]
        self.assertIs(
            services_parameter.kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(services_parameter.default)
        auth_parameter = constructor.parameters["auth_coordinator"]
        self.assertIs(auth_parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(auth_parameter.default)
        provider_catalog_parameter = constructor.parameters["provider_catalog"]
        self.assertIs(
            provider_catalog_parameter.kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(provider_catalog_parameter.default)
        self.assertEqual(
            get_type_hints(InteractiveController.__init__),
            {
                "workspace": Workspace,
                "context": CliContext,
                "backend": AgentBackend,
                "terminal": InteractiveTerminal,
                "conversation_storage_services": ConversationStorageServices | None,
                "auth_coordinator": InteractiveAuthCoordinator | None,
                "provider_catalog": ProviderCatalogService | None,
                "return": type(None),
            },
        )
        run_signature = inspect.signature(InteractiveController.run_session_lobby)
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

        with self.assertRaisesRegex(TypeError, "conversation_catalog"):
            InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
                conversation_catalog=object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "auth_coordinator"):
            InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
                auth_coordinator=object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "provider_catalog"):
            InteractiveController(
                workspace,
                CliContext(),
                backend,
                terminal,
                provider_catalog=object(),  # type: ignore[arg-type]
            )

        with mock.patch.object(backend, "close", wraps=backend.close) as close:
            self.assertEqual(controller.run_session_lobby(), 0)
            close.assert_called_once_with()
            with self.assertRaisesRegex(RuntimeError, "single-use"):
                controller.run_session_lobby()
            close.assert_called_once_with()

        for failure, after_body in (
            (KeyboardInterrupt("input guard activation"), False),
            (SystemExit("input guard activation"), False),
            (RuntimeError("interactive terminal restoration failed"), True),
        ):
            controller, backend, callbacks = self.make_controller(self.make_workspace(), "/quit")
            @contextmanager
            def fail_guard():
                if not after_body:
                    raise failure
                yield
                raise failure
            controller._terminal._input_session = fail_guard
            with mock.patch.object(backend, "close", wraps=backend.close) as close:
                if isinstance(failure, SystemExit):
                    with self.assertRaises(SystemExit) as raised:
                        controller.run_session_lobby()
                    self.assertIs(raised.exception, failure)
                else:
                    self.assertEqual(controller.run_session_lobby(), 2)
                close.assert_called_once_with()
                with self.assertRaisesRegex(RuntimeError, "single-use"):
                    controller.run_session_lobby()
                close.assert_called_once_with()
            if not after_body:
                self.assertEqual(callbacks.prompts, [])
            self.assertIsNone(controller._runner)

    def test_diagnostic_error_failure_preserves_status_output_and_cleanup(
        self,
    ) -> None:
        """Keep the controller contract when its diagnostic sink fails fatally."""

        workspace = self.make_workspace()
        original_failure = RuntimeError("PRIVATE_TRANSCRIPT_ERROR")
        controller, backend, callbacks = self.make_controller(
            workspace,
            original_failure,
        )
        failed_log = mock.Mock()
        failed_log.error.side_effect = SystemExit("diagnostic sink failed")
        controller._debug_log = failed_log

        with _object_wrap_patch(backend, "close", backend.close) as close:
            self.assertEqual(controller.run_session_lobby(), 2)

        close.assert_called_once_with()
        failed_log.error.assert_called_once()
        self.assertIs(failed_log.error.call_args.args[2], original_failure)
        self.assertEqual(
            callbacks.records[-1],
            _error_record(InteractiveErrorCode.INTERNAL_ERROR),
        )
        self.assertNotIn(
            "PRIVATE_TRANSCRIPT_ERROR",
            "".join(callbacks.records),
        )
        self.assertIsNone(controller._runner)
        self.assertIsNone(controller._attached_session_id)
        self.assertIsNone(controller._focused_conversation)
        self.assertIsNone(controller._collaboration_history_service)

    def test_trace_failure_preserves_rejected_and_success_output_and_cleanup(
        self,
    ) -> None:
        """Keep handled errors and normal exit independent of fatal traces."""

        workspace = self.make_workspace()
        controller, backend, callbacks = self.make_controller(
            workspace,
            "/search PRIVATE_TRACE_SEARCH",
            "/quit",
        )
        failed_log = mock.Mock()
        failed_log.trace.side_effect = KeyboardInterrupt("diagnostic trace failure")
        controller._debug_log = failed_log

        with _object_wrap_patch(backend, "close", backend.close) as close:
            self.assertEqual(controller.run_session_lobby(), 0)

        close.assert_called_once_with()
        self.assertGreater(failed_log.trace.call_count, 0)
        self.assertEqual(
            callbacks.records.count(
                _error_record(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            ),
            1,
        )
        self.assertNotIn("PRIVATE_TRACE_SEARCH", "".join(callbacks.records))
        self.assertNotIn("diagnostic trace failure", "".join(callbacks.records))
        self.assertIsNone(controller._runner)
        self.assertIsNone(controller._attached_session_id)
        self.assertIsNone(controller._focused_conversation)
        self.assertIsNone(controller._collaboration_history_service)

    def test_runtime_inspection_facts_are_exact_frozen_internal_contracts(
        self,
    ) -> None:
        debate_type = runtime_policy_module.DebateInspectionFacts
        context_type = runtime_policy_module.DebateContextInspectionFacts
        self.assertEqual(
            tuple(field.name for field in fields(debate_type)),
            (
                "session_id",
                "debate_id",
                "status",
                "terminal",
                "input_kind",
                "revision_id",
                "rounds_completed",
                "artifact",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(context_type)),
            (
                "conversation_id",
                "mode",
                "event_count",
                "complete_turns",
                "retained_turns",
                "omitted_turns",
                "retained_utf8_bytes",
                "truncated",
            ),
        )
        self.assertNotIn("DebateInspectionFacts", controller_package.__all__)
        self.assertNotIn("DebateContextInspectionFacts", controller_package.__all__)

        debate = runtime_policy_module.validate_debate_inspection(
            {
                "session_id": "session",
                "debate_id": "debate_1",
                "status": "not_found",
                "terminal": False,
                "state": None,
                "result": None,
                "messages": [],
            },
            session_id="session",
            debate_id="debate_1",
        )
        self.assertEqual(
            debate,
            debate_type(
                session_id="session",
                debate_id="debate_1",
                status="not_found",
                terminal=False,
                input_kind=None,
                revision_id=None,
                rounds_completed=0,
                artifact=None,
            ),
        )
        context = runtime_policy_module.validate_debate_context_inspection(
            {
                "schema_version": 1,
                "session_id": "session",
                "debate_id": "debate_1",
                "conversation_id": "c_0123456789",
                "mode": "fresh",
                "snapshot": None,
            },
            session_id="session",
            debate_id="debate_1",
        )
        self.assertEqual(
            context,
            context_type(
                conversation_id="c_0123456789",
                mode=runtime_policy_module.DebateContextMode.FRESH,
                event_count=0,
                complete_turns=0,
                retained_turns=0,
                omitted_turns=0,
                retained_utf8_bytes=0,
                truncated=False,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            debate.status = "active"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            context.truncated = True  # type: ignore[misc]

    def test_agents_and_status_ignore_message_detail_preference(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        def projection(method, *, details: bool):
            controller._show_message_details = details
            component_start = len(components)
            record_start = len(callbacks.records)
            method()
            return (
                tuple(components[component_start:]),
                tuple(callbacks.records[record_start:]),
            )

        with _autospec_effect_patch(terminal_module.InteractiveSemanticOutput, "write_component",
                 capture_component):
            agents_off = projection(
                controller._write_agent_roster,
                details=False,
            )
            agents_on = projection(
                controller._write_agent_roster,
                details=True,
            )
            status_off = projection(
                controller._write_runtime_status,
                details=False,
            )
            status_on = projection(
                controller._write_runtime_status,
                details=True,
            )

        self.assertEqual(agents_off, agents_on)
        self.assertEqual(status_off, status_on)
        agent_view = agents_off[0][0]
        self.assertIs(type(agent_view), terminal_module.ListView)
        self.assertEqual(agent_view.title, "Agents")
        self.assertEqual(len(agent_view.items), 4)
        self.assertTrue(
            all(
                tuple(field.label for field in item.fields)
                == (
                    "ID",
                    "Advisory domain",
                    "Execution",
                    "Confirmation",
                )
                for item in agent_view.items
            )
        )
        execution_view = next(
            component
            for component in status_off[0]
            if type(component) is terminal_module.ListView
            and component.title == "Execution profiles"
        )
        self.assertEqual(len(execution_view.items), 4)
        self.assertTrue(
            all(
                tuple(field.label for field in item.fields)
                == (
                    "Provider",
                    "Provider source",
                    "Model",
                    "Model source",
                    "Effort",
                    "Effort source",
                    "Confirmation",
                    "Thread",
                    "Route generation",
                )
                for item in execution_view.items
            )
        )

    def test_controller_and_composer_have_no_generic_status_sites(
        self,
    ) -> None:
        source_root = (
            Path(__file__).resolve().parents[1] / "src" / "story_writing_agents"
        )
        controller_tree = ast.parse(
            (source_root / "interactive_controller" / "_core.py").read_text(
                encoding="utf-8"
            )
        )
        terminal_tree = ast.parse(
            (source_root / "interactive_terminal" / "_core.py").read_text(
                encoding="utf-8"
            )
        )

        deferred_controller_calls = [
            node
            for node in ast.walk(controller_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_status"
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
            and node.func.value.attr == "_terminal"
        ]
        deferred_composer_calls = [
            node
            for node in ast.walk(terminal_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_status"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ]
        self.assertEqual(deferred_controller_calls, [])
        self.assertEqual(deferred_composer_calls, [])

    def test_five_unit_responsibilities_dependencies_and_policy_state(self) -> None:
        source_root = (
            Path(__file__).resolve().parents[1] / "src" / "story_writing_agents"
        )
        controller_root = source_root / "interactive_controller"
        sources = {
            "interactive_controller": controller_root / "__init__.py",
            "interactive_controller._core": controller_root / "_core.py",
            "interactive_controller._session_policy": (
                controller_root / "_session_policy.py"
            ),
            "interactive_controller._conversation_policy": (
                controller_root / "_conversation_policy.py"
            ),
            "interactive_controller._runtime_policy": (
                controller_root / "_runtime_policy.py"
            ),
        }
        expected_docstrings = {
            "interactive_controller": (
                "Stable import boundary for synchronous interactive coordination."
            ),
            "interactive_controller._core": (
                "Stateful synchronous interactive shell coordination."
            ),
            "interactive_controller._session_policy": (
                "Pure lifecycle interpretation for the interactive controller."
            ),
            "interactive_controller._conversation_policy": (
                "Pure conversation fact interpretation for the interactive controller."
            ),
            "interactive_controller._runtime_policy": (
                "Pure persisted-runtime fact interpretation for the interactive "
                "controller."
            ),
        }
        expected_dependencies = {
            "interactive_controller": {"interactive_controller._core"},
            "interactive_controller._core": {
                "agent_exchanges",
                "agents",
                "backend",
                "cli_context",
                "conversation_search",
                "conversation_storage.contracts",
                "conversation_storage.selection",
                "conversations",
                "debug_log",
                "errors",
                "interactive_auth",
                "interactive_commands",
                "interactive_settings",
                "interactive_controller._conversation_policy",
                "interactive_controller._exchange_policy",
                "interactive_controller._exchange_viewer",
                "interactive_controller._lead_policy",
                "interactive_controller._runtime_policy",
                "interactive_controller._session_policy",
                "interactive_terminal",
                "lead_exploration",
                "orchestrator",
                "provider_adapter",
                "provider_auth",
                "provider_catalog",
                "session_lifecycle",
                "workspace", "workspace.initialization",
            },
            "interactive_controller._session_policy": {
                "errors",
                "interactive_terminal",
                "session_lifecycle",
                "session_recovery.transition_assessment",
            },
            "interactive_controller._conversation_policy": {
                "conversation_search",
                "conversations",
                "errors",
                "interactive_terminal",
                "workspace",
            },
            "interactive_controller._runtime_policy": {
                "errors",
                "interactive_terminal",
                "models",
                "workspace",
            },
        }
        trees: dict[str, ast.Module] = {}
        for module_name, source_path in sources.items():
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            trees[module_name] = tree
            package_name = (
                f"story_writing_agents.{module_name}"
                if source_path.name == "__init__.py"
                else f"story_writing_agents.{module_name.rpartition('.')[0]}"
            )
            dependencies = {
                importlib.util.resolve_name(
                    "." * node.level + (node.module or ""),
                    package_name,
                ).removeprefix("story_writing_agents.")
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level > 0
            }
            with self.subTest(module=module_name):
                self.assertEqual(
                    ast.get_docstring(tree), expected_docstrings[module_name]
                )
                self.assertEqual(dependencies, expected_dependencies[module_name])

        expected_policy_exports = {
            session_policy_module: [
                "RECOVERABLE_SESSION_ERROR_CODES",
                "SessionTransitionAssessment",
                "SessionTransitionOperation",
                "SessionTransitionPresentation",
                "classify_session_failure",
                "next_session_action",
                "project_transition_assessment",
                "require_correlated_state",
                "require_lifecycle_state",
                "require_ready_access_state",
                "require_released_state",
                "require_transition_assessment",
                "transition_operation_is_allowed",
            ],
            conversation_policy_module: [
                "ConversationCatalogProtocolFailure",
                "ConversationInputFailure",
                "ConversationResolutionFailure",
                "classify_conversation_failure",
                "classify_memory_catalog_or_reference_failure",
                "classify_search_service_failure",
                "classify_transcript_service_failure",
                "normalize_conversation_input",
                "resolve_conversation_reference",
                "validate_catalog_entries",
                "validate_catalog_metadata",
                "validate_conversation_reference",
                "validate_search_result_handles",
                "validate_history_snapshot",
                "validate_transcript_page",
                "visible_conversation_handle",
            ],
            runtime_policy_module: [
                "DebateContextInspectionFacts",
                "DebateInspectionFacts",
                "classify_inspection_failure",
                "validate_debate_context_inspection",
                "validate_debate_inspection",
                "validate_session_inspection",
            ],
        }
        for policy_module, expected_exports in expected_policy_exports.items():
            with self.subTest(policy=policy_module.__name__):
                self.assertEqual(policy_module.__all__, expected_exports)
        self.assertFalse(
            hasattr(
                conversation_policy_module,
                "format_transcript_context_status",
            )
        )

        expected_policy_assignments = {
            "interactive_controller._session_policy": {
                "RECOVERABLE_SESSION_ERROR_CODES",
                "__all__",
            },
            "interactive_controller._conversation_policy": {
                "_MINIMUM_CONVERSATION_PREFIX_LENGTH",
                "__all__",
            },
            "interactive_controller._runtime_policy": {"__all__"},
        }
        expected_policy_classes = {
            "interactive_controller._session_policy": {
                "SessionTransitionPresentation",
            },
            "interactive_controller._conversation_policy": {
                "ConversationCatalogProtocolFailure",
                "ConversationInputFailure",
                "ConversationResolutionFailure",
            },
            "interactive_controller._runtime_policy": {
                "DebateContextInspectionFacts",
                "DebateInspectionFacts",
            },
        }
        for module_name in expected_policy_assignments:
            tree = trees[module_name]
            assignments = {
                target.id
                for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            classes = {
                node.name for node in tree.body if isinstance(node, ast.ClassDef)
            }
            with self.subTest(policy_state=module_name):
                self.assertEqual(assignments, expected_policy_assignments[module_name])
                self.assertEqual(classes, expected_policy_classes[module_name])
                self.assertFalse(
                    [
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, ast.AsyncFunctionDef)
                    ]
                )
                self.assertFalse(
                    [
                        node
                        for node in ast.walk(tree)
                        if isinstance(node, (ast.Global, ast.Nonlocal))
                    ]
                )
                self.assertFalse(
                    {
                        dependency
                        for dependency in expected_dependencies[module_name]
                        if dependency.startswith("interactive_controller.")
                    }
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
        for module_name, tree in trees.items():
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
            with self.subTest(callable_contracts=module_name):
                for node in callable_nodes:
                    self.assertTrue(ast.get_docstring(node), node.name)
                    self.assertNotIn(node.name.strip("_"), generic_names, node.name)

    def test_direct_import_is_clean_and_package_graph_is_acyclic(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        source_path = str(repository_root / "src")
        environment["PYTHONPATH"] = source_path
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import story_writing_agents as facade; "
            "from story_writing_agents import interactive_controller as module; "
            "from story_writing_agents.interactive_controller import "
            "InteractiveController; "
            "from story_writing_agents.interactive_controller import _core; "
            "from story_writing_agents import run_inputs; "
            "assert module.__all__ == ['InteractiveController']; "
            "assert _core.__all__ == ['InteractiveController']; "
            "assert module.InteractiveController is InteractiveController; "
            "assert _core.InteractiveController is InteractiveController; "
            "assert InteractiveController.__module__ == "
            "'story_writing_agents.interactive_controller'; "
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
        module_paths = tuple(sorted(package.rglob("*.py")))

        def import_name(path: Path) -> str:
            """Return the canonical import name for one production source."""

            relative_parts = list(path.relative_to(package).with_suffix("").parts)
            if relative_parts[-1] == "__init__":
                relative_parts.pop()
            suffix = ".".join(relative_parts)
            return "story_writing_agents" + (f".{suffix}" if suffix else "")

        def runtime_nodes(node: ast.AST):
            yield node
            if isinstance(node, ast.If) and (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
            ):
                for child in node.orelse:
                    yield from runtime_nodes(child)
                return
            for child in ast.iter_child_nodes(node):
                yield from runtime_nodes(child)

        module_names = {path: import_name(path) for path in module_paths}
        modules = set(module_names.values())
        graph: dict[str, set[str]] = {name: set() for name in modules}
        for path, module_name in module_names.items():
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            for node in runtime_nodes(parsed):
                if isinstance(node, ast.ImportFrom) and node.level > 0:
                    package_name = (
                        module_name
                        if path.name == "__init__.py"
                        else module_name.rpartition(".")[0]
                    )
                    dependency = importlib.util.resolve_name(
                        "." * node.level + (node.module or ""),
                        package_name,
                    )
                    if dependency in modules:
                        graph[module_name].add(dependency)
                elif isinstance(node, ast.Import):
                    graph[module_name].update(
                        alias.name for alias in node.names if alias.name in modules
                    )

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
        self.assertEqual(len(modules), 154)
        self.assertEqual(sum(len(dependencies) for dependencies in graph.values()), 660)
        self.assertTrue(
            {
                "story_writing_agents.artifact_proposals",
                "story_writing_agents.conversations",
                "story_writing_agents.conversations.catalog",
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations.events",
                "story_writing_agents.conversation_storage.archive",
                "story_writing_agents.conversation_storage.filesystem",
                "story_writing_agents.conversation_storage.filesystem_archive",
                "story_writing_agents.conversation_storage.migration",
                "story_writing_agents.conversation_storage.sqlite_database",
                "story_writing_agents.conversation_storage.sqlite",
                "story_writing_agents.conversation_storage.sqlite_search_index",
                "story_writing_agents.conversation_literal_search",
                "story_writing_agents.conversation_rag_cli",
                "story_writing_agents.conversation_rag_runtime",
                "story_writing_agents.provider_catalog",
                "story_writing_agents.interactive_controller._exchange_policy",
                "story_writing_agents.interactive_controller._exchange_viewer",
                "story_writing_agents.interactive_terminal._external_view",
                "story_writing_agents.workspace.initialization",
                "story_writing_agents.workspace.schema",
            }
            <= modules
        )








class ControllerAuthTests(ControllerTestCase):
    """Verify live `/auth` behavior and durable missing-default policy."""

    def test_auth_uses_live_status_and_new_authentication_wins_default(self) -> None:
        workspace = self.make_workspace()
        initial = _auth_snapshot(
            ProviderAuthState.UNAUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        refreshed = _auth_snapshot(
            ProviderAuthState.UNAUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(initial, refreshed),
            results=(_auth_result(refreshed, "antigravity"),),
        )
        controller, backend, callbacks = self.make_controller(
            workspace,
            "/auth",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=120,
        )

        with mock.patch.object(subprocess, "Popen") as popen:
            self.assertEqual(controller.run_session_lobby(), 0)
        popen.assert_not_called()
        self.assertEqual(
            coordinator.events,
            [
                ("probe_all", None),
                ("run", None),
                ("probe_all", None),
            ],
        )
        self.assertEqual(
            coordinator.presentations,
            [terminal_module.InputPresentation.STANDARD],
        )
        defaults = ConfigManager(workspace).load_system_defaults()
        self.assertEqual(defaults.default_backend, "antigravity")
        self.assertTrue(backend._closed)
        rendered = "".join(callbacks.records)
        for secret in ("token=", "account@", "https://oauth"):
            self.assertNotIn(secret, rendered)

    def test_auth_cancel_keeps_an_already_established_live_default(self) -> None:
        workspace = self.make_workspace()
        both = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(both,),
            results=(InteractiveAuthResult(InteractiveAuthResultKind.CANCEL, both),),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/auth",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=120,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            ConfigManager(workspace).load_system_defaults().default_backend,
            "codex",
        )
        self.assertEqual(
            coordinator.events,
            [("probe_all", None), ("run", None)],
        )
        self.assertIn(
            "Any authenticated workspace default already established was kept.",
            "".join(callbacks.records),
        )

    def test_auth_without_coordinator_is_safe_nonmutating_and_compatible(self) -> None:
        workspace = self.make_workspace()
        before = _tree_snapshot(workspace.root)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/auth",
            "/quit",
            terminal_width=120,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertIn(
            "Provider authentication is unavailable in this shell.",
            "".join(callbacks.records),
        )


def _cancel_control_outcome():
    return terminal_module.ControlOutcome(terminal_module.ControlOutcomeKind.CANCEL)


class ControllerSettingsTests(ControllerTestCase):
    """Verify isolated settings UI, validation, persistence, and cancellation."""

    VALUE_SAVE_GUIDANCE = (
        "Entered changes remain pending in memory and are not saved; choose Apply "
        "Changes in Settings to persist them."
    )

    def assert_value_save_guidance(self, instruction: str) -> None:
        """Require one exact terminal-visible persistence warning."""

        self.assertEqual(instruction.count(self.VALUE_SAVE_GUIDANCE), 1)
        self.assertTrue(instruction.endswith(self.VALUE_SAVE_GUIDANCE))

    @staticmethod
    def expected_pending_save_notice() -> terminal_module.Notice:
        return terminal_module.Notice(
            "Choose Apply Changes to persist pending changes",
            label="save required",
            role=terminal_module.SemanticRole.WARNING,
            state=terminal_module.ViewState.IDLE,
        )

    @staticmethod
    def expected_panel(state) -> object:
        """Return the exact public semantic tree for one settings snapshot."""

        def configuration_values(configuration):
            defaults = configuration.system_defaults
            values = [
                ("Language", defaults.language),
                ("Persona", defaults.persona),
                ("Maximum debate rounds", str(defaults.max_debate_rounds)),
                (
                    "Conversation storage",
                    defaults.conversation_storage_backend or "filesystem",
                ),
                (
                    "Conversation RAG provider",
                    (
                        "Local"
                        if defaults.conversation_rag_provider == "local"
                        else "Disabled"
                    ),
                ),
                (
                    "Conversation RAG automatic",
                    "On" if defaults.conversation_rag_automatic else "Off",
                ),
                (
                    "Message details",
                    "On" if defaults.show_message_details else "Off",
                ),
            ]
            for agent_id in ROSTER:
                display_name = AGENT_SPECS[agent_id].display_name
                agent = configuration.agent_configs[agent_id]
                provider = (
                    agent.backend
                    or configuration.system_defaults.default_backend
                    or "codex"
                )
                values.extend(
                    (
                        (
                            f"{display_name} Provider",
                            provider
                            if agent.backend is not None
                            else f"{provider} (default)",
                        ),
                        (
                            f"{display_name} Codex model",
                            agent.codex_model or "inherit",
                        ),
                        (
                            f"{display_name} Codex reasoning",
                            agent.codex_reasoning_effort or "inherit",
                        ),
                        (
                            f"{display_name} Antigravity model",
                            agent.antigravity_model or "inherit",
                        ),
                        (
                            f"{display_name} Antigravity effort",
                            agent.antigravity_effort or "inherit",
                        ),
                    )
                )
            return tuple(values)

        current_values = configuration_values(state.baseline.configuration)
        pending_values = configuration_values(state.pending)
        changed_indices = tuple(
            index
            for index, (_label, value) in enumerate(pending_values)
            if value != current_values[index][1]
        )
        current_fields = tuple(
            terminal_module.Field(
                current_values[index][0],
                f"{current_values[index][1]} (Changed)",
                role=terminal_module.SemanticRole.DEFAULT,
            )
            for index in changed_indices
        )
        pending_fields = tuple(
            terminal_module.Field(
                pending_values[index][0],
                f"{pending_values[index][1]} (Changed)",
                role=terminal_module.SemanticRole.WARNING,
            )
            for index in changed_indices
        )

        if changed_indices:
            live_changes = any(index not in {3, 4, 5} for index in changed_indices)
            storage_changes = 3 in changed_indices
            rag_changes = any(index in {4, 5} for index in changed_indices)
            activation_parts: list[str] = []
            if live_changes:
                activation_parts.append(
                    "Runtime changes apply to this shell after Apply Changes."
                )
            if storage_changes:
                activation_parts.append(
                    "Desired storage changes apply to the next shell."
                )
            if rag_changes:
                activation_parts.append(
                    "Conversation RAG changes apply to the next shell; this "
                    "running shell retains its current RAG service and "
                    "automatic policy."
                )
            items = (
                terminal_module.Section("Current", current_fields),
                terminal_module.Section("Pending", pending_fields),
                terminal_module.Notice(
                    " ".join(activation_parts),
                    label="activation",
                    role=terminal_module.SemanticRole.INFO,
                ),
            )
        else:
            items = (
                terminal_module.Notice(
                    "No pending settings changes.",
                    label="no changes",
                    role=terminal_module.SemanticRole.MUTED,
                ),
            )
        return terminal_module.Panel(
            "Settings",
            items,
            state=terminal_module.ViewState.OPEN,
        )

    @staticmethod
    def expected_entered_notice(
        field: str,
        display_value: str,
    ) -> terminal_module.Notice:
        return terminal_module.Notice(
            f"Pending {field} entered: {display_value}.",
            label="entered",
            role=terminal_module.SemanticRole.SUCCESS,
            state=terminal_module.ViewState.COMPLETED,
        )

    def test_authenticated_provider_assignment_is_discarded_without_save(
        self,
    ) -> None:
        workspace = self.make_workspace()
        defaults_path = workspace.root / "config/system_defaults.json"
        agents_path = workspace.root / "config/agent_configs.json"
        durable_before = (defaults_path.read_bytes(), agents_path.read_bytes())
        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        both = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(codex_only, codex_only, both, both),
            results=(_auth_result(both, "antigravity"),),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "7",
            "2",
            "1",
            "2",
            "/cancel",
            "/cancel",
            "/cancel",
            "9",
            "1",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=160,
        )
        returned_states = []
        edit_provider = controller._edit_settings_agent_provider

        def capture_returned_state(_controller, state, agent_id):
            result = edit_provider(state, agent_id)
            returned_states.append(result)
            self.assertEqual(
                (defaults_path.read_bytes(), agents_path.read_bytes()),
                durable_before,
            )
            return result

        with (
            _autospec_effect_patch(InteractiveController, "_edit_settings_agent_provider",
                capture_returned_state),
            _object_wrap_patch(controller_core_module, "establish_settings_default_backend",
                controller_core_module.establish_settings_default_backend) as establish_default,
            _object_wrap_patch(controller_core_module, "prepare_settings_save",
                controller_core_module.prepare_settings_save) as prepare,
            _storage_selection_patch_fixture() as select_storage,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(len(returned_states), 1)
        returned, whole_cancel = returned_states[0]
        self.assertFalse(whole_cancel)
        self.assertIsNone(returned.pending.system_defaults.default_backend)
        self.assertEqual(
            returned.pending.agent_configs[PLOT_ARCHITECT].backend,
            "antigravity",
        )
        establish_default.assert_not_called()
        prepare.assert_not_called()
        select_storage.assert_not_called()
        self.assertEqual(
            (defaults_path.read_bytes(), agents_path.read_bytes()),
            durable_before,
        )
        durable = settings_module.load_settings_state(workspace).pending
        self.assertIsNone(durable.system_defaults.default_backend)
        self.assertIsNone(durable.agent_configs[PLOT_ARCHITECT].backend)
        self.assertEqual(
            coordinator.events,
            [
                ("probe_all", None),
                ("choices", "codex"),
                ("probe_all", None),
                ("run", "antigravity"),
                ("probe_all", None),
                ("probe_all", None),
                ("choices", "antigravity"),
            ],
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count("[cancelled] Pending changes discarded.\n"),
            1,
        )
        self.assertNotIn("Settings saved", rendered)

    def test_failed_provider_auth_never_assigns_route_but_keeps_default(self) -> None:
        workspace = self.make_workspace()
        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(codex_only,) * 9,
            results=(_auth_result(codex_only, "antigravity"),),
        )
        controller, _backend, callbacks = _auth_cancellation_controller_fixture(self, workspace, coordinator)
        self.assertEqual(controller.run_session_lobby(), 0)

        configuration = settings_module.load_settings_state(workspace).pending
        self.assertIsNone(configuration.system_defaults.default_backend)
        self.assertIsNone(configuration.agent_configs[PLOT_ARCHITECT].backend)
        rendered = "".join(callbacks.records)
        self.assertIn(
            "was not assigned because fresh authentication was not confirmed",
            rendered,
        )
        self.assertEqual(
            rendered.count("[cancelled] Pending changes discarded.\n"),
            1,
        )
        self.assertNotIn("Settings saved", rendered)
        self.assertEqual(
            coordinator.events.count(("run", "antigravity")),
            1,
        )
        self.assertEqual(
            coordinator.events.count(("probe_all", None)),
            9,
        )

    def test_provider_auth_cancel_returns_to_picker_before_parent_cancel(self) -> None:
        workspace = self.make_workspace()
        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(codex_only,) * 8,
            results=(
                InteractiveAuthResult(
                    InteractiveAuthResultKind.CANCEL,
                    codex_only,
                ),
            ),
        )
        controller, _backend, callbacks = _auth_cancellation_controller_fixture(self, workspace, coordinator)
        self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            coordinator.events.count(("run", "antigravity")),
            1,
        )
        self.assertEqual(
            coordinator.events.count(("probe_all", None)),
            8,
        )
        provider_prompts = [
            prompt
            for prompt in callbacks.prompts
            if prompt.startswith("Provider\nContext: Plot & Structure Architect\n")
        ]
        self.assertEqual(len(provider_prompts), 4)
        configuration = settings_module.load_settings_state(workspace).pending
        self.assertIsNone(configuration.agent_configs[PLOT_ARCHITECT].backend)
        self.assertEqual(
            "".join(callbacks.records).count(
                "[cancelled] Pending changes discarded.\n"
            ),
            1,
        )

    def test_provider_auth_interrupt_and_eof_cancel_whole_settings(self) -> None:
        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        for kind in (
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        ):
            with self.subTest(kind=kind):
                workspace = self.make_workspace()
                before = _tree_snapshot(workspace.root)
                coordinator = ScriptedAuthCoordinator(
                    snapshots=(codex_only, codex_only),
                    results=(InteractiveAuthResult(kind, codex_only),),
                )
                controller, _backend, _callbacks = self.make_controller(
                    workspace,
                    auth_coordinator=coordinator,
                )
                choose_antigravity = _control_commit_text("antigravity")
                with _cancel_picker_patch(choose_antigravity):
                    unchanged, whole_cancel = controller._edit_settings_agent_provider(
                        settings_module.load_settings_state(workspace),
                        PLOT_ARCHITECT,
                    )

                self.assertTrue(whole_cancel)
                self.assertIsNone(
                    unchanged.pending.agent_configs[PLOT_ARCHITECT].backend
                )
                self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertEqual(
                    coordinator.events,
                    [
                        ("probe_all", None),
                        ("choices", "codex"),
                        ("probe_all", None),
                        ("run", "antigravity"),
                    ],
                )

    def test_settings_panel_omits_empty_diff_sections(self) -> None:
        workspace = self.make_workspace()
        state = settings_module.load_settings_state(workspace)
        self.assertEqual(
            InteractiveController._settings_panel(state),
            terminal_module.Panel(
                "Settings",
                (
                    terminal_module.Notice(
                        "No pending settings changes.",
                        label="no changes",
                        role=terminal_module.SemanticRole.MUTED,
                    ),
                ),
                state=terminal_module.ViewState.OPEN,
            ),
        )

        changed = settings_module.update_agent_codex_model(
            state,
            PLOT_ARCHITECT,
            "codex-model",
        )
        changed = settings_module.update_agent_antigravity_model(
            changed,
            PLOT_ARCHITECT,
            "agy-model",
        )
        panel = InteractiveController._settings_panel(changed)
        self.assertEqual(panel, self.expected_panel(changed))
        rendered = render_plain(panel, width=120).text
        self.assertIn("Plot & Structure Architect Codex model", rendered)
        self.assertIn(
            "Plot & Structure Architect Antigravity model",
            rendered,
        )

    def test_top_level_pending_notice_tracks_actual_diff_and_full_revert(
        self,
    ) -> None:
        workspace = self.make_workspace()
        before = _tree_snapshot(workspace.root)
        baseline = settings_module.load_settings_state(
            workspace
        ).pending.system_defaults.language
        changed = "JA" if baseline != "JA" else "EN"
        controller, _backend, callbacks = self.make_controller(workspace)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()
        with (
            _observed_picker_patch(
                (
                    commit("language"),
                    commit(changed),
                    cancel,
                    commit("language"),
                    commit(baseline),
                    cancel,
                    commit("save"),
                    cancel,
                ),
            ) as picker,
            _storage_selection_patch_fixture() as save,
        ):
            controller._run_settings()

        pending_notice = self.expected_pending_save_notice()
        top_level_calls = [
            call for call in picker.call_args_list if call.args[1].name == "Settings"
        ]
        self.assertEqual(
            tuple(call.kwargs.get("notice") for call in top_level_calls),
            (None, pending_notice, None, None),
        )
        self.assertEqual(
            tuple(
                tuple(choice.identity for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (
                    "language",
                    "persona",
                    "max_debate_rounds",
                    "conversation_storage",
                    "conversation_rag",
                    "show_message_details",
                    "providers",
                ),
                (
                    "language",
                    "persona",
                    "max_debate_rounds",
                    "conversation_storage",
                    "conversation_rag",
                    "show_message_details",
                    "providers",
                    "save",
                    "cancel",
                ),
                (
                    "language",
                    "persona",
                    "max_debate_rounds",
                    "conversation_storage",
                    "conversation_rag",
                    "show_message_details",
                    "providers",
                ),
                (
                    "language",
                    "persona",
                    "max_debate_rounds",
                    "conversation_storage",
                    "conversation_rag",
                    "show_message_details",
                    "providers",
                ),
            ),
        )
        self.assertEqual(
            tuple(
                tuple(choice.label for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (
                    "Language",
                    "Persona",
                    "Maximum debate rounds",
                    "Conversation storage",
                    "Conversation RAG",
                    "Message details",
                    "Agents",
                ),
                (
                    "Language",
                    "Persona",
                    "Maximum debate rounds",
                    "Conversation storage",
                    "Conversation RAG",
                    "Message details",
                    "Agents",
                    "Apply Changes",
                    "Discard Changes",
                ),
                (
                    "Language",
                    "Persona",
                    "Maximum debate rounds",
                    "Conversation storage",
                    "Conversation RAG",
                    "Message details",
                    "Agents",
                ),
                (
                    "Language",
                    "Persona",
                    "Maximum debate rounds",
                    "Conversation storage",
                    "Conversation RAG",
                    "Message details",
                    "Agents",
                ),
            ),
        )
        self.assertEqual(
            tuple(call.args[1].instruction for call in top_level_calls),
            (
                "Choose a setting to edit.",
                "Choose a setting to edit, apply changes, or discard changes.",
                "Choose a setting to edit.",
                "Choose a setting to edit.",
            ),
        )
        self.assertEqual(
            tuple(
                tuple(choice.form_summary for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (
                    f"Current: {baseline}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    "Current: Provider Disabled; Automatic Off",
                    "Current: Off",
                    None,
                ),
                (
                    f"Pending: {changed}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    "Current: Provider Disabled; Automatic Off",
                    "Current: Off",
                    None,
                    None,
                    None,
                ),
                (
                    f"Current: {baseline}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    "Current: Provider Disabled; Automatic Off",
                    "Current: Off",
                    None,
                ),
                (
                    f"Current: {baseline}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    "Current: Provider Disabled; Automatic Off",
                    "Current: Off",
                    None,
                ),
            ),
        )
        default = terminal_module.SemanticRole.DEFAULT
        muted = terminal_module.SemanticRole.MUTED
        warning = terminal_module.SemanticRole.WARNING
        self.assertEqual(
            tuple(
                tuple(choice.form_summary_role for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (muted, muted, muted, muted, muted, muted, default),
                (
                    warning,
                    muted,
                    muted,
                    muted,
                    muted,
                    muted,
                    default,
                    default,
                    default,
                ),
                (muted, muted, muted, muted, muted, muted, default),
                (muted, muted, muted, muted, muted, muted, default),
            ),
        )
        self.assertEqual(
            tuple(
                tuple(choice.description for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (None,) * 7,
                (None,) * 8 + ("Pending changes will be discarded.",),
                (None,) * 7,
                (None,) * 7,
            ),
        )
        self.assertEqual(
            tuple(
                tuple(choice.warning_suffix for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (None, None, None, None, None, None, None),
                (None, None, None, None, None, None, None, "(*Required)", None),
                (None, None, None, None, None, None, None),
                (None, None, None, None, None, None, None),
            ),
        )
        self.assertEqual(
            sum(
                call.kwargs.get("notice") == pending_notice
                for call in picker.call_args_list
            ),
            1,
        )
        self.assertTrue(
            all(
                call.kwargs.get("notice") != pending_notice
                for call in picker.call_args_list
                if call.args[1].name != "Settings"
            )
        )
        self.assertEqual(_tree_snapshot(workspace.root), before)
        save.assert_not_called()
        self.assertNotIn("[saved] Settings saved.", "".join(callbacks.records))
        self.assertIsNone(top_level_calls[-1].kwargs["highlighted_id"])

    def test_agents_root_summary_counts_every_ordered_pending_agent_value(
        self,
    ) -> None:
        workspace = self.make_workspace()
        baseline = settings_module.load_settings_state(workspace)
        edits = (
            (
                "explicit default provider",
                lambda state: settings_module.update_agent_backend(
                    state,
                    PLOT_ARCHITECT,
                    "codex",
                ),
            ),
            (
                "codex model",
                lambda state: settings_module.update_agent_codex_model(
                    state,
                    PLOT_ARCHITECT,
                    "gpt-pending",
                ),
            ),
            (
                "codex reasoning",
                lambda state: settings_module.update_agent_codex_reasoning_effort(
                    state,
                    PLOT_ARCHITECT,
                    "future_2",
                ),
            ),
            (
                "inactive antigravity model",
                lambda state: settings_module.update_agent_antigravity_model(
                    state,
                    PLOT_ARCHITECT,
                    "agy-pending",
                ),
            ),
            (
                "inactive antigravity effort",
                lambda state: settings_module.update_agent_antigravity_effort(
                    state,
                    PLOT_ARCHITECT,
                    "high",
                ),
            ),
        )

        def agents_summary(state) -> tuple[str | None, object, str | None]:
            controller, _backend, _callbacks = self.make_controller(workspace)
            interrupt = terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.INTERRUPT
            )
            with (
                _loaded_settings_state_patch(state),
                _cancel_picker_patch(interrupt) as picker,
                _storage_selection_patch_fixture() as select_storage,
            ):
                controller._run_settings()
            select_storage.assert_not_called()
            choices = picker.call_args.args[2]
            choice = next(
                choice for choice in choices if choice.identity == "providers"
            )
            return (
                choice.form_summary,
                choice.form_summary_role,
                choice.description,
            )

        self.assertEqual(
            agents_summary(baseline),
            (None, terminal_module.SemanticRole.DEFAULT, None),
        )
        for label, edit in edits:
            with self.subTest(label=label):
                self.assertEqual(
                    agents_summary(edit(baseline)),
                    (
                        "1 pending change",
                        terminal_module.SemanticRole.WARNING,
                        None,
                    ),
                )

        plural = baseline
        for _label, edit in edits:
            plural = edit(plural)
        self.assertEqual(
            agents_summary(plural),
            (
                "5 pending changes",
                terminal_module.SemanticRole.WARNING,
                None,
            ),
        )

        panel = InteractiveController._settings_panel(edits[0][1](baseline))
        current, pending, _activation = panel.items
        self.assertEqual(
            current.items[0].value,
            "codex (default) (Changed)",
        )
        self.assertEqual(pending.items[0].value, "codex (Changed)")

    def test_settings_panel_qualifies_current_and_pending_provider_routes(
        self,
    ) -> None:
        workspace = self.make_workspace()
        settings_module.establish_default_backend(workspace, "antigravity")
        state = settings_module.load_settings_state(workspace)
        changed = settings_module.update_agent_backend(
            state,
            PLOT_ARCHITECT,
            "codex",
        )
        changed = settings_module.update_agent_codex_model(
            changed,
            PLOT_ARCHITECT,
            "pending-codex-model",
        )

        panel = InteractiveController._settings_panel(changed)
        self.assertEqual(panel, self.expected_panel(changed))
        rendered = render_plain(panel, width=72).text
        compact = "".join(rendered.split())
        self.assertIn("antigravity(default)(Changed)", compact)
        self.assertIn("codex(Changed)", compact)
        self.assertIn("pending-codex-model(Changed)", compact)

    def test_settings_applied_result_is_compact_scope_truthful_and_context_aware(
        self,
    ) -> None:
        def render_result(
            *,
            changed_indexes: tuple[int, ...],
            switched_agents: tuple[str, ...],
            live_settings_changed: bool,
            storage_before: str,
            storage_after: str,
            rag_changed: bool = False,
        ) -> str:
            workspace = self.make_workspace()
            controller, backend, callbacks = self.make_controller(
                workspace,
                terminal_width=320,
            )
            before = backend.execution_profiles()
            active = list(before)
            for index in changed_indexes:
                profile = active[index]
                if profile.agent_id in switched_agents:
                    active[index] = replace(
                        profile,
                        provider=(
                            "antigravity" if profile.provider == "codex" else "codex"
                        ),
                    )
                else:
                    active[index] = replace(
                        profile,
                        model=f"changed-model-{index}",
                    )
            controller._write_settings_applied_result(
                before,
                tuple(active),
                switched_agents=switched_agents,
                live_settings_changed=live_settings_changed,
                storage_before=storage_before,
                storage_after=storage_after,
                rag_changed=rag_changed,
            )
            return " ".join("".join(callbacks.records).split())

        runtime_only = render_result(
            changed_indexes=(0,),
            switched_agents=(ROSTER[0],),
            live_settings_changed=True,
            storage_before="filesystem",
            storage_after="filesystem",
        )
        self.assertIn(
            "[saved and applied] Settings saved and applied to this running shell now.",
            runtime_only,
        )
        self.assertNotIn("Restart is required", runtime_only)
        self.assertIn(
            "[profiles changed] 1 agent profile changed. The provider switch "
            "starts a fresh thread with story-session context on the next turn. "
            "Use /agents for active values and confirmation, or /status for "
            "thread and source details.",
            runtime_only,
        )
        self.assertNotIn("bounded context", runtime_only)
        self.assertNotIn("rehydrated", runtime_only)
        self.assertNotIn("belonging story session", runtime_only)

        storage_only = render_result(
            changed_indexes=(),
            switched_agents=(),
            live_settings_changed=False,
            storage_before="filesystem",
            storage_after="sqlite",
        )
        self.assertIn(
            "[saved] Restart is required for sqlite.",
            storage_only,
        )
        self.assertNotIn("[saved and applied]", storage_only)
        self.assertNotIn("[profiles changed]", storage_only)

        rag_only = render_result(
            changed_indexes=(),
            switched_agents=(),
            live_settings_changed=False,
            storage_before="filesystem",
            storage_after="filesystem",
            rag_changed=True,
        )
        self.assertIn(
            "[saved for next shell] Conversation RAG settings were saved for "
            "the next shell. This running shell retains its current Conversation "
            "RAG service and automatic policy.",
            rag_only,
        )
        self.assertNotIn("[saved and applied]", rag_only)
        self.assertNotIn("Restart is required", rag_only)

        mixed = render_result(
            changed_indexes=(0, 1),
            switched_agents=(),
            live_settings_changed=True,
            storage_before="filesystem",
            storage_after="sqlite",
            rag_changed=True,
        )
        self.assertIn("[saved and applied] Settings saved and applied", mixed)
        self.assertIn("[saved for next shell] Conversation RAG settings", mixed)
        self.assertIn("[saved] Restart is required for sqlite.", mixed)
        self.assertIn("[profiles changed] 2 agent profiles changed.", mixed)

        for rendered in (runtime_only, storage_only, rag_only, mixed):
            with self.subTest(rendered=rendered):
                self.assertNotIn("Before", rendered)
                self.assertNotIn("Active now", rendered)
                self.assertNotIn("[active roster]", rendered)
                self.assertNotIn("[thread reuse]", rendered)
                self.assertNotIn("[pending confirmation]", rendered)
                self.assertNotIn("provider default", rendered)
                self.assertFalse(
                    any(
                        AGENT_SPECS[agent_id].display_name in rendered
                        for agent_id in ROSTER
                    )
                )

    def test_agent_and_field_choices_show_effective_current_and_pending_values(
        self,
    ) -> None:
        workspace = self.make_workspace()
        state = settings_module.load_settings_state(workspace)
        catalog = ScriptedProviderCatalog(
            ProviderCatalogDiscovery("codex", ProviderCatalogState.EMPTY)
        )
        auth = ScriptedAuthCoordinator(snapshots=())
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            auth_coordinator=auth,
            provider_catalog=catalog,
        )

        def commit(value):
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()

        provider_pending = settings_module.update_agent_backend(
            state,
            PLOT_ARCHITECT,
            "antigravity",
        )
        before_tree = _tree_snapshot(workspace.root)
        with (
            _observed_picker_patch(
                (
                    commit(PLOT_ARCHITECT),
                    commit("provider"),
                    cancel,
                    cancel,
                )
            ) as picker,
            _autospec_return_patch(InteractiveController, "_edit_settings_agent_provider",
                (provider_pending, False)) as edit_provider,
            _object_wrap_patch(subprocess, "Popen", subprocess.Popen) as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            updated, whole_cancel = controller._edit_settings_provider(state)

        self.assertFalse(whole_cancel)
        self.assertEqual(updated, provider_pending)
        edit_provider.assert_called_once_with(controller, state, PLOT_ARCHITECT)
        self.assertEqual(_tree_snapshot(workspace.root), before_tree)
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(auth.events, [])
        self.assertEqual(catalog.events, [])

        initial_agents = picker.call_args_list[0].args[2]
        self.assertEqual(
            tuple(choice.identity for choice in initial_agents),
            ROSTER,
        )
        self.assertEqual(
            tuple(choice.label for choice in initial_agents),
            tuple(AGENT_SPECS[agent_id].display_name for agent_id in ROSTER),
        )
        self.assertEqual(
            tuple(choice.form_summary for choice in initial_agents),
            ("Current: (Codex/provider default/provider default)",) * len(ROSTER),
        )
        self.assertTrue(
            all(
                choice.form_summary_role is terminal_module.SemanticRole.MUTED
                for choice in initial_agents
            )
        )
        self.assertTrue(all(choice.description is None for choice in initial_agents))

        initial_fields = picker.call_args_list[1].args[2]
        self.assertEqual(
            tuple((choice.identity, choice.label) for choice in initial_fields),
            (
                ("provider", "Provider"),
                ("model", "Model"),
                ("reasoning", "Effort"),
            ),
        )
        self.assertEqual(
            tuple(choice.form_summary for choice in initial_fields),
            (
                "Current: Codex",
                "Current: provider default",
                "Current: provider default",
            ),
        )
        self.assertEqual(
            tuple(choice.form_summary_role for choice in initial_fields),
            (terminal_module.SemanticRole.MUTED,) * 3,
        )
        self.assertTrue(all(choice.description is None for choice in initial_fields))

        redrawn_fields = picker.call_args_list[2].args[2]
        self.assertEqual(
            tuple(choice.form_summary for choice in redrawn_fields),
            (
                "Pending: Antigravity",
                "Current: provider default",
                "Current: provider default",
            ),
        )
        self.assertEqual(
            tuple(choice.form_summary_role for choice in redrawn_fields),
            (
                terminal_module.SemanticRole.WARNING,
                terminal_module.SemanticRole.MUTED,
                terminal_module.SemanticRole.MUTED,
            ),
        )
        self.assertTrue(all(choice.description is None for choice in redrawn_fields))
        redrawn_agents = picker.call_args_list[3].args[2]
        plot_choice = next(
            choice for choice in redrawn_agents if choice.identity == PLOT_ARCHITECT
        )
        self.assertEqual(
            plot_choice.form_summary,
            "Pending: (Antigravity/provider default/provider default)",
        )
        self.assertIs(
            plot_choice.form_summary_role,
            terminal_module.SemanticRole.WARNING,
        )
        self.assertIsNone(plot_choice.description)

        model_effort_pending = settings_module.update_agent_codex_model(
            state,
            PLOT_ARCHITECT,
            "gpt-pending",
        )
        model_effort_pending = settings_module.update_agent_codex_reasoning_effort(
            model_effort_pending,
            PLOT_ARCHITECT,
            "future_2",
        )
        with _observed_picker_patch((commit(PLOT_ARCHITECT), cancel, cancel)) as pending_picker:
            unchanged, whole_cancel = controller._edit_settings_provider(
                model_effort_pending
            )
        self.assertFalse(whole_cancel)
        self.assertEqual(unchanged, model_effort_pending)
        pending_fields = pending_picker.call_args_list[1].args[2]
        self.assertEqual(
            tuple(choice.form_summary for choice in pending_fields),
            (
                "Current: Codex",
                "Pending: gpt-pending",
                "Pending: future_2",
            ),
        )
        self.assertEqual(
            tuple(choice.form_summary_role for choice in pending_fields),
            (
                terminal_module.SemanticRole.MUTED,
                terminal_module.SemanticRole.WARNING,
                terminal_module.SemanticRole.WARNING,
            ),
        )
        self.assertTrue(all(choice.description is None for choice in pending_fields))

        inactive_only = settings_module.update_agent_antigravity_model(
            state,
            PLOT_ARCHITECT,
            "inactive-agy-model",
        )
        with _cancel_picker_patch(cancel) as inactive_picker:
            unchanged, whole_cancel = controller._edit_settings_provider(inactive_only)
        self.assertFalse(whole_cancel)
        self.assertEqual(unchanged, inactive_only)
        inactive_plot = next(
            choice
            for choice in inactive_picker.call_args.args[2]
            if choice.identity == PLOT_ARCHITECT
        )
        self.assertEqual(
            inactive_plot.form_summary,
            "Current: (Codex/provider default/provider default)",
        )
        self.assertIs(
            inactive_plot.form_summary_role,
            terminal_module.SemanticRole.MUTED,
        )
        self.assertIsNone(inactive_plot.description)

    def test_launch_override_fields_stay_disabled_and_show_effective_values(
        self,
    ) -> None:
        workspace = self.make_workspace()
        backend = AgentBackend(
            workspace,
            executable=sys.executable,
            backend_overrides={PLOT_ARCHITECT: "antigravity"},
            antigravity_executable=sys.executable,
            antigravity_model="agy-override",
            antigravity_effort="high",
        )
        self.addCleanup(backend.close)
        catalog = ScriptedProviderCatalog(
            ProviderCatalogDiscovery("antigravity", ProviderCatalogState.EMPTY)
        )
        auth = ScriptedAuthCoordinator(snapshots=())
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            backend=backend,
            auth_coordinator=auth,
            provider_catalog=catalog,
        )
        commit = _control_commit_text(PLOT_ARCHITECT)
        cancel = _cancel_control_outcome()
        with (
            _observed_picker_patch((commit, cancel, cancel)) as picker,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            unchanged, whole_cancel = controller._edit_settings_provider(
                settings_module.load_settings_state(workspace)
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(
            unchanged,
            settings_module.load_settings_state(workspace),
        )
        fields = picker.call_args_list[1].args[2]
        self.assertEqual(
            tuple(choice.form_summary for choice in fields),
            (
                "Current: Antigravity",
                "Current: agy-override",
                "Current: high",
            ),
        )
        self.assertEqual(tuple(choice.enabled for choice in fields), (False,) * 3)
        self.assertEqual(
            tuple(choice.disabled_reason for choice in fields),
            ("Set by a launch override; read-only in this shell.",) * 3,
        )
        self.assertEqual(
            tuple(choice.form_summary_role for choice in fields),
            (terminal_module.SemanticRole.MUTED,) * 3,
        )
        self.assertTrue(all(choice.description is None for choice in fields))
        self.assertEqual(auth.events, [])
        self.assertEqual(catalog.events, [])
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()

    def test_settings_open_is_exact_seven_item_discovery_free_snapshot(self) -> None:
        workspace = self.make_workspace()
        before = _tree_snapshot(workspace.root)
        catalog = ScriptedProviderCatalog(
            ProviderCatalogDiscovery(
                "codex",
                ProviderCatalogState.UNAVAILABLE,
            )
        )
        controller, backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "/cancel",
            "/quit",
            provider_catalog=catalog,
            terminal_width=120,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with (
            _observed_picker_patch(original_picker) as picker,
            _object_wrap_patch(controller_core_module, "parse_interactive_line",
                controller_core_module.parse_interactive_line) as parse_line,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            parse_line.call_args_list, [mock.call("/settings"), mock.call("/quit")]
        )
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(catalog.events, [])
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertIn("Current: KO", callbacks.prompts[1])
        self.assertIn("Current: default_kim_eun_hee", callbacks.prompts[1])
        self.assertIn("Current: 1", callbacks.prompts[1])
        self.assertIn("Current: filesystem", callbacks.prompts[1])
        self.assertIn("Current: Off", callbacks.prompts[1])
        settings_call = picker.call_args_list[0]
        self.assertEqual(settings_call.args[1].name, "Settings")
        self.assertIsNone(settings_call.kwargs["notice"])
        self.assertEqual(
            callbacks.prompts[1],
            "Settings\n"
            "\n"
            "Choose a setting to edit.\n"
            "\n"
            "Choices 1/7\n"
            "> 1. Language              — Current: KO\n"
            "  2. Persona               — Current: default_kim_eun_hee\n"
            "  3. Maximum debate rounds — Current: 1\n"
            "  4. Conversation storage  — Current: filesystem\n"
            "  5. Conversation RAG      — Current: Provider Disabled; Automatic Off\n"
            "  6. Message details       — Current: Off\n"
            "  7. Agents\n"
            "\n"
            "Enter a choice number, press Enter for the active choice, or enter /cancel.\n"
            "Choice > ",
        )
        self.assertEqual(
            tuple(choice.identity for choice in settings_call.args[2]),
            (
                "language",
                "persona",
                "max_debate_rounds",
                "conversation_storage",
                "conversation_rag",
                "show_message_details",
                "providers",
            ),
        )
        self.assertEqual(
            tuple(choice.label for choice in settings_call.args[2]),
            (
                "Language",
                "Persona",
                "Maximum debate rounds",
                "Conversation storage",
                "Conversation RAG",
                "Message details",
                "Agents",
            ),
        )
        self.assertEqual(
            tuple(choice.form_summary for choice in settings_call.args[2]),
            (
                "Current: KO",
                "Current: default_kim_eun_hee",
                "Current: 1",
                "Current: filesystem",
                "Current: Provider Disabled; Automatic Off",
                "Current: Off",
                None,
            ),
        )
        self.assertEqual(
            tuple(choice.form_summary_role for choice in settings_call.args[2]),
            (terminal_module.SemanticRole.MUTED,) * 6
            + (terminal_module.SemanticRole.DEFAULT,),
        )
        self.assertTrue(
            all(choice.description is None for choice in settings_call.args[2])
        )
        self.assertTrue(
            all(choice.warning_suffix is None for choice in settings_call.args[2])
        )
        self.assertEqual(settings_call.args[1].instruction, "Choose a setting to edit.")
        self.assertIs(
            settings_call.kwargs["presentation"],
            terminal_module.InputPresentation.FORM,
        )
        self.assertEqual(settings_call.kwargs["visible_choice_limit"], 7)
        self.assertNotIn("Save", callbacks.prompts[1])
        self.assertNotIn("[available] Cancel", callbacks.prompts[1])
        self.assertFalse(
            any("Discard changes?" in prompt for prompt in callbacks.prompts)
        )
        self.assertEqual(
            "".join(callbacks.records).count(
                "[cancelled] Pending changes discarded.\n"
            ),
            0,
        )
        self.assertTrue(backend._closed)

    def test_provider_navigation_does_not_discover_until_model_or_effort(self) -> None:
        workspace = self.make_workspace()
        catalog = ScriptedProviderCatalog(
            ProviderCatalogDiscovery("codex", ProviderCatalogState.EMPTY)
        )
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            "/settings",
            "7",
            "2",
            "1",
            "1",
            "/cancel",
            "/cancel",
            "/cancel",
            "9",
            "1",
            "/quit",
            provider_catalog=catalog,
            terminal_width=80,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(catalog.events, [])

    def test_provider_model_and_effort_option_reads_use_transient_progress(
        self,
    ) -> None:
        workspace = self.make_workspace()
        authenticated = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(authenticated,),
            results=(),
        )
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog(
                "codex",
                ProviderModel(
                    "gpt-progress",
                    "GPT Progress",
                    None,
                    "medium",
                    (ProviderEffort("medium", "medium"),),
                ),
            )
        )
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            auth_coordinator=coordinator,
            provider_catalog=catalog,
        )
        progress_events: list[str] = []

        async def observe_progress(operation):
            progress_events.append("started")
            result = await operation
            progress_events.append("completed")
            return result

        controller._advisory_progress._run = observe_progress
        controller._runner = asyncio.Runner()
        cancel = _cancel_control_outcome()
        state = settings_module.load_settings_state(workspace)
        try:
            with _observed_picker_patch((cancel, cancel, cancel)):
                controller._edit_settings_agent_provider(state, PLOT_ARCHITECT)
                controller._edit_settings_agent_model(state, PLOT_ARCHITECT)
                controller._edit_settings_agent_reasoning(state, PLOT_ARCHITECT)
        finally:
            controller._runner.close()
            controller._runner = None

        self.assertEqual(
            progress_events,
            ["started", "completed"] * 3,
        )
        self.assertEqual(
            coordinator.events,
            [("probe_all", None), ("choices", "codex")],
        )
        self.assertEqual(catalog.events, ["codex", "codex"])

    def test_simple_value_editors_reopen_with_latest_notice_and_selection(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        state = settings_module.load_settings_state(workspace)
        persona_values = tuple(entry.name for entry in state.baseline.persona_catalog)
        self.assertTrue(persona_values)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()
        cases = (
            (
                "Language",
                controller._edit_settings_language,
                ("EN", "JA"),
                lambda candidate: candidate.pending.system_defaults.language,
            ),
            (
                "Persona",
                controller._edit_settings_persona,
                (persona_values[0], persona_values[0]),
                lambda candidate: candidate.pending.system_defaults.persona,
            ),
        )
        for field, editor, values, pending_value in cases:
            with self.subTest(field=field):
                with _observed_picker_patch((commit(values[0]), commit(values[1]), cancel)) as picker:
                    updated, whole_cancel = editor(state)

                self.assertFalse(whole_cancel)
                self.assertEqual(pending_value(updated), values[1])
                expected_calls = 2 if values[0] == values[1] else 3
                self.assertEqual(picker.call_count, expected_calls)
                self.assertIn(
                    "Enter applies", picker.call_args_list[0].args[1].instruction
                )
                self.assertIn("press Esc", picker.call_args_list[0].args[1].instruction)
                self.assert_value_save_guidance(
                    picker.call_args_list[0].args[1].instruction
                )
                self.assertEqual(
                    picker.call_args_list[1].kwargs["notice"],
                    self.expected_entered_notice(field, values[0]),
                )
                last_call = picker.call_args_list[-1]
                self.assertEqual(
                    last_call.kwargs["notice"],
                    self.expected_entered_notice(field, values[1]),
                )
                self.assertEqual(
                    (
                        last_call.kwargs["highlighted_id"],
                        last_call.kwargs["selected_id"],
                    ),
                    (values[1], values[1]),
                )

    def test_message_details_editor_retains_exact_off_on_choices(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        baseline = settings_module.load_settings_state(workspace)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return _control_commit_text(value)

        cases = (
            (baseline, "off", "on", "On", True),
            (
                settings_module.update_show_message_details(baseline, True),
                "on",
                "off",
                "Off",
                False,
            ),
        )
        choices = (
            terminal_module.Choice("off", "Off"),
            terminal_module.Choice("on", "On"),
        )
        for state, initial, selected, display, expected in cases:
            with self.subTest(selected=selected):
                with _observed_picker_patch((commit(selected), commit(selected))) as picker:
                    updated, whole_cancel = controller._edit_settings_message_details(
                        state
                    )

                self.assertFalse(whole_cancel)
                self.assertIs(
                    updated.pending.system_defaults.show_message_details,
                    expected,
                )
                self.assertEqual(picker.call_count, 2)
                self.assertTrue(
                    all(call.args[2] == choices for call in picker.call_args_list)
                )
                self.assertEqual(
                    (
                        picker.call_args_list[0].kwargs["highlighted_id"],
                        picker.call_args_list[0].kwargs["selected_id"],
                    ),
                    (initial, initial),
                )
                second = picker.call_args_list[1]
                self.assertEqual(
                    (
                        second.kwargs["highlighted_id"],
                        second.kwargs["selected_id"],
                    ),
                    (selected, selected),
                )
                self.assertEqual(
                    second.kwargs["notice"],
                    self.expected_entered_notice("message details", display),
                )
                prompt = picker.call_args_list[0].args[1]
                self.assertEqual(prompt.name, "Message details")
                self.assertIn("Choose Off for compact messages", prompt.instruction)
                self.assertIn("Enter applies", prompt.instruction)
                self.assertIn("press Esc", prompt.instruction)
                self.assert_value_save_guidance(prompt.instruction)

    def test_rounds_editor_retains_valid_value_and_clears_stale_success(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()
        with _observed_prompt_patch((commit("2"), commit("bad"), commit("3"), cancel)) as prompt:
            updated, whole_cancel = controller._edit_settings_rounds(
                settings_module.load_settings_state(workspace)
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(updated.pending.system_defaults.max_debate_rounds, 3)
        self.assertEqual(prompt.call_count, 4)
        self.assertTrue(
            all(
                call.kwargs["presentation"] is terminal_module.InputPresentation.FORM
                for call in prompt.call_args_list
            )
        )
        self.assertIn("Enter applies", prompt.call_args_list[0].args[1].instruction)
        self.assertIn("press Esc", prompt.call_args_list[0].args[1].instruction)
        self.assert_value_save_guidance(prompt.call_args_list[0].args[1].instruction)
        self.assertEqual(
            prompt.call_args_list[1].kwargs["context"],
            (self.expected_entered_notice("Maximum debate rounds", "2"),),
        )
        self.assertEqual(prompt.call_args_list[2].kwargs["context"], ())
        self.assertTrue(
            all(call.args[1].default is None for call in prompt.call_args_list)
        )
        self.assertEqual(
            tuple(call.kwargs["initial_value"] for call in prompt.call_args_list),
            ("1", "2", "2", "3"),
        )
        self.assertEqual(
            prompt.call_args_list[3].kwargs["context"],
            (self.expected_entered_notice("Maximum debate rounds", "3"),),
        )
        self.assertEqual(
            "".join(callbacks.records).count(
                "[validation] Maximum debate rounds must be a nonnegative "
                "ASCII integer.\n"
            ),
            1,
        )

        with _observed_prompt_patch((commit("1"), commit("1"))) as unchanged_prompt:
            unchanged, whole_cancel = controller._edit_settings_rounds(
                settings_module.load_settings_state(workspace)
            )
        self.assertFalse(whole_cancel)
        self.assertEqual(
            unchanged.pending.system_defaults.max_debate_rounds,
            1,
        )
        self.assertEqual(unchanged_prompt.call_count, 2)
        self.assertEqual(
            tuple(
                call.kwargs["initial_value"] for call in unchanged_prompt.call_args_list
            ),
            ("1", "1"),
        )

    def test_provider_editor_reopens_with_latest_notice_and_selection(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        commit_antigravity = _control_commit_text("antigravity")
        commit_codex = _control_commit_text("codex")
        cancel = _cancel_control_outcome()
        with _observed_picker_patch((commit_antigravity, commit_codex, cancel)) as picker:
            updated, whole_cancel = controller._edit_settings_agent_provider(
                settings_module.load_settings_state(workspace),
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(
            updated.pending.agent_configs[PLOT_ARCHITECT].backend,
            "codex",
        )
        self.assert_value_save_guidance(picker.call_args_list[0].args[1].instruction)
        self.assertEqual(
            picker.call_args_list[1].kwargs["notice"],
            self.expected_entered_notice("Provider", "Antigravity"),
        )
        self.assertEqual(
            picker.call_args_list[2].kwargs["notice"],
            self.expected_entered_notice("Provider", "Codex"),
        )
        self.assertEqual(
            (
                picker.call_args_list[2].kwargs["highlighted_id"],
                picker.call_args_list[2].kwargs["selected_id"],
            ),
            ("codex", "codex"),
        )

        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        authenticated_controller, _backend, _callbacks = self.make_controller(
            workspace,
            auth_coordinator=ScriptedAuthCoordinator(snapshots=(codex_only,)),
        )
        with _cancel_picker_patch(cancel) as authenticated_picker:
            unchanged, whole_cancel = (
                authenticated_controller._edit_settings_agent_provider(
                    settings_module.load_settings_state(workspace),
                    PLOT_ARCHITECT,
                )
            )
        self.assertFalse(whole_cancel)
        self.assertEqual(
            unchanged,
            settings_module.load_settings_state(workspace),
        )
        self.assert_value_save_guidance(
            authenticated_picker.call_args.args[1].instruction
        )

    def test_all_value_editors_double_commit_and_reentry_are_call_local(
        self,
    ) -> None:
        workspace = self.make_workspace()
        models = (
            ProviderModel(
                "gpt-a",
                "GPT Alpha",
                None,
                "low",
                (
                    ProviderEffort("low", "low"),
                    ProviderEffort("high", "high"),
                ),
            ),
            ProviderModel(
                "gpt-b",
                "GPT Beta",
                None,
                "high",
                (
                    ProviderEffort("low", "low"),
                    ProviderEffort("high", "high"),
                ),
            ),
        )
        catalog = ScriptedProviderCatalog(_ready_provider_catalog("codex", *models))
        controller, _backend, callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
        state = settings_module.load_settings_state(workspace)
        persona_values = tuple(entry.name for entry in state.baseline.persona_catalog)
        self.assertTrue(persona_values)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()

        def exercise_picker(
            label,
            candidate,
            editor,
            first,
            second,
            expected_value,
            pending_value,
            updater_name,
        ):
            with self.subTest(editor=label, phase="armed"):
                updater = getattr(controller_core_module, updater_name)
                with (
                    _observed_picker_patch(
                        (
                            commit(first),
                            commit(second),
                            commit(second),
                        ),
                    ) as picker,
                    _object_wrap_patch(controller_core_module, updater_name, updater) as update,
                ):
                    updated, whole_cancel = editor(candidate)
                self.assertFalse(whole_cancel)
                self.assertEqual(picker.call_count, 3)
                self.assertEqual(update.call_count, 2)
                self.assertEqual(pending_value(updated), expected_value)

            with self.subTest(editor=label, phase="reentry"):
                updater = getattr(controller_core_module, updater_name)
                with (
                    _observed_picker_patch((commit(second), cancel)) as picker,
                    _object_wrap_patch(controller_core_module, updater_name, updater) as update,
                ):
                    reentered, whole_cancel = editor(updated)
                self.assertFalse(whole_cancel)
                self.assertEqual(picker.call_count, 2)
                self.assertEqual(update.call_count, 1)
                self.assertEqual(reentered, updated)
            return updated

        with (
            _fresh_literal_object_none(controller_core_module, 'prepare_settings_save') as prepare,
            _storage_selection_patch_fixture() as select_storage,
        ):
            state = exercise_picker(
                "Language",
                state,
                controller._edit_settings_language,
                "EN",
                "JA",
                "JA",
                lambda candidate: candidate.pending.system_defaults.language,
                "update_language",
            )
            with (
                _observed_picker_patch(
                    (
                        commit(persona_values[0]),
                        commit(persona_values[0]),
                    ),
                ) as picker,
                _object_wrap_patch(controller_core_module, "update_persona",
                    controller_core_module.update_persona) as update,
            ):
                persona_state, whole_cancel = controller._edit_settings_persona(state)
            self.assertFalse(whole_cancel)
            self.assertEqual(picker.call_count, 2)
            self.assertEqual(update.call_count, 1)
            with (
                _observed_picker_patch((commit(persona_values[0]), cancel)) as picker,
                _object_wrap_patch(controller_core_module, "update_persona",
                    controller_core_module.update_persona) as update,
            ):
                reentered, whole_cancel = controller._edit_settings_persona(
                    persona_state
                )
            self.assertFalse(whole_cancel)
            self.assertEqual(picker.call_count, 2)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(reentered, persona_state)
            state = persona_state
            state = exercise_picker(
                "Provider",
                state,
                lambda candidate: controller._edit_settings_agent_provider(
                    candidate,
                    PLOT_ARCHITECT,
                ),
                "antigravity",
                "codex",
                "codex",
                lambda candidate: candidate.pending.agent_configs[
                    PLOT_ARCHITECT
                ].backend,
                "update_agent_backend",
            )
            state = exercise_picker(
                "Model",
                state,
                lambda candidate: controller._edit_settings_agent_model(
                    candidate,
                    PLOT_ARCHITECT,
                ),
                "settings:model:0",
                "settings:model:1",
                "gpt-b",
                lambda candidate: candidate.pending.agent_configs[
                    PLOT_ARCHITECT
                ].codex_model,
                "update_agent_codex_model",
            )
            self.assertEqual(
                state.pending.agent_configs[PLOT_ARCHITECT].codex_model,
                "gpt-b",
            )
            state = exercise_picker(
                "Effort",
                state,
                lambda candidate: controller._edit_settings_agent_reasoning(
                    candidate,
                    PLOT_ARCHITECT,
                ),
                "settings:effort:0",
                "settings:effort:1",
                "high",
                lambda candidate: candidate.pending.agent_configs[
                    PLOT_ARCHITECT
                ].codex_reasoning_effort,
                "update_agent_codex_reasoning_effort",
            )
            self.assertEqual(
                state.pending.agent_configs[PLOT_ARCHITECT].codex_reasoning_effort,
                "high",
            )

            with (
                _observed_prompt_patch((commit("02"), commit("3"), commit("03"))) as prompt,
                _object_wrap_patch(controller_core_module, "update_max_debate_rounds",
                    controller_core_module.update_max_debate_rounds) as update,
            ):
                state, whole_cancel = controller._edit_settings_rounds(state)
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 2)
            self.assertEqual(state.pending.system_defaults.max_debate_rounds, 3)

            with (
                _observed_prompt_patch((commit("bad"), commit("03"), commit("3"))) as prompt,
                _object_wrap_patch(controller_core_module, "update_max_debate_rounds",
                    controller_core_module.update_max_debate_rounds) as update,
            ):
                reentered, whole_cancel = controller._edit_settings_rounds(state)
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(reentered, state)

            with (
                _observed_prompt_patch((commit("custom-a"), commit(""), commit(""))) as prompt,
                _object_wrap_patch(controller_core_module, "update_agent_codex_model",
                    controller_core_module.update_agent_codex_model) as update,
            ):
                state, whole_cancel = controller._edit_settings_custom_model(
                    state,
                    PLOT_ARCHITECT,
                    "codex",
                )
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 2)
            self.assertIsNone(state.pending.agent_configs[PLOT_ARCHITECT].codex_model)

            with (
                _observed_prompt_patch((commit("-unsafe"), commit(""), commit(""))) as prompt,
                _object_wrap_patch(controller_core_module, "update_agent_codex_model",
                    controller_core_module.update_agent_codex_model) as update,
            ):
                reentered, whole_cancel = controller._edit_settings_custom_model(
                    state,
                    PLOT_ARCHITECT,
                    "codex",
                )
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 2)
            self.assertEqual(reentered, state)
            prepare.assert_not_called()
            select_storage.assert_not_called()

        self.assertEqual(catalog.events, ["codex"] * 4)
        rendered = "".join(callbacks.records)
        self.assertEqual(
            rendered.count(
                "[validation] Maximum debate rounds must be a nonnegative "
                "ASCII integer.\n"
            ),
            1,
        )
        self.assertEqual(
            rendered.count(
                "[validation] Codex model must be safe nonempty text, or "
                "blank to inherit.\n"
            ),
            1,
        )

    def test_nested_agent_editors_return_one_level_and_retain_highlights(
        self,
    ) -> None:
        workspace = self.make_workspace()
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog(
                "antigravity",
                ProviderModel(
                    "agy-a",
                    "AGY Alpha",
                    None,
                    None,
                    (ProviderEffort("high", "high"),),
                ),
            )
        )
        controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)

        def commit(value):
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()
        picker_outcomes = (
            commit(PLOT_ARCHITECT),
            commit("provider"),
            cancel,
            commit("model"),
            cancel,
            commit("reasoning"),
            cancel,
            cancel,
            commit(ROSTER[0]),
            cancel,
            cancel,
        )
        with _observed_picker_patch(picker_outcomes) as picker:
            updated, whole_cancel = controller._edit_settings_provider(
                settings_module.load_settings_state(workspace)
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(
            updated,
            settings_module.load_settings_state(workspace),
        )
        calls = picker.call_args_list
        self.assertEqual(
            tuple(call.args[1].name for call in calls),
            (
                "Agents",
                "Agent settings",
                "Provider",
                "Agent settings",
                "Model",
                "Agent settings",
                "Effort",
                "Agent settings",
                "Agents",
                "Agent settings",
                "Agents",
            ),
        )
        self.assertIn("press Esc to return to Settings", calls[0].args[1].instruction)
        self.assertEqual(
            tuple(calls[index].kwargs["highlighted_id"] for index in (0, 8, 10)),
            (ROSTER[0], PLOT_ARCHITECT, ROSTER[0]),
        )
        self.assertEqual(
            tuple(calls[index].kwargs["highlighted_id"] for index in (1, 3, 5, 7, 9)),
            (
                "provider",
                "provider",
                "model",
                "reasoning",
                "provider",
            ),
        )
        self.assertEqual(catalog.events, ["codex", "codex"])

    def test_model_picker_exact_order_identities_highlights_and_custom_fallback(
        self,
    ) -> None:
        workspace = self.make_workspace()
        models = (
            ProviderModel(
                "gpt-a",
                "GPT Alpha",
                "First model",
                "low",
                (ProviderEffort("low", "low", "Quick"),),
            ),
            ProviderModel(
                "gpt-b",
                "GPT Beta",
                None,
                "future_2",
                (
                    ProviderEffort("low", "low"),
                    ProviderEffort("future_2", "future_2", "Future mode"),
                ),
            ),
        )
        catalog = ScriptedProviderCatalog(_ready_provider_catalog("codex", *models))
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
            terminal_width=48,
        )
        state = settings_module.update_agent_antigravity_model(
            settings_module.update_agent_codex_model(
                settings_module.load_settings_state(workspace),
                PLOT_ARCHITECT,
                "legacy-custom",
            ),
            PLOT_ARCHITECT,
            "inactive-agy",
        )
        commit_gpt_b = _control_commit_text("settings:model:1")
        commit_gpt_a = _control_commit_text("settings:model:0")
        cancel = _cancel_control_outcome()
        with _observed_picker_patch((commit_gpt_b, commit_gpt_a, cancel)) as picker:
            updated, whole_cancel = controller._edit_settings_agent_model(
                state,
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(catalog.events, ["codex"])
        call = picker.call_args_list[0]
        prompt = call.args[1]
        choices = call.args[2]
        self.assertEqual(prompt.name, "Model")
        self.assertIn("Codex", prompt.scope)
        self.assert_value_save_guidance(prompt.instruction)
        self.assertEqual(
            tuple(choice.identity for choice in choices),
            (
                "settings:inherit",
                "settings:model:0",
                "settings:model:1",
                "settings:model:current-custom",
                "settings:model:custom",
            ),
        )
        self.assertEqual(
            tuple(choice.label for choice in choices),
            ("Inherit", "GPT Alpha", "GPT Beta", "Current custom", "Custom model"),
        )
        self.assertEqual(choices[1].description, "gpt-a — First model")
        self.assertEqual(
            choices[3].description,
            "Exact pending model: legacy-custom",
        )
        self.assertEqual(call.kwargs["highlighted_id"], "settings:model:current-custom")
        self.assertEqual(call.kwargs["selected_id"], "settings:model:current-custom")
        self.assertTrue(call.kwargs["transient"])
        agent = updated.pending.agent_configs[PLOT_ARCHITECT]
        self.assertEqual(agent.codex_model, "gpt-a")
        self.assertEqual(agent.antigravity_model, "inactive-agy")
        self.assertEqual(
            picker.call_args_list[1].kwargs["notice"],
            self.expected_entered_notice("Model", "GPT Beta"),
        )
        self.assertEqual(
            picker.call_args_list[2].kwargs["notice"],
            self.expected_entered_notice("Model", "GPT Alpha"),
        )
        self.assertEqual(
            (
                picker.call_args_list[2].kwargs["highlighted_id"],
                picker.call_args_list[2].kwargs["selected_id"],
            ),
            ("settings:model:0", "settings:model:0"),
        )

        selected_state = settings_module.update_agent_codex_model(
            state,
            PLOT_ARCHITECT,
            "gpt-b",
        )
        cancel = _cancel_control_outcome()
        with _cancel_picker_patch(cancel) as picker:
            unchanged, whole_cancel = controller._edit_settings_agent_model(
                selected_state,
                PLOT_ARCHITECT,
            )
        self.assertFalse(whole_cancel)
        self.assertEqual(unchanged, selected_state)
        choices = picker.call_args.args[2]
        self.assertEqual(
            tuple(choice.identity for choice in choices),
            (
                "settings:inherit",
                "settings:model:0",
                "settings:model:1",
                "settings:model:custom",
            ),
        )
        self.assertEqual(picker.call_args.kwargs["selected_id"], "settings:model:1")

    def test_custom_model_cancel_reuses_picker_without_mutation_or_rediscovery(
        self,
    ) -> None:
        workspace = self.make_workspace()
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog(
                "codex",
                ProviderModel("gpt-a", "GPT Alpha", None, None, ()),
            )
        )
        controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
        state = settings_module.update_agent_codex_model(
            settings_module.load_settings_state(workspace),
            PLOT_ARCHITECT,
            "existing-custom",
        )
        choose_custom = _control_commit_text("settings:model:custom")
        cancel = _cancel_control_outcome()
        with (
            _observed_picker_patch((choose_custom, cancel)) as picker,
            _autospec_return_patch(terminal_module.InteractivePromptInput, "read_prompt", cancel) as prompt,
        ):
            unchanged, whole_cancel = controller._edit_settings_agent_model(
                state,
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(unchanged, state)
        self.assertEqual(catalog.events, ["codex"])
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(picker.call_count, 2)
        returned_picker = picker.call_args_list[1]
        self.assertEqual(returned_picker.args[1].name, "Model")
        self.assertEqual(
            returned_picker.kwargs["highlighted_id"],
            "settings:model:custom",
        )
        self.assertEqual(
            tuple(choice.identity for choice in returned_picker.args[2]),
            (
                "settings:inherit",
                "settings:model:0",
                "settings:model:current-custom",
                "settings:model:custom",
            ),
        )

    def test_custom_model_reopens_replaces_notice_and_clears_it_on_validation(
        self,
    ) -> None:
        workspace = self.make_workspace()
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog(
                "codex",
                ProviderModel("gpt-a", "GPT Alpha", None, None, ()),
            )
        )
        controller, _backend, callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return _control_commit_text(value)

        cancel = _cancel_control_outcome()
        with (
            _observed_picker_patch((commit("settings:model:custom"), cancel)) as picker,
            _observed_prompt_patch(
                (
                    commit("first-custom"),
                    commit("-unsafe"),
                    commit(""),
                    commit("/cancel"),
                    cancel,
                ),
            ) as prompt,
        ):
            updated, whole_cancel = controller._edit_settings_agent_model(
                settings_module.load_settings_state(workspace),
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(
            updated.pending.agent_configs[PLOT_ARCHITECT].codex_model,
            "/cancel",
        )
        self.assertEqual(catalog.events, ["codex"])
        self.assertEqual(picker.call_count, 2)
        self.assertEqual(prompt.call_count, 5)
        self.assert_value_save_guidance(prompt.call_args_list[0].args[1].instruction)
        self.assertEqual(
            prompt.call_args_list[1].kwargs["context"],
            (self.expected_entered_notice("Custom model", "first-custom"),),
        )
        self.assertEqual(prompt.call_args_list[2].kwargs["context"], ())
        self.assertEqual(prompt.call_args_list[2].args[1].default, "first-custom")
        self.assertEqual(
            prompt.call_args_list[3].kwargs["context"],
            (self.expected_entered_notice("Custom model", "Inherit"),),
        )
        self.assertEqual(
            prompt.call_args_list[4].kwargs["context"],
            (self.expected_entered_notice("Custom model", "/cancel"),),
        )
        self.assertEqual(
            "".join(callbacks.records).count(
                "[validation] Codex model must be safe nonempty text, or "
                "blank to inherit.\n"
            ),
            1,
        )

    def test_effort_picker_uses_selected_codex_model_and_current_compatibility(
        self,
    ) -> None:
        workspace = self.make_workspace()
        models = (
            ProviderModel(
                "gpt-a",
                "GPT Alpha",
                None,
                "low",
                (ProviderEffort("low", "low"),),
            ),
            ProviderModel(
                "gpt-b",
                "GPT Beta",
                None,
                "future_2",
                (
                    ProviderEffort("low", "low", "Quick"),
                    ProviderEffort("future_2", "future_2", "Future mode"),
                ),
            ),
        )
        catalog = ScriptedProviderCatalog(_ready_provider_catalog("codex", *models))
        controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
        state = settings_module.update_agent_codex_reasoning_effort(
            settings_module.update_agent_codex_model(
                settings_module.load_settings_state(workspace),
                PLOT_ARCHITECT,
                "gpt-b",
            ),
            PLOT_ARCHITECT,
            "future_2",
        )
        choose_low = _control_commit_text("settings:effort:0")
        choose_future = _control_commit_text("settings:effort:1")
        cancel = _cancel_control_outcome()
        with _observed_picker_patch((choose_low, choose_future, cancel)) as picker:
            updated, whole_cancel = controller._edit_settings_agent_reasoning(
                state,
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(catalog.events, ["codex"])
        choices = picker.call_args_list[0].args[2]
        self.assert_value_save_guidance(picker.call_args_list[0].args[1].instruction)
        self.assertEqual(
            tuple(choice.identity for choice in choices),
            ("settings:inherit", "settings:effort:0", "settings:effort:1"),
        )
        self.assertEqual(
            tuple(choice.label for choice in choices),
            ("Inherit", "low", "future_2"),
        )
        self.assertEqual(
            choices[2].description,
            "Future mode Default for the selected model.",
        )
        self.assertEqual(
            picker.call_args_list[0].kwargs["selected_id"],
            "settings:effort:1",
        )
        self.assertEqual(
            updated.pending.agent_configs[PLOT_ARCHITECT].codex_reasoning_effort,
            "future_2",
        )
        self.assertEqual(
            picker.call_args_list[1].kwargs["notice"],
            self.expected_entered_notice("Effort", "low"),
        )
        self.assertEqual(
            picker.call_args_list[2].kwargs["notice"],
            self.expected_entered_notice("Effort", "future_2"),
        )
        self.assertEqual(
            (
                picker.call_args_list[2].kwargs["highlighted_id"],
                picker.call_args_list[2].kwargs["selected_id"],
            ),
            ("settings:effort:1", "settings:effort:1"),
        )

        compatibility = settings_module.update_agent_codex_reasoning_effort(
            state,
            PLOT_ARCHITECT,
            "legacy_mode",
        )
        keep_current = _control_commit_text("settings:effort:current-compatibility")
        with _observed_picker_patch((keep_current, cancel)) as picker:
            unchanged, whole_cancel = controller._edit_settings_agent_reasoning(
                compatibility,
                PLOT_ARCHITECT,
            )
        self.assertFalse(whole_cancel)
        self.assertEqual(unchanged, compatibility)
        choices = picker.call_args_list[0].args[2]
        self.assertEqual(choices[-1].identity, "settings:effort:current-compatibility")
        self.assertEqual(choices[-1].label, "Current compatibility")
        self.assertEqual(
            choices[-1].description,
            "Keep exact pending effort: legacy_mode",
        )
        self.assertEqual(
            picker.call_args_list[0].kwargs["highlighted_id"],
            "settings:effort:current-compatibility",
        )
        self.assertEqual(
            picker.call_args_list[1].kwargs["notice"],
            self.expected_entered_notice("Effort", "legacy_mode"),
        )

    def test_pending_provider_precedence_controls_discovery_without_mutation(
        self,
    ) -> None:
        workspace = self.make_workspace()
        settings_module.establish_default_backend(workspace, "antigravity")
        agy_model = ProviderModel(
            "agy-model",
            "Antigravity Model",
            None,
            None,
            (
                ProviderEffort("low", "low"),
                ProviderEffort("medium", "medium"),
                ProviderEffort("high", "high"),
            ),
        )
        codex_model = ProviderModel("codex-model", "Codex Model", None, None, ())
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog("antigravity", agy_model),
            _ready_provider_catalog("codex", codex_model),
        )
        controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
        cancel = _cancel_control_outcome()
        state = settings_module.load_settings_state(workspace)
        with _cancel_picker_patch(cancel):
            inherited, inherited_cancel = controller._edit_settings_agent_model(
                state,
                PLOT_ARCHITECT,
            )
            pending = settings_module.update_agent_backend(
                state,
                PLOT_ARCHITECT,
                "codex",
            )
            overridden, overridden_cancel = controller._edit_settings_agent_model(
                pending,
                PLOT_ARCHITECT,
            )
        self.assertEqual(catalog.events, ["antigravity", "codex"])
        self.assertEqual(inherited, state)
        self.assertEqual(overridden, pending)
        self.assertFalse(inherited_cancel)
        self.assertFalse(overridden_cancel)

    def test_catalog_failure_notices_are_fixed_for_model_and_effort_pickers(
        self,
    ) -> None:
        state_copy = {
            ProviderCatalogState.UNAVAILABLE: "is unavailable",
            ProviderCatalogState.TIMED_OUT: "timed out",
            ProviderCatalogState.COMMAND_FAILED: "did not complete successfully",
            ProviderCatalogState.MALFORMED: "returned an unsupported format",
            ProviderCatalogState.EMPTY: "returned no visible models",
            ProviderCatalogState.OVERSIZED: "exceeded the safe output limit",
            ProviderCatalogState.FAILED: "could not be completed",
        }
        for discovery_state, detail in state_copy.items():
            with self.subTest(state=discovery_state):
                workspace = self.make_workspace()
                discovery = ProviderCatalogDiscovery("codex", discovery_state)
                catalog = ScriptedProviderCatalog(discovery)
                controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
                state = settings_module.load_settings_state(workspace)
                cancel = _cancel_control_outcome()
                with _observed_picker_patch((cancel, cancel)) as picker:
                    controller._edit_settings_agent_model(state, PLOT_ARCHITECT)
                    controller._edit_settings_agent_reasoning(state, PLOT_ARCHITECT)
                model_notice = picker.call_args_list[0].kwargs["notice"]
                effort_notice = picker.call_args_list[1].kwargs["notice"]
                self.assertEqual(
                    model_notice.text,
                    f"Codex catalog discovery {detail}. Inherit, keep the current "
                    "custom value, or enter a custom model. Retry later.",
                )
                self.assertEqual(
                    effort_notice.text,
                    f"Codex catalog discovery {detail}. Inherit or keep the "
                    "current compatibility value. Retry later.",
                )
                self.assertIs(model_notice.role, terminal_module.SemanticRole.WARNING)
                self.assertIs(effort_notice.role, terminal_module.SemanticRole.WARNING)
                self.assertEqual(catalog.events, ["codex", "codex"])

        hostile_outcomes = (
            RuntimeError("PRIVATE_CATALOG_FAILURE"),
            object(),
            ProviderCatalogDiscovery("antigravity", ProviderCatalogState.EMPTY),
        )
        for outcome in hostile_outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                workspace = self.make_workspace()
                catalog = ScriptedProviderCatalog(outcome)
                controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
                with _cancel_picker_patch(_cancel_control_outcome()) as picker:
                    controller._edit_settings_agent_model(
                        settings_module.load_settings_state(workspace),
                        PLOT_ARCHITECT,
                    )
                notice = picker.call_args.kwargs["notice"]
                self.assertIn("could not be completed", notice.text)
                self.assertNotIn("PRIVATE_CATALOG_FAILURE", notice.text)

    def test_model_and_effort_cancel_return_while_interrupt_and_eof_cancel_all(
        self,
    ) -> None:
        model = ProviderModel("gpt", "GPT", None, None, ())
        for kind in (
            terminal_module.ControlOutcomeKind.CANCEL,
            terminal_module.ControlOutcomeKind.INTERRUPT,
            terminal_module.ControlOutcomeKind.EOF,
        ):
            with self.subTest(kind=kind):
                workspace = self.make_workspace()
                catalog = ScriptedProviderCatalog(
                    _ready_provider_catalog("codex", model)
                )
                controller, _backend, _callbacks = _catalog_settings_controller_fixture(self, workspace, catalog)
                state = settings_module.load_settings_state(workspace)
                outcome = terminal_module.ControlOutcome(kind)
                with _observed_picker_patch((outcome, outcome)):
                    model_state, model_cancel = controller._edit_settings_agent_model(
                        state,
                        PLOT_ARCHITECT,
                    )
                    effort_state, effort_cancel = (
                        controller._edit_settings_agent_reasoning(
                            state,
                            PLOT_ARCHITECT,
                        )
                    )
                expected_cancel = kind in {
                    terminal_module.ControlOutcomeKind.INTERRUPT,
                    terminal_module.ControlOutcomeKind.EOF,
                }
                self.assertEqual(model_state, state)
                self.assertEqual(effort_state, state)
                self.assertIs(model_cancel, expected_cancel)
                self.assertIs(effort_cancel, expected_cancel)

    def test_interrupt_and_eof_cancel_once_from_every_nested_settings_level(
        self,
    ) -> None:
        paths = {
            "agents": ("7",),
            "agent-settings": ("7", "2"),
            "provider": ("7", "2", "1"),
            "model": ("7", "2", "2"),
            "effort": ("7", "2", "3"),
            "custom-model": ("7", "2", "2", "3"),
        }
        for dirty in (False, True):
            for failure_type in (KeyboardInterrupt, EOFError):
                for level, path in paths.items():
                    with self.subTest(
                        dirty=dirty,
                        control=failure_type.__name__,
                        level=level,
                    ):
                        prefix = ("1", "2", "/cancel") if dirty else ()
                        workspace = self.make_workspace()
                        before = _tree_snapshot(workspace.root)
                        catalog = ScriptedProviderCatalog(
                            _ready_provider_catalog(
                                "codex",
                                ProviderModel("gpt-a", "GPT Alpha", None, None, ()),
                            )
                        )
                        controller, _backend, callbacks = self.make_controller(
                            workspace,
                            "/settings",
                            *prefix,
                            *path,
                            failure_type(),
                            "/quit",
                            provider_catalog=catalog,
                            terminal_width=80,
                        )
                        self.assertEqual(controller.run_session_lobby(), 0)
                        self.assertEqual(_tree_snapshot(workspace.root), before)
                        self.assertEqual(
                            "".join(callbacks.records).count(
                                "[cancelled] Pending changes discarded.\n"
                            ),
                            1 if dirty else 0,
                        )
                        self.assertEqual(callbacks.reads, [])

    def test_catalog_process_controls_propagate_from_controller_collaborator(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for failure in (KeyboardInterrupt("catalog"), SystemExit("catalog")):
            with self.subTest(failure=type(failure).__name__):
                controller, _backend, _callbacks = self.make_controller(
                    workspace,
                    provider_catalog=ScriptedProviderCatalog(failure),
                )
                with self.assertRaises(type(failure)) as caught:
                    controller._discover_settings_catalog("codex")
                self.assertIs(caught.exception, failure)

    def test_settings_routes_picker_and_prompt_inputs_as_transient_only(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            "/settings",
            "3",
            "0",
            "\x1b",
            "8",
            "1",
            "/quit",
            terminal_width=120,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        original_prompt = terminal_module.InteractivePromptInput.read_prompt
        with (
            _observed_picker_patch(original_picker) as picker,
            _observed_prompt_patch(original_prompt) as prompt,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertGreaterEqual(len(picker.call_args_list), 2)
        self.assertTrue(
            all(call.kwargs.get("transient") is True for call in picker.call_args_list)
        )
        self.assertTrue(
            all(
                call.kwargs["presentation"] is terminal_module.InputPresentation.FORM
                for call in picker.call_args_list
            )
        )
        confirmation_calls = [
            call
            for call in picker.call_args_list
            if call.args[1].name in {"Apply changes?", "Discard changes?"}
        ]
        self.assertEqual(len(confirmation_calls), 1)
        self.assertEqual(len(confirmation_calls[0].kwargs["transient_context"]), 1)
        self.assertIsInstance(
            confirmation_calls[0].kwargs["transient_context"][0],
            terminal_module.Panel,
        )
        self.assertTrue(
            all(
                call.kwargs["transient_context"] == ()
                for call in picker.call_args_list
                if call not in confirmation_calls
            )
        )
        root_calls = [
            call for call in picker.call_args_list if call.args[1].name == "Settings"
        ]
        self.assertEqual(
            tuple(
                (len(call.args[2]), call.kwargs["visible_choice_limit"])
                for call in root_calls
            ),
            ((7, 7), (9, 9)),
        )
        dirty_prompt = next(
            value
            for value in _callbacks.prompts
            if value.startswith("Settings\n") and "Choices 3/9" in value
        )
        self.assertEqual(
            dirty_prompt,
            "Settings\n"
            "\n"
            "Choose a setting to edit, apply changes, or discard changes.\n"
            "\n"
            "Choices 3/9\n"
            "  1. Language                  — Current: KO\n"
            "  2. Persona                   — Current: default_kim_eun_hee\n"
            "> 3. Maximum debate rounds     — Pending: 0\n"
            "  4. Conversation storage      — Current: filesystem\n"
            "  5. Conversation RAG          — Current: Provider Disabled; Automatic Off\n"
            "  6. Message details           — Current: Off\n"
            "  7. Agents\n"
            "  8. Apply Changes (*Required)\n"
            "  9. Discard Changes\n"
            "     Pending changes will be discarded.\n"
            "  [save required] Choose Apply Changes to persist pending changes\n"
            "\n"
            "Enter a choice number, press Enter for the active choice, or enter /cancel.\n"
            "Choice > ",
        )
        self.assertTrue(
            all(
                call.kwargs["visible_choice_limit"] == 7
                for call in picker.call_args_list
                if call.args[1].name != "Settings"
            )
        )
        self.assertEqual(len(prompt.call_args_list), 2)
        self.assertTrue(
            all(call.kwargs.get("transient") is True for call in prompt.call_args_list)
        )
        self.assertTrue(
            all(
                call.kwargs["presentation"] is terminal_module.InputPresentation.FORM
                for call in prompt.call_args_list
            )
        )
        self.assertEqual(
            tuple(call.kwargs.get("initial_value") for call in prompt.call_args_list),
            ("1", "0"),
        )
        self.assertTrue(
            all(call.args[1].default is None for call in prompt.call_args_list)
        )

        ordinary_controller, _ordinary_backend, _ordinary_callbacks = (
            self.make_controller(workspace, "/help", "/quit", terminal_width=120)
        )
        with (
            _observed_picker_patch(original_picker) as ordinary_picker,
            _observed_prompt_patch(original_prompt) as ordinary_prompt,
        ):
            self.assertEqual(ordinary_controller.run_session_lobby(), 0)
        ordinary_picker.assert_not_called()
        ordinary_prompt.assert_not_called()

    def test_settings_confirmation_panel_is_changed_only_and_selectable(
        self,
    ) -> None:
        workspace = self.make_workspace()
        baseline = settings_module.load_settings_state(workspace)
        changed = settings_module.update_language(baseline, "EN")
        expected = self.expected_panel(changed)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "1",
            "2",
            "/cancel",
            "8",
            "1",
            "/quit",
            terminal_width=48,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with _observed_picker_patch(original_picker) as picker:
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertGreaterEqual(len(picker.call_args_list), 3)
        picker_call = picker.call_args_list[-1]
        self.assertIsNone(picker_call.kwargs.get("notice"))
        self.assertEqual(
            picker_call.args[2],
            (terminal_module.Choice("yes", "YES"), terminal_module.Choice("no", "NO")),
        )
        self.assertEqual(picker_call.kwargs["highlighted_id"], "yes")
        self.assertTrue(picker_call.kwargs["transient"])
        self.assertEqual(picker_call.kwargs["transient_context"], (expected,))
        confirmation_prompt = next(
            prompt for prompt in callbacks.prompts if "Apply changes?" in prompt
        )
        self.assertIn("YES", confirmation_prompt)
        self.assertIn("NO", confirmation_prompt)
        for omitted in ("--- Settings", "Current", "Pending", "(Changed)"):
            with self.subTest(omitted=omitted):
                self.assertNotIn(omitted, confirmation_prompt)
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("--- Settings [open]")
            ],
            [],
        )
        self.assertIn(
            "[savedandapplied]Settingssavedandappliedtothisrunningshellnow.",
            "".join("".join(callbacks.records).split()),
        )
        self.assertNotIn(
            "Restart is required for",
            "".join(callbacks.records),
        )
        self.assertEqual(
            settings_module.load_settings_state(
                workspace
            ).pending.system_defaults.language,
            "EN",
        )

    def test_settings_confirmation_routes_context_without_semantic_write_for_all_outcomes(
        self,
    ) -> None:
        workspace = self.make_workspace()
        state = settings_module.update_language(
            settings_module.load_settings_state(workspace),
            "EN",
        )
        expected_context = (self.expected_panel(state),)
        controller, _backend, _callbacks = self.make_controller(workspace)
        outcomes = (
            (
                "save-yes",
                "save",
                _control_commit_text("yes"),
                "save",
            ),
            (
                "save-no",
                "save",
                _control_commit_text("no"),
                "return",
            ),
            (
                "discard-yes",
                "cancel",
                _control_commit_text("yes"),
                "cancel",
            ),
            (
                "discard-no",
                "cancel",
                _control_commit_text("no"),
                "return",
            ),
            (
                "escape",
                "save",
                _cancel_control_outcome(),
                "return",
            ),
            (
                "interrupt",
                "cancel",
                terminal_module.ControlOutcome(
                    terminal_module.ControlOutcomeKind.INTERRUPT,
                ),
                "cancel",
            ),
            (
                "eof",
                "save",
                terminal_module.ControlOutcome(
                    terminal_module.ControlOutcomeKind.EOF,
                ),
                "cancel",
            ),
        )
        for label, action, outcome, expected in outcomes:
            with self.subTest(label=label):
                with (
                    _autospec_return_patch(InteractiveController, "_read_settings_picker", outcome) as picker,
                    _autospec_method_patch(type(controller._semantic_output), "write_component") as output,
                ):
                    self.assertEqual(
                        controller._settings_save_confirmation(
                            state,
                            action=action,
                        ),
                        expected,
                    )

                output.assert_not_called()
                picker.assert_called_once()
                called_controller, prompt, choices = picker.call_args.args
                self.assertIs(called_controller, controller)
                self.assertEqual(
                    prompt.name,
                    "Apply changes?" if action == "save" else "Discard changes?",
                )
                self.assertEqual(
                    choices,
                    (
                        terminal_module.Choice("yes", "YES"),
                        terminal_module.Choice("no", "NO"),
                    ),
                )
                self.assertEqual(
                    picker.call_args.kwargs["transient_context"],
                    expected_context,
                )
                self.assertEqual(
                    picker.call_args.kwargs["highlighted_id"],
                    "yes",
                )
                self.assertTrue(picker.call_args.kwargs["transient"])

    def test_message_details_only_save_is_confirmed_and_transaction_only(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, backend, callbacks = self.make_controller(
            workspace,
            auth_coordinator=mock.Mock(),
        )
        state = settings_module.update_show_message_details(
            settings_module.load_settings_state(workspace),
            True,
        )
        agent_path = workspace.path("config/agent_configs.json")
        route_path = workspace.path(
            "agent_states/orchestrator_state/provider_route_generations.json"
        )
        agent_before = agent_path.read_bytes()
        route_before = route_path.read_bytes() if route_path.exists() else None
        route_generations_before = dict(backend.provider_route_generations())
        profiles_before = tuple(
            backend.execution_profile(agent_id) for agent_id in ROSTER
        )
        thread_status_before = tuple(
            backend.execution_profile_thread_status(agent_id) for agent_id in ROSTER
        )
        commit_save = _control_commit_text("save")
        events: list[str] = []
        prepared_values = []
        rendered_components = []
        real_classify = InteractiveController._settings_change_is_message_details_only
        real_prepare = controller_core_module.prepare_settings_save
        real_commit = controller_core_module.commit_prepared_settings_save
        real_output = controller._semantic_output.write_component

        def confirm(_controller, candidate, *, action):
            self.assertIs(candidate, state)
            self.assertEqual(action, "save")
            events.append("confirmation")
            return "save"

        def classify(candidate):
            self.assertIs(candidate, state)
            events.append("final dirty classification")
            return real_classify(candidate)

        def prepare(saved_workspace, candidate, *, provider_overrides=None):
            events.append("prepare")
            prepared = real_prepare(
                saved_workspace,
                candidate,
                provider_overrides=provider_overrides,
            )
            prepared_values.append(prepared)
            return prepared

        def commit(saved_workspace, prepared):
            events.append("commit")
            self.assertIs(prepared, prepared_values[0])
            self.assertEqual(prepared.configuration, state.pending)
            self.assertEqual(prepared.switched_agents, ())
            self.assertFalse(controller._show_message_details)
            return real_commit(saved_workspace, prepared)

        def write_component(_output, component):
            events.append("completion")
            rendered_components.append(component)
            return real_output(component)

        runtime_prepare = mock.AsyncMock()
        runtime_commit = mock.AsyncMock()
        runtime_discard = mock.Mock()
        with (
            _loaded_settings_state_patch(state),
            _cancel_picker_patch(commit_save) as picker,
            _autospec_effect_patch(InteractiveController, "_settings_save_confirmation", confirm) as confirmation,
            _autospec_effect_patch(InteractiveController, "_settings_change_is_message_details_only",
                classify) as classification,
            _autospec_effect_patch(controller_core_module, "prepare_settings_save", prepare) as prepared_save,
            _autospec_effect_patch(controller_core_module, "commit_prepared_settings_save", commit) as committed_save,
            _settings_routes_authentication_patch() as authenticate,
            _fresh_literal_object_none(backend, 'execution_profiles') as execution_profiles,
            _fresh_literal_object_none(backend, 'stage_runtime_configuration') as stage_backend,
            _fresh_literal_object_none(backend, 'validate_runtime_configuration_candidate') as validate_backend,
            _fresh_literal_object_none(backend, 'discard_runtime_configuration') as discard_backend,
            _fresh_literal_object_none(backend, 'record_runtime_configuration_recovery_required') as recovery_backend,
            _object_new_patch(controller._runtime, "prepare_runtime_configuration", runtime_prepare),
            _object_new_patch(controller._runtime, "commit_runtime_configuration", runtime_commit),
            _object_new_patch(controller._runtime, "discard_runtime_configuration", runtime_discard),
            _storage_selection_patch_fixture() as select_storage,
            _object_wrap_patch(workspace, "write_text", workspace.write_text) as write,
            _autospec_effect_patch(type(controller._semantic_output), "write_component", write_component) as output,
        ):
            controller._run_settings()

        picker.assert_called_once()
        confirmation.assert_called_once()
        classification.assert_called_once_with(state)
        prepared_save.assert_called_once()
        committed_save.assert_called_once()
        output.assert_called_once()
        authenticate.assert_not_called()
        execution_profiles.assert_not_called()
        stage_backend.assert_not_called()
        validate_backend.assert_not_called()
        discard_backend.assert_not_called()
        recovery_backend.assert_not_called()
        runtime_prepare.assert_not_awaited()
        runtime_commit.assert_not_awaited()
        runtime_discard.assert_not_called()
        select_storage.assert_not_called()
        self.assertEqual(
            events,
            [
                "confirmation",
                "final dirty classification",
                "prepare",
                "commit",
                "completion",
            ],
        )
        self.assertEqual(
            [call.args[0] for call in write.call_args_list],
            ["config/system_defaults.json"],
        )
        self.assertEqual(agent_path.read_bytes(), agent_before)
        self.assertEqual(
            route_path.read_bytes() if route_path.exists() else None,
            route_before,
        )
        self.assertEqual(
            dict(backend.provider_route_generations()),
            route_generations_before,
        )
        self.assertEqual(
            tuple(backend.execution_profile(agent_id) for agent_id in ROSTER),
            profiles_before,
        )
        self.assertEqual(
            tuple(
                backend.execution_profile_thread_status(agent_id) for agent_id in ROSTER
            ),
            thread_status_before,
        )
        self.assertTrue(controller._show_message_details)
        self.assertTrue(
            settings_module.load_settings_state(
                workspace
            ).pending.system_defaults.show_message_details
        )
        self.assertEqual(
            rendered_components,
            [
                terminal_module.Panel(
                    "Settings",
                    (
                        terminal_module.Notice(
                            ("Settings saved and applied to this running shell now."),
                            label="saved and applied",
                            role=terminal_module.SemanticRole.SUCCESS,
                            state=terminal_module.ViewState.COMPLETED,
                        ),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                )
            ],
        )
        self.assertEqual(
            "".join(callbacks.records).count(
                "[saved and applied] Settings saved and applied to this "
                "running shell now.\n"
            ),
            1,
        )

    def test_message_details_only_save_rechecks_dirty_after_confirmation(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        state = settings_module.update_show_message_details(
            settings_module.load_settings_state(workspace),
            True,
        )
        commit_save = _control_commit_text("save")
        cancel = _cancel_control_outcome()

        def clear_candidate(_controller, candidate, *, action):
            self.assertEqual(action, "save")
            object.__setattr__(
                candidate,
                "pending",
                candidate.baseline.configuration,
            )
            return "save"

        with (
            _loaded_settings_state_patch(state),
            _observed_picker_patch((commit_save, cancel)) as picker,
            _autospec_effect_patch(InteractiveController, "_settings_save_confirmation", clear_candidate) as confirmation,
            _autospec_method_patch(InteractiveController, "_settings_change_is_message_details_only") as classification,
            _fresh_literal_object_none(controller_core_module, 'prepare_settings_save') as prepare,
            _prepared_settings_commit_patch() as commit,
            _settings_routes_authentication_patch() as authenticate,
        ):
            controller._run_settings()

        self.assertEqual(picker.call_count, 2)
        confirmation.assert_called_once()
        classification.assert_not_called()
        prepare.assert_not_called()
        commit.assert_not_called()
        authenticate.assert_not_called()
        self.assertFalse(controller._show_message_details)
        self.assertFalse(
            settings_module.load_settings_state(
                workspace
            ).pending.system_defaults.show_message_details
        )
        self.assertEqual(callbacks.records, [])

    def test_message_details_only_failures_preserve_activation_and_order(
        self,
    ) -> None:
        failures = (
            (
                "prepare_settings_save",
                settings_module.SettingsErrorKind.VALIDATION,
            ),
            (
                "prepare_settings_save",
                settings_module.SettingsErrorKind.CONFLICT,
            ),
            (
                "commit_prepared_settings_save",
                settings_module.SettingsErrorKind.WRITE,
            ),
            (
                "commit_prepared_settings_save",
                settings_module.SettingsErrorKind.ROLLBACK,
            ),
        )
        for operation, kind in failures:
            with self.subTest(operation=operation, kind=kind):
                workspace = self.make_workspace()
                controller, _backend, callbacks = self.make_controller(workspace)
                state = settings_module.update_show_message_details(
                    settings_module.load_settings_state(workspace),
                    True,
                )
                before = _tree_snapshot(workspace.root)
                if operation == "prepare_settings_save":
                    with (
                        _object_effect_patch(controller_core_module, operation,
                            settings_module.SettingsError(kind)) as failed,
                        _prepared_settings_commit_patch() as commit,
                    ):
                        outcome = controller._save_message_details_setting(state)
                    commit.assert_not_called()
                else:
                    with _object_effect_patch(controller_core_module, operation,
                             settings_module.SettingsError(kind)) as failed:
                        outcome = controller._save_message_details_setting(state)

                self.assertEqual(outcome, "retry")
                failed.assert_called_once()
                self.assertFalse(controller._show_message_details)
                self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertFalse(
                    settings_module.load_settings_state(
                        workspace
                    ).pending.system_defaults.show_message_details
                )
                self.assertEqual(
                    "".join(callbacks.records).count("[settings error]"),
                    1,
                )
                self.assertNotIn("saved and applied", "".join(callbacks.records))

        for tampering in ("configuration", "switched agents"):
            with self.subTest(tampering=tampering):
                workspace = self.make_workspace()
                controller, backend, callbacks = self.make_controller(workspace)
                state = settings_module.update_show_message_details(
                    settings_module.load_settings_state(workspace),
                    True,
                )
                before = _tree_snapshot(workspace.root)
                prepared = settings_module.prepare_settings_save(
                    workspace,
                    state,
                    provider_overrides=backend.backend_overrides,
                )
                tampered = (
                    replace(
                        prepared,
                        configuration=state.baseline.configuration,
                    )
                    if tampering == "configuration"
                    else replace(
                        prepared,
                        switched_agents=(PLOT_ARCHITECT,),
                    )
                )
                with (
                    _object_return_patch(controller_core_module, "prepare_settings_save", tampered) as prepare,
                    _prepared_settings_commit_patch() as commit,
                ):
                    with self.assertRaises(controller_core_module._ControllerAbort):
                        controller._save_message_details_setting(state)

                prepare.assert_called_once()
                commit.assert_not_called()
                self.assertFalse(controller._show_message_details)
                self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertEqual(callbacks.records, [])

        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        state = settings_module.update_show_message_details(
            settings_module.load_settings_state(workspace),
            True,
        )
        agent_path = workspace.path("config/agent_configs.json")
        route_path = workspace.path(
            "agent_states/orchestrator_state/provider_route_generations.json"
        )
        agent_before = agent_path.read_bytes()
        route_before = route_path.read_bytes() if route_path.exists() else None
        terminal_failure = OSError("terminal write failed")
        with (
            _object_wrap_patch(controller_core_module, "commit_prepared_settings_save",
                controller_core_module.commit_prepared_settings_save) as commit,
            _autospec_effect_patch(type(controller._semantic_output), "write_component", terminal_failure) as output,
        ):
            with self.assertRaises(OSError) as caught:
                controller._save_message_details_setting(state)

        self.assertIs(caught.exception, terminal_failure)
        commit.assert_called_once()
        output.assert_called_once()
        self.assertTrue(controller._show_message_details)
        self.assertTrue(
            settings_module.load_settings_state(
                workspace
            ).pending.system_defaults.show_message_details
        )
        self.assertEqual(agent_path.read_bytes(), agent_before)
        self.assertEqual(
            route_path.read_bytes() if route_path.exists() else None,
            route_before,
        )

    def test_settings_save_and_cancel_confirmation_controls_are_call_local(
        self,
    ) -> None:
        cases = (
            (
                "save-no-then-dirty-cancel",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "8",
                    "2",
                    "9",
                    "1",
                    "/quit",
                ),
                2,
                False,
                1,
            ),
            (
                "save-fallback-cancel-then-dirty-cancel",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "8",
                    "/cancel",
                    "9",
                    "1",
                    "/quit",
                ),
                2,
                False,
                1,
            ),
            (
                "clean-action-ordinal-is-not-selectable",
                ("/settings", "8", "/cancel", "/quit"),
                0,
                False,
                0,
            ),
            ("clean-root-escape", ("/settings", "/cancel", "/quit"), 0, False, 0),
            (
                "clean-root-interrupt",
                ("/settings", KeyboardInterrupt(), "/quit"),
                0,
                False,
                0,
            ),
            (
                "clean-root-eof",
                ("/settings", EOFError(), "/quit"),
                0,
                False,
                0,
            ),
            (
                "dirty-cancel-no-then-yes",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "9",
                    "2",
                    "9",
                    "1",
                    "/quit",
                ),
                2,
                True,
                1,
            ),
            (
                "dirty-root-escape-confirmation-escape-then-yes",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "/cancel",
                    "/cancel",
                    "9",
                    "1",
                    "/quit",
                ),
                2,
                True,
                1,
            ),
            (
                "dirty-cancel-interrupt",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "9",
                    KeyboardInterrupt(),
                    "/quit",
                ),
                1,
                False,
                1,
            ),
            (
                "dirty-root-escape-eof",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "/cancel",
                    EOFError(),
                    "/quit",
                ),
                1,
                False,
                1,
            ),
            (
                "dirty-root-interrupt",
                ("/settings", "1", "2", "/cancel", KeyboardInterrupt(), "/quit"),
                0,
                False,
                1,
            ),
            (
                "dirty-root-eof",
                ("/settings", "1", "2", "/cancel", EOFError(), "/quit"),
                0,
                False,
                1,
            ),
            (
                "save-interrupt",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "8",
                    KeyboardInterrupt(),
                    "/quit",
                ),
                1,
                False,
                1,
            ),
            (
                "save-eof",
                (
                    "/settings",
                    "1",
                    "2",
                    "/cancel",
                    "8",
                    EOFError(),
                    "/quit",
                ),
                1,
                False,
                1,
            ),
        )
        for label, reads, confirmation_count, returns_to_cancel, discard_count in cases:
            with self.subTest(label=label):
                workspace = self.make_workspace()
                before = _tree_snapshot(workspace.root)
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    *reads,
                    terminal_width=80,
                )
                original_picker = terminal_module.InteractivePickerInput.read_picker
                with _observed_picker_patch(original_picker) as picker:
                    self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(_tree_snapshot(workspace.root), before)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    rendered.count("[cancelled] Pending changes discarded.\n"),
                    discard_count,
                )
                self.assertNotIn("[saved] Settings saved.", rendered)
                confirmation_prompts = [
                    prompt
                    for prompt in callbacks.prompts
                    if "Apply changes?" in prompt or "Discard changes?" in prompt
                ]
                self.assertEqual(len(confirmation_prompts), confirmation_count)
                if discard_count:
                    dirty_settings_calls = [
                        call
                        for call in picker.call_args_list
                        if call.args[1].name == "Settings"
                        and any(
                            choice.identity == "cancel"
                            and choice.description
                            == "Pending changes will be discarded."
                            for choice in call.args[2]
                        )
                    ]
                    self.assertTrue(dirty_settings_calls)
                if returns_to_cancel:
                    settings_calls = [
                        call
                        for call in picker.call_args_list
                        if call.args[1].name == "Settings"
                    ]
                    self.assertEqual(
                        settings_calls[-1].kwargs["highlighted_id"],
                        "cancel",
                    )

    def test_internal_clean_save_route_is_silent_and_normalizes_highlight(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)
        commit_save = _control_commit_text("save")
        cancel = _cancel_control_outcome()

        with (
            _observed_picker_patch((commit_save, cancel)) as picker,
            _autospec_method_patch(InteractiveController, "_settings_save_confirmation") as confirmation,
            _settings_routes_authentication_patch() as authenticate,
            _storage_selection_patch_fixture() as save,
            _fresh_literal_object_none(InteractiveController, '_settings_panel') as panel,
            _autospec_method_patch(type(controller._semantic_output), "write_component") as output,
        ):
            controller._run_settings()

        confirmation.assert_not_called()
        authenticate.assert_not_called()
        save.assert_not_called()
        panel.assert_not_called()
        output.assert_not_called()
        self.assertEqual(callbacks.records, [])
        self.assertEqual(len(picker.call_args_list), 2)
        self.assertTrue(
            all(
                tuple(choice.identity for choice in call.args[2])
                == (
                    "language",
                    "persona",
                    "max_debate_rounds",
                    "conversation_storage",
                    "conversation_rag",
                    "show_message_details",
                    "providers",
                )
                for call in picker.call_args_list
            )
        )
        self.assertIsNone(picker.call_args_list[1].kwargs["highlighted_id"])

    def test_save_auth_exits_and_clean_rebase_never_persist_or_emit_saved(
        self,
    ) -> None:
        workspace = self.make_workspace()
        baseline = settings_module.load_settings_state(workspace)
        changed = settings_module.update_language(baseline, "EN")
        commit_save = _control_commit_text("save")
        interrupt = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.INTERRUPT
        )
        cancel = _cancel_control_outcome()
        cases = (
            ("auth-return", changed, "return", (commit_save, interrupt)),
            ("auth-cancel", changed, "cancel", (commit_save,)),
            ("clean-rebase", baseline, "ready", (commit_save, cancel)),
        )

        for label, authenticated_state, auth_outcome, picker_outcomes in cases:
            with self.subTest(label=label):
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    auth_coordinator=mock.Mock(),
                )
                with (
                    _loaded_settings_state_patch(changed),
                    _observed_picker_patch(picker_outcomes) as picker,
                    _autospec_return_patch(InteractiveController, "_settings_save_confirmation", "confirm") as confirmation,
                    _autospec_return_patch(InteractiveController, "_authenticate_settings_routes",
                        (authenticated_state, auth_outcome)) as authenticate,
                    _storage_selection_patch_fixture() as save,
                ):
                    controller._run_settings()

                confirmation.assert_called_once()
                authenticate.assert_called_once()
                save.assert_not_called()
                self.assertNotIn(
                    "[saved] Settings saved.",
                    "".join(callbacks.records),
                )
                if label == "clean-rebase":
                    self.assertEqual(len(picker.call_args_list), 2)
                    self.assertEqual(
                        tuple(
                            choice.identity
                            for choice in picker.call_args_list[1].args[2]
                        ),
                        (
                            "language",
                            "persona",
                            "max_debate_rounds",
                            "conversation_storage",
                            "conversation_rag",
                            "show_message_details",
                            "providers",
                        ),
                    )
                    self.assertIsNone(picker.call_args_list[1].kwargs["highlighted_id"])
                    self.assertNotIn(
                        "[cancelled] Pending changes discarded.",
                        "".join(callbacks.records),
                    )

    def test_settings_edit_save_isolated_discovered_candidate_and_literal_cancel(
        self,
    ) -> None:
        workspace = self.make_workspace()
        agent_configs = workspace.read_json("config/agent_configs.json")
        agent_configs[PLOT_ARCHITECT].update(
            {
                "antigravity_model": "inactive-agy-model",
                "antigravity_effort": "medium",
            }
        )
        workspace.write_json("config/agent_configs.json", agent_configs)
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog(
                "codex",
                ProviderModel(
                    "gpt-a",
                    "GPT Alpha",
                    None,
                    "low",
                    (ProviderEffort("low", "low"),),
                ),
                ProviderModel(
                    "gpt-b",
                    "GPT Beta",
                    None,
                    "future_2",
                    (
                        ProviderEffort("low", "low"),
                        ProviderEffort("future_2", "future_2"),
                    ),
                ),
            )
        )
        before = _tree_snapshot(workspace.root)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "1",
            "2",
            "/cancel",
            "3",
            "bad",
            "0",
            "\x1b",
            "7",
            "2",
            "2",
            "3",
            "/cancel",
            "/cancel",
            "/cancel",
            "7",
            "2",
            "3",
            "3",
            "/cancel",
            "/cancel",
            "/cancel",
            "7",
            "2",
            "2",
            "4",
            "/cancel",
            "\x1b",
            "/cancel",
            "/cancel",
            "/cancel",
            "8",
            "1",
            "/quit",
            provider_catalog=catalog,
            terminal_width=48,
        )
        real_commit = settings_module.commit_prepared_settings_save
        real_select = controller_core_module.select_conversation_storage

        def select_after_isolation(*args, **kwargs):
            self.assertEqual(_tree_snapshot(workspace.root), before)
            self.assertNotIn(
                "[saved] Settings saved.",
                "".join(callbacks.records),
            )
            return real_select(*args, **kwargs)

        def commit_after_selection(saved_workspace, prepared):
            self.assertEqual(
                workspace.path("config/system_defaults.json").read_bytes(),
                prepared.system_after,
            )
            self.assertEqual(
                workspace.path("config/agent_configs.json").read_bytes(),
                prepared.agent_after,
            )
            route_path = workspace.path(
                "agent_states/orchestrator_state/provider_route_generations.json"
            )
            observed_route = route_path.read_bytes() if route_path.exists() else None
            self.assertEqual(observed_route, prepared.route_before)
            return real_commit(saved_workspace, prepared)

        with (
            _autospec_effect_patch(controller_core_module, "commit_prepared_settings_save",
                commit_after_selection) as commit_save,
            _object_effect_patch(controller_core_module, "select_conversation_storage",
                select_after_isolation) as select,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        commit_save.assert_called_once()
        select.assert_called_once()
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        configured = settings_module.load_settings_state(workspace).pending
        self.assertEqual(configured.system_defaults.language, "EN")
        self.assertEqual(configured.system_defaults.max_debate_rounds, 0)
        self.assertIsNone(configured.system_defaults.codex_model)
        self.assertIsNone(configured.system_defaults.codex_reasoning_effort)
        self.assertEqual(
            configured.agent_configs[PLOT_ARCHITECT].codex_model,
            "/cancel",
        )
        self.assertEqual(
            configured.agent_configs[PLOT_ARCHITECT].codex_reasoning_effort,
            "future_2",
        )
        self.assertEqual(
            configured.agent_configs[PLOT_ARCHITECT].antigravity_model,
            "inactive-agy-model",
        )
        self.assertEqual(
            configured.agent_configs[PLOT_ARCHITECT].antigravity_effort,
            "medium",
        )
        self.assertEqual(
            configured.agent_configs[PLOT_ARCHITECT].backend,
            None,
        )
        rendered = "".join(callbacks.records)
        compact_rendered = "".join(rendered.split())
        self.assertEqual(
            compact_rendered.count(
                "[validation]MaximumdebateroundsmustbeanonnegativeASCIIinteger."
            ),
            1,
        )
        self.assertEqual(
            compact_rendered.count(
                "[savedandapplied]Settingssavedandappliedtothisrunningshellnow."
            ),
            1,
        )
        self.assertEqual(catalog.events, ["codex", "codex", "codex"])
        compact_prompts = "".join(
            "".join(prompt.split()) for prompt in callbacks.prompts
        )
        self.assertIn("pendingCodexprovider", compact_prompts)
        self.assertIn("effectivemodel", compact_prompts)
        self.assertGreaterEqual(
            sum("Choose a setting to edit" in prompt for prompt in callbacks.prompts),
            1,
        )
        self.assertNotIn("Inherit", rendered)
        self.assertNotIn("Settings cancelled", rendered)
        self.assertEqual(callbacks.reads, [])

    def test_antigravity_settings_edit_active_fields_and_preserve_codex(self) -> None:
        workspace = self.make_workspace()
        agent_configs = workspace.read_json("config/agent_configs.json")
        agent_configs[PLOT_ARCHITECT].update(
            {
                "codex_model": "inactive-codex-model",
                "codex_reasoning_effort": "xhigh",
            }
        )
        workspace.write_json("config/agent_configs.json", agent_configs)
        efforts = (
            ProviderEffort("low", "low"),
            ProviderEffort("medium", "medium"),
            ProviderEffort("high", "high"),
        )
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog(
                "antigravity",
                ProviderModel("agy-one", "AGY One", None, None, efforts),
                ProviderModel("agy-two", "AGY Two", None, None, efforts),
            )
        )
        controller, backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "7",
            "2",
            "1",
            "2",
            "/cancel",
            "/cancel",
            "/cancel",
            "7",
            "2",
            "2",
            "4",
            "-unsafe",
            "agy-model",
            "\x1b",
            "/cancel",
            "/cancel",
            "/cancel",
            "7",
            "2",
            "3",
            "4",
            "/cancel",
            "/cancel",
            "/cancel",
            "8",
            "1",
            "/quit",
            provider_catalog=catalog,
            terminal_width=160,
        )

        with (
            _availability_patch("story_writing_agents.backend.AntigravityAdapter.executable_available", True) as executable_available,
            _availability_patch(
                "story_writing_agents.backend.AntigravityAdapter.headless_capabilities_available",
                True,
            ) as headless_capabilities_available,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        executable_available.assert_called_once()
        headless_capabilities_available.assert_called_once()
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()

        agent = settings_module.load_settings_state(workspace).pending.agent_configs[
            PLOT_ARCHITECT
        ]
        self.assertEqual(
            (
                agent.backend,
                agent.antigravity_model,
                agent.antigravity_effort,
                agent.codex_model,
                agent.codex_reasoning_effort,
            ),
            (
                "antigravity",
                "agy-model",
                "high",
                "inactive-codex-model",
                "xhigh",
            ),
        )
        effort_prompt = next(
            prompt
            for prompt in callbacks.prompts
            if prompt.startswith(
                "Effort\nContext: Plot & Structure Architect · Antigravity\n"
            )
        )
        self.assertIn(". low", effort_prompt)
        self.assertIn(". medium", effort_prompt)
        self.assertIn(". high", effort_prompt)
        self.assertNotIn("[available]", effort_prompt)
        self.assertNotIn("[highlighted]", effort_prompt)
        self.assertNotIn("minimal", effort_prompt)
        self.assertNotIn("xhigh", effort_prompt)
        rendered = "".join(callbacks.records)
        self.assertIn(
            "[validation] Antigravity model must be safe nonempty text, "
            "or blank to inherit.\n",
            rendered,
        )
        self.assertIn("Antigravity model", rendered)
        self.assertIn("[saved and applied] Settings saved and applied", rendered)
        self.assertEqual(catalog.events, ["antigravity", "antigravity"])
        self.assertEqual(
            (
                backend.execution_profile(PLOT_ARCHITECT).provider,
                backend.execution_profile(PLOT_ARCHITECT).model,
                backend.execution_profile(PLOT_ARCHITECT).effort,
                backend.provider_route_generations()[PLOT_ARCHITECT],
            ),
            ("antigravity", "agy-model", "high", 1),
        )

    def test_hot_save_preflight_quiescence_restore_and_recovery_are_truthful(
        self,
    ) -> None:
        def setup_case():
            workspace = self.make_workspace()
            controller, backend, callbacks = self.make_controller(
                workspace,
                "/quit",
                terminal_width=160,
            )
            state = settings_module.update_show_message_details(
                settings_module.update_agent_codex_model(
                    settings_module.load_settings_state(workspace),
                    PLOT_ARCHITECT,
                    "hot-ui-model",
                ),
                True,
            )
            self.assertFalse(controller._show_message_details)
            return workspace, controller, backend, callbacks, state

        workspace, controller, backend, callbacks, state = setup_case()
        before = _tree_snapshot(workspace.root)
        before_profile = backend.execution_profile(PLOT_ARCHITECT)
        with (
            _object_effect_patch(backend, "stage_runtime_configuration",
                ConfigurationError("PRIVATE_PREFLIGHT_FAILURE")) as stage,
            _object_new_patch(controller._runtime, "prepare_runtime_configuration", mock.AsyncMock()) as prepare_runtime,
        ):
            self.assertEqual(controller._save_and_apply_settings(state), "retry")
        stage.assert_called_once()
        prepare_runtime.assert_not_awaited()
        self.assertFalse(controller._show_message_details)
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(backend.execution_profile(PLOT_ARCHITECT), before_profile)
        rendered = "".join(callbacks.records)
        self.assertIn("[not applied] Settings were not saved or applied.", rendered)
        self.assertNotIn("PRIVATE_PREFLIGHT_FAILURE", rendered)
        self.assertNotIn("saved and applied", rendered)

        workspace, controller, backend, callbacks, state = setup_case()
        before = _tree_snapshot(workspace.root)
        before_profile = backend.execution_profile(PLOT_ARCHITECT)
        with (
            _object_new_patch(controller._runtime, "prepare_runtime_configuration",
                mock.AsyncMock(
                    side_effect=BackendProtocolError("PRIVATE_QUIESCENCE_FAILURE")
                )) as prepare_runtime,
            _object_wrap_patch(backend, "discard_runtime_configuration",
                backend.discard_runtime_configuration) as discard_backend,
        ):
            self.assertEqual(controller._save_and_apply_settings(state), "retry")
        prepare_runtime.assert_awaited_once()
        discard_backend.assert_called_once()
        self.assertFalse(controller._show_message_details)
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(backend.execution_profile(PLOT_ARCHITECT), before_profile)
        rendered = "".join(callbacks.records)
        self.assertIn("[not applied] Settings were not saved or applied.", rendered)
        self.assertNotIn("PRIVATE_QUIESCENCE_FAILURE", rendered)
        self.assertNotIn("saved and applied", rendered)

        workspace, controller, backend, callbacks, state = setup_case()
        before = _tree_snapshot(workspace.root)
        before_profile = backend.execution_profile(PLOT_ARCHITECT)
        with (
            _object_new_patch(controller._runtime, "commit_runtime_configuration",
                mock.AsyncMock(
                    side_effect=BackendProtocolError("PRIVATE_POST_PERSIST_FAILURE")
                )) as commit_runtime,
            _object_wrap_patch(controller_core_module, "restore_prepared_settings_save",
                controller_core_module.restore_prepared_settings_save) as restore,
        ):
            self.assertEqual(controller._save_and_apply_settings(state), "retry")
        commit_runtime.assert_awaited_once()
        restore.assert_called_once()
        self.assertFalse(controller._show_message_details)
        after_files = {
            relative: value[0]
            for relative, value in _tree_snapshot(workspace.root).items()
        }
        self.assertEqual(
            after_files.pop("config/.conversation_storage.lock", None),
            b"",
        )
        self.assertEqual(
            after_files,
            {relative: value[0] for relative, value in before.items()},
        )
        self.assertEqual(backend.execution_profile(PLOT_ARCHITECT), before_profile)
        rendered = "".join(callbacks.records)
        self.assertIn("[restored; not applied] Runtime activation failed", rendered)
        self.assertNotIn("PRIVATE_POST_PERSIST_FAILURE", rendered)
        self.assertNotIn("saved and applied", rendered)

        workspace, controller, backend, callbacks, state = setup_case()
        before = _tree_snapshot(workspace.root)

        async def activate_then_fail(runtime_candidate, backend_candidate):
            backend.commit_runtime_configuration(backend_candidate)
            raise BackendProtocolError("PRIVATE_ACTIVE_RECOVERY_FAILURE")

        with (
            _object_new_patch(controller._runtime, "commit_runtime_configuration",
                mock.AsyncMock(side_effect=activate_then_fail)) as commit_runtime,
            _object_wrap_patch(backend, "record_runtime_configuration_recovery_required",
                backend.record_runtime_configuration_recovery_required) as record_recovery,
        ):
            self.assertEqual(
                controller._save_and_apply_settings(state),
                "recovery_required",
            )
        commit_runtime.assert_awaited_once()
        record_recovery.assert_called_once()
        self.assertFalse(controller._show_message_details)
        self.assertNotEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(
            backend.execution_profile(PLOT_ARCHITECT).model,
            "hot-ui-model",
        )
        rendered = "".join(callbacks.records)
        self.assertIn("[settings error] Settings were saved", rendered)
        self.assertIn("restart it to recover", rendered)
        self.assertNotIn("PRIVATE_ACTIVE_RECOVERY_FAILURE", rendered)
        self.assertNotIn("saved and applied", rendered)

    def test_nested_return_but_interrupt_and_eof_cancel_all(self) -> None:
        scenarios = (
            (
                (
                    "/settings",
                    "7",
                    "2",
                    "2",
                    "/cancel",
                    "/cancel",
                    "/cancel",
                    "/cancel",
                    "/quit",
                ),
            ),
            (("/settings", KeyboardInterrupt(), "/quit"),),
            (("/settings", EOFError(), "/quit"),),
        )
        for (reads,) in scenarios:
            with self.subTest(reads=reads):
                workspace = self.make_workspace()
                before = _tree_snapshot(workspace.root)
                catalog = ScriptedProviderCatalog(
                    ProviderCatalogDiscovery(
                        "codex",
                        ProviderCatalogState.UNAVAILABLE,
                    )
                )
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    *reads,
                    provider_catalog=catalog,
                    terminal_width=160,
                )
                self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(_tree_snapshot(workspace.root), before)
                rendered = "".join(callbacks.records)
                self.assertTrue(
                    any(
                        "Choose a setting to edit" in prompt
                        for prompt in callbacks.prompts
                    )
                )
                self.assertEqual(
                    rendered.count("[cancelled] Pending changes discarded.\n"),
                    0,
                )

    def test_storage_edit_is_desired_only_and_keeps_current_shell_bundle(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "4",
            "2",
            "2",
            "8",
            "1",
            "/quit",
            terminal_width=160,
        )
        bundle = controller._conversation_storage_services
        self.assertIsNotNone(bundle)
        self.assertIs(bundle.backend, ConversationStorageBackend.FILESYSTEM)
        with _autospec_effect_patch(ConversationStorageProvisioner, "install",
                 AssertionError("a real installer must not execute")) as install:
            self.assertEqual(controller.run_session_lobby(), 0)
        install.assert_not_called()
        self.assertEqual(
            settings_module.load_settings_state(
                workspace
            ).pending.system_defaults.conversation_storage_backend,
            "sqlite",
        )
        self.assertIs(controller._conversation_storage_services, bundle)
        self.assertIs(controller._runtime.conversation_storage_services, bundle)
        self.assertIs(bundle.backend, ConversationStorageBackend.FILESYSTEM)
        self.assertFalse(
            workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH).exists()
        )
        self.assertIn(
            "Restart is required for sqlite.",
            "".join(callbacks.records),
        )

    def test_persona_catalog_race_reports_conflict_and_preserves_config(self) -> None:
        workspace = self.make_workspace()
        system_before = workspace.path("config/system_defaults.json").read_bytes()
        agents_before = workspace.path("config/agent_configs.json").read_bytes()
        controller, _backend, callbacks = _message_visibility_controller_fixture(self, workspace)
        real_select = controller_core_module.select_conversation_storage

        def race_persona_catalog(*args, **kwargs):
            workspace.write_text(
                "config/persona_templates/alternative_templates/raced.md",
                "raced persona",
            )
            self.assertTrue(callable(kwargs.get("precommit_guard")))
            return real_select(*args, **kwargs)

        with _object_effect_patch(controller_core_module, "select_conversation_storage", race_persona_catalog) as select:
            self.assertEqual(controller.run_session_lobby(), 0)
        select.assert_called_once()
        self.assertEqual(
            workspace.path("config/system_defaults.json").read_bytes(),
            system_before,
        )
        self.assertEqual(
            workspace.path("config/agent_configs.json").read_bytes(),
            agents_before,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            "[conversation storage] Settings changed on disk; cancel and "
            "reopen /settings.\n",
            rendered,
        )
        self.assertNotIn("Settings saved", rendered)

    def test_each_typed_save_failure_retains_candidate_and_redacts_details(
        self,
    ) -> None:
        messages = {
            settings_module.SettingsErrorKind.VALIDATION: (
                "Pending settings are invalid; correct them before saving."
            ),
            settings_module.SettingsErrorKind.CONFLICT: (
                "Settings changed on disk; cancel and reopen /settings."
            ),
            settings_module.SettingsErrorKind.WRITE: (
                "Settings could not be saved; pending values were retained."
            ),
            settings_module.SettingsErrorKind.ROLLBACK: (
                "Settings rollback failed; inspect both configuration files."
            ),
        }
        for kind, message in messages.items():
            with self.subTest(kind=kind):
                workspace = self.make_workspace()
                before = _tree_snapshot(workspace.root)
                controller, _backend, callbacks = _message_visibility_controller_fixture(self, workspace)
                operation = (
                    "prepare_settings_save"
                    if kind
                    in {
                        settings_module.SettingsErrorKind.VALIDATION,
                        settings_module.SettingsErrorKind.CONFLICT,
                    }
                    else "commit_prepared_settings_save"
                )
                with _object_effect_patch(controller_core_module, operation,
                         settings_module.SettingsError(kind)) as save:
                    self.assertEqual(controller.run_session_lobby(), 0)
                save.assert_called_once()
                after = _tree_snapshot(workspace.root)
                storage_guard = after.pop(
                    "config/.conversation_storage.lock",
                    None,
                )
                if kind in {
                    settings_module.SettingsErrorKind.WRITE,
                    settings_module.SettingsErrorKind.ROLLBACK,
                }:
                    self.assertIsNotNone(storage_guard)
                    assert storage_guard is not None
                    self.assertEqual(storage_guard[0], b"")
                    for relative in (
                        "config/system_defaults.json",
                        "config/agent_configs.json",
                        (
                            "agent_states/orchestrator_state/"
                            "provider_route_generations.json"
                        ),
                    ):
                        if relative in before:
                            self.assertEqual(
                                after[relative][:2],
                                before[relative][:2],
                            )
                            after[relative] = before[relative]
                        else:
                            self.assertNotIn(relative, after)
                else:
                    self.assertIsNone(storage_guard)
                self.assertEqual(after, before)
                rendered = "".join(callbacks.records)
                if kind in {
                    settings_module.SettingsErrorKind.WRITE,
                    settings_module.SettingsErrorKind.ROLLBACK,
                }:
                    self.assertEqual(
                        rendered.count(
                            "[restored; not applied] Runtime activation failed"
                        ),
                        1,
                    )
                    self.assertNotIn(f"[settings error] {message}\n", rendered)
                else:
                    self.assertEqual(
                        rendered.count(f"[settings error] {message}\n"),
                        1,
                    )
                    self.assertNotIn("[restored; not applied]", rendered)
                self.assertNotIn("settings validation failed", rendered)
                self.assertEqual(
                    rendered.count("[cancelled] Pending changes discarded.\n"),
                    1,
                )

    def test_each_selection_save_failure_retains_candidate_and_redacts_details(
        self,
    ) -> None:
        messages = {
            ConversationStorageSelectionFailure.CONFIGURATION_INVALID: (
                "Pending settings are invalid; correct them before saving."
            ),
            ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT: (
                "Settings changed on disk; cancel and reopen /settings."
            ),
            ConversationStorageSelectionFailure.CONFIGURATION_WRITE_FAILED: (
                "Settings could not be saved; prior configuration was preserved."
            ),
        }
        for failure, message in messages.items():
            with self.subTest(failure=failure):
                workspace = self.make_workspace()
                before = _tree_snapshot(workspace.root)
                controller, _backend, callbacks = _message_visibility_controller_fixture(self, workspace)
                with _object_return_patch(controller_core_module, "select_conversation_storage",
                         ConversationStorageSelectionReport.failed(
                        failure,
                        desired_backend="filesystem",
                    )) as select:
                    self.assertEqual(controller.run_session_lobby(), 0)
                select.assert_called_once()
                self.assertEqual(_tree_snapshot(workspace.root), before)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    rendered.count(f"[conversation storage] {message}\n"),
                    1,
                )
                self.assertNotIn("settings validation failed", rendered)
                self.assertNotIn("[saved] Settings saved.", rendered)
                self.assertEqual(
                    rendered.count("[cancelled] Pending changes discarded.\n"),
                    1,
                )



























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
        self.seed_session(workspace, "session", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "session")
        selected_catalog = catalog or MemoryConversationCatalog(entries)
        if selected_id is not None:
            save_default_conversation(
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
        workspace, _backend, catalog, controller, callbacks = self.prepared_controller(
            "/conversation new",
            "Opening scene",
            "/conversation new Opening scene",
            "/quit",
        )
        with (
            _object_wrap_patch(subprocess, "Popen", subprocess.Popen) as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertTrue(popen.called)
        self.assertTrue(
            all(call.args[0][0] == "git" for call in popen.call_args_list)
        )
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
            load_default_conversation(workspace, "session"),
            catalog.entries[1].conversation_id,
        )
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith(("[conversation]\n", "[conversation] [empty]"))
            ],
            [
                _conversation_scope_record("session", None, None),
                _conversation_scope_record(
                    "session",
                    catalog.entries[0].conversation_id[:12],
                    "Opening scene",
                ),
                _conversation_scope_record(
                    "session",
                    catalog.entries[1].conversation_id,
                    "Opening scene",
                ),
            ],
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("Created and focused"), 2)
        self.assertIn(
            "  Subject: Opening scene\n",
            rendered,
        )
        self.assertEqual(list(workspace.root.rglob("transcript.jsonl")), [])
        self.assertEqual(list(workspace.root.rglob("*.result.json")), [])
        self.assertEqual(list(workspace.root.rglob("*.state.json")), [])
        self.assertEqual(
            [path.name for path in workspace.path("manuscript").iterdir()],
            [".gitkeep"],
        )





























class ControllerContentionTests(ControllerTestCase):
    """Verify two real controllers cannot own one session concurrently."""

    def test_second_controller_enters_lobby_while_first_holds_access(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "shared", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "shared")
        context_before = workspace.path(_CONTEXT_PATH).read_bytes()
        nested: dict[str, object] = {}

        def run_contender(_prompt: str) -> str:
            """Run a second controller while the first holds shared access."""

            access_path = workspace.path(_access_relative("shared"))
            nested["access_before"] = access_path.read_bytes()
            inner, _backend, inner_callbacks = self.make_controller(
                workspace,
                "/quit",
                terminal_width=200,
            )
            nested["exit_code"] = inner.run_session_lobby()
            nested["records"] = "".join(inner_callbacks.records)
            nested["access_after"] = access_path.read_bytes()
            nested["context_after"] = workspace.path(_CONTEXT_PATH).read_bytes()
            return "/quit"

        outer, _backend, outer_callbacks = self.make_controller(
            workspace,
            run_contender,
        )
        self.assertEqual(outer.run_session_lobby(), 0)
        self.assertEqual(nested["exit_code"], 0)
        self.assertEqual(nested["access_before"], nested["access_after"])
        self.assertEqual(nested["context_after"], context_before)
        self.assertIn(
            _error_record(
                InteractiveErrorCode.SESSION_ACCESS_BLOCKED,
                width=200,
            ),
            nested["records"],
        )
        self.assertIn("Session: lobby\n", nested["records"])
        self.assertIn("Session: shared (opened)\n", "".join(outer_callbacks.records))
        self.assertFalse(workspace.path(_access_relative("shared")).exists())


class InteractiveLeadAdapterTests(unittest.TestCase):
    """Verify the terminal adapter emits only exact runtime decisions."""

    class _Progress:
        def __init__(self) -> None:
            self.begins: list[tuple[object, ...]] = []
            self.observed: list[object] = []
        def begin(self, requests: tuple[object, ...] = ()) -> None: self.begins.append(requests)
        def observe(self, event: object) -> None: self.observed.append(event)
        def finish(self) -> str: return "00:00:00"
        async def run(self, operation: object) -> object:
            return await operation  # type: ignore[misc]

    class _Picker:
        def __init__(self, *outcomes: ControlOutcome) -> None:
            self.outcomes = list(outcomes)
            self.calls = []

        def read_picker(self, *_args: object, **_kwargs: object) -> ControlOutcome:
            self.calls.append(_args)
            return self.outcomes.pop(0)

    class _Prompt:
        def __init__(self, *outcomes: ControlOutcome) -> None: self.outcomes = list(outcomes)

        def read_prompt(self, *_args: object, **_kwargs: object) -> ControlOutcome: return self.outcomes.pop(0)

    class _Output:
        def __init__(self) -> None:
            self.components: list[object] = []

        def write_component(self, component: object) -> None:
            self.components.append(component)

    @staticmethod
    def _commit(text: str) -> ControlOutcome: return ControlOutcome(ControlOutcomeKind.COMMIT, text=text)

    @staticmethod
    def _raw(value_type: type[object], **values: object) -> object:
        value = object.__new__(value_type)
        for name, item in values.items():
            object.__setattr__(value, name, item)
        return value

    @staticmethod
    def _terminal_turn(
        state: CollaborationStepState,
        *,
        evidence: object | None = None,
    ) -> LeadTurnResult:
        return LeadTurnResult(
            "session", _UNIQUE_C, "turn", STEP_ID,
            LeadOutcomeKind.RECOMMENDATION,
            "The lead completed the requested decision.",
            None, state, None,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
            evidence=evidence,  # type: ignore[arg-type]
        )

    def test_literal_lead_turn_and_consultation_decisions_are_exact(self) -> None:
        session = RuntimeSession.__new__(RuntimeSession)
        proposal = make_proposal()
        proposed = LeadTurnResult(
            "session",
            _UNIQUE_C,
            "turn",
            STEP_ID,
            LeadOutcomeKind.PROPOSAL,
            "Specialist consultation is proposed.",
            proposal,
            CollaborationStepState.AWAITING_CONFIRMATION,
            "confirmation",
            CollaborationNextAction.SUBMIT_CONFIRMATION,
            evidence=proposal.basis.evidence,
        )
        calls: list[tuple[str, object]] = []
        current: list[LeadTurnResult] = [proposed]

        forwarded = []
        async def lead_turn(
            _session: RuntimeSession,
            conversation_id: str,
            text: str,
            **_kwargs: object,
        ) -> LeadTurnResult:
            forwarded.append(_kwargs)
            calls.append(("lead", (conversation_id, text)))
            return current[0]

        async def decide(
            _session: RuntimeSession,
            decision: object,
            **_kwargs: object,
        ) -> LeadDecisionResult:
            calls.append(("decision", decision))
            kind = decision.kind  # type: ignore[attr-defined]
            state = {
                CollaborationConfirmationDecision.CONFIRM: CollaborationStepState.COMPLETED,
                CollaborationConfirmationDecision.REJECT: CollaborationStepState.REJECTED,
                CollaborationConfirmationDecision.REVISE: CollaborationStepState.SUPERSEDED,
            }[kind]
            return LeadDecisionResult(
                "session",
                _UNIQUE_C,
                STEP_ID,
                kind,
                proposal,
                self._terminal_turn(
                    state,
                    evidence=(
                        proposal.basis.evidence
                        if state is CollaborationStepState.SUPERSEDED
                        else None
                    ),
                ),
            )

        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        with (
            mock.patch.object(RuntimeSession, "lead_turn", lead_turn),
            mock.patch.object(RuntimeSession, "decide_lead_step", decide),
        ):
            for kind, revision in (
                (CollaborationConfirmationDecision.CONFIRM, None),
                (CollaborationConfirmationDecision.REJECT, None),
                (CollaborationConfirmationDecision.REVISE, "Narrow the review."),
            ):
                with self.subTest(kind=kind):
                    calls.clear()
                    prompts = (
                        (self._commit(revision),) if revision is not None else ()
                    )
                    adapter = lead_policy_module.InteractiveLeadAdapter(
                        session,
                        runner,
                        self._Progress(),  # type: ignore[arg-type]
                        self._Picker(self._commit(kind.value)),  # type: ignore[arg-type]
                        self._Prompt(*prompts),  # type: ignore[arg-type]
                        self._Output(),  # type: ignore[arg-type]
                    )
                    self.assertFalse(adapter.submit(_UNIQUE_C, " literal input "))
                    self.assertEqual(calls[0], ("lead", (_UNIQUE_C, " literal input ")))
                    decision = next(value for label, value in calls if label == "decision")
                    self.assertIs(decision.kind, kind)  # type: ignore[attr-defined]
                    self.assertEqual(
                        decision.revision_request,  # type: ignore[attr-defined]
                        revision,
                    )

            calls.clear()
            adapter = lead_policy_module.InteractiveLeadAdapter(
                session,
                runner,
                self._Progress(),  # type: ignore[arg-type]
                self._Picker(ControlOutcome(ControlOutcomeKind.CANCEL)),  # type: ignore[arg-type]
                self._Prompt(),  # type: ignore[arg-type]
                self._Output(),  # type: ignore[arg-type]
            )
            self.assertFalse(adapter.submit(_UNIQUE_C, "cancel proposal"))
            self.assertFalse(any(label == "decision" for label, _value in calls))

            calls.clear()
            current[:] = [self._terminal_turn(CollaborationStepState.COMPLETED)]
            adapter = lead_policy_module.InteractiveLeadAdapter(
                session,
                runner,
                self._Progress(),  # type: ignore[arg-type]
                self._Picker(),  # type: ignore[arg-type]
                self._Prompt(),  # type: ignore[arg-type]
                self._Output(),  # type: ignore[arg-type]
            )
            self.assertFalse(adapter.submit(_UNIQUE_C, "replace the request"))
            self.assertEqual(
                calls,
                [("lead", (_UNIQUE_C, "replace the request"))],
            )
            text = "Provisional 한글\r\nwhole text. "
            idea = exploration.ExplorationIdea("ui-current-idea", exploration.ExplorationIdeaKind.ALTERNATIVE, text, hashlib.sha256(text.encode()).hexdigest())
            report = exploration.LeadExplorationReport("ui-request", "ui-call", exploration.ExplorationReportStatus.IDEAS, "Provisional possibilities.", (idea,))
            from tests import collaboration_contract_fixture as events_fixture
            drafts = (
                events_fixture.make_draft(
                    "ui-user", collaboration.CollaborationEventKind.MESSAGE,
                    events_fixture.USER, events_fixture.LEAD,
                    collaboration.CollaborationMessagePayload("Explore directions."),
                ),
                events_fixture.make_draft(
                    report.request_event_id,
                    collaboration.CollaborationEventKind.LEAD_EXPLORATION_REQUEST,
                    events_fixture.USER, events_fixture.LEAD,
                    collaboration.CollaborationLeadExplorationRequestPayload("ui-user"),
                ),
                *events_fixture.provider_call_drafts(
                    invocation_id="ui-invocation", call_id=report.provider_call_id,
                    role=events_fixture.LEAD.agent_id, recipient=events_fixture.LEAD,
                    invocation_kind=collaboration.CollaborationInvocationKind.LEAD_EXPLORATION,
                ),
                events_fixture.make_draft(
                    "ui-result", collaboration.CollaborationEventKind.LEAD_EXPLORATION_RESULT,
                    events_fixture.LEAD, events_fixture.USER,
                    collaboration.CollaborationLeadExplorationResultPayload(report),
                ),
            )
            drafts = tuple(
                replace(item, session_id="session", conversation_id=_UNIQUE_C,
                        turn_id="turn", step_id=STEP_ID)
                for item in drafts
            )
            batch = events_fixture.make_batch((), "ui-exploration", *drafts)
            history = events_fixture.store_batch((), batch)
            current[:] = [replace(self._terminal_turn(CollaborationStepState.COMPLETED), message=report.message, kind=LeadOutcomeKind.EXPLORATION, state=None, interaction_mode=exploration.LeadInteractionMode.EXPLORATION, exploration=report, evidence=None, next_action=CollaborationNextAction.CONTINUE_WITH_LEAD)]
            output = self._Output()
            adapter = lead_policy_module.InteractiveLeadAdapter(session, runner, self._Progress(), self._Picker(self._commit(idea.idea_id)), self._Prompt(self._commit("core_documents/global_outline.md")), output, read_events=lambda conversation: history if conversation == _UNIQUE_C else ())
            self.assertFalse(adapter.submit(_UNIQUE_C, "Explore directions.", interaction_mode=exploration.LeadInteractionMode.EXPLORATION))
            self.assertEqual((set(forwarded[-1]), forwarded[-1]["interaction_mode"]), ({"action_progress_sink", "interaction_mode"}, exploration.LeadInteractionMode.EXPLORATION))
            self.assertIn("Exploration (provisional)", "\n".join(render_plain(component, width=120).text for component in output.components))
            current[:] = [self._terminal_turn(CollaborationStepState.COMPLETED)]
            self.assertFalse(adapter.adopt(_UNIQUE_C))
            self.assertEqual((set(forwarded[-1]), forwarded[-1]["adoption"]), ({"action_progress_sink", "adoption"}, exploration.ExplorationAdoptionSelection(_UNIQUE_C, "ui-result", idea.idea_id, idea.material_digest, ("core_documents/global_outline.md",))))
            self.assertEqual(tuple((component.items[0].label, component.items[0].value, component.items[1].content, component.items[2].value) for component in output.components if isinstance(component, terminal_module.Panel) and component.title == "Selected whole idea"), (("Source conversation", _UNIQUE_C, text, "core_documents/global_outline.md"),))


            from tests.test_lead_orchestration import LeadOrchestrationTestCase, _confirmed_premise, _premise_choice, _SubmittedFailure
            from story_writing_agents import lead_premise_decisions as premise
            fixture = LeadOrchestrationTestCase("runTest")
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            conversation = fixture.conversation()
            _, first_report = runner.run(_confirmed_premise(fixture, conversation))
            fixture.backend.enqueue(_SubmittedFailure(RuntimeError("known selected front failure")))
            runner.run(fixture.runtime.lead_turn(fixture.session_id, conversation, "Keep this whole choice.", premise_selection=_premise_choice(first_report, ("core_documents/global_outline.md",))))
            _, latest = runner.run(_confirmed_premise(fixture, conversation))
            history = fixture.history(conversation)
            retained = collaboration.project_collaboration_history(history).premise_selection
            self.assertNotIn(retained.option_id, tuple(item.option_id for item in latest.premise_report.options))
            selected = latest.premise_report.options[1]
            output = self._Output()
            current[:] = [self._terminal_turn(CollaborationStepState.COMPLETED)]
            adapter = lead_policy_module.InteractiveLeadAdapter(session, runner, self._Progress(), self._Picker(self._commit("choose"), self._commit(selected.option_id), self._commit("user_choice")), self._Prompt(self._commit("core_documents/global_outline.md"), self._commit("core_documents/character_sheets.md"), self._commit("")), output, read_events=lambda current_conversation: history)
            self.assertFalse(adapter.premise(conversation))
            selection = forwarded[-1]["premise_selection"]
            self.assertEqual((selection.report_event_id, selection.option_id, selection.material_digest), (latest.premise_report_ref.report_event_id, selected.option_id, selected.material_digest))
            self.assertIs(selection.choice_policy, premise.PremiseChoicePolicy.USER_CHOICE)
            self.assertEqual(selection.target_paths, ("core_documents/global_outline.md", "core_documents/character_sheets.md"))
            self.assertEqual(tuple(item.identity for item in adapter._picker.calls[0][1]), ("choose", "reject", "defer"))
            self.assertEqual(tuple(item.identity for item in adapter._picker.calls[2][1]), ("user_choice",))
            self.assertTrue(any(component.title == "Retained USER choice (reference)" for component in output.components if isinstance(component, terminal_module.Panel)))
            before_cancel = tuple(forwarded)
            adapter._picker = self._Picker(ControlOutcome(ControlOutcomeKind.CANCEL))
            self.assertFalse(adapter.premise(conversation))
            self.assertEqual(tuple(forwarded), before_cancel)
            conversation = fixture.conversation()
            _, unranked = runner.run(_confirmed_premise(fixture, conversation, recommendation=None))
            history = fixture.history(conversation)
            self.assertIsNone(unranked.premise_report.recommended_option_id)
            option = unranked.premise_report.options[0]
            adapter._picker = self._Picker(self._commit("choose"), self._commit(option.option_id), self._commit("user_choice"))
            adapter._prompt = self._Prompt(self._commit("core_documents/global_outline.md"), self._commit(""))
            self.assertFalse(adapter.premise(conversation))
            self.assertEqual(tuple(item.identity for item in adapter._picker.calls[2][1]), ("user_choice",))
            self.assertEqual(forwarded[-1]["premise_selection"].report_event_id, unranked.premise_report_ref.report_event_id)

        from tests.test_lead_orchestration import _task_fixture, _discussion_reply
        case = _task_fixture(self)
        task_conversation = case.conversation("Shell task choices")
        runner.run(case.proposal(task_conversation))
        case.backend.enqueue(_discussion_reply("Whole shell material e\u0301\r\n"))
        runner.run(case.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
            case.session_id, task_conversation, task.LeadTaskAction.DISCUSS, text="Discuss role responsibilities.",
        )))
        projection = case.runtime.inspect_lead_collaboration(case.session_id, task_conversation)
        selected_task, material = projection.available_tasks[0], projection.selectable_materials[-1]
        decisions = []
        async def decide_task(decision, *, action_progress_sink=None):
            decisions.append(decision)
            return collaboration.LeadTaskDecisionResult(
                decision.session_id, decision.conversation_id, decision.action,
                decision.task_ref, "shell_transition", None,
            )
        with (mock.patch.object(RuntimeSession, "inspect_lead_collaboration", return_value=projection) as inspection,
              mock.patch.object(RuntimeSession, "decide_lead_task", side_effect=decide_task)):
            for action in task.LeadTaskAction:
                picks = [self._commit(action.value), self._commit("task_0")]
                uses_material = action in (task.LeadTaskAction.DISCUSS, task.LeadTaskAction.RESUME,
                                          task.LeadTaskAction.NEW_TASK)
                if uses_material:
                    picks.append(self._commit("material_0"))
                output = self._Output()
                adapter = lead_policy_module.InteractiveLeadAdapter(
                    case.runtime.session(case.session_id), runner, self._Progress(), self._Picker(*picks),
                    self._Prompt(self._commit("Explicit question or new writing intent.")), output,
                )
                self.assertFalse(adapter.task(task_conversation, material_choices=(material,)))
                task_ref = selected_task.ref
                identity = f"Task ID {task_ref.task_id} | Revision {task_ref.task_revision}"
                shown = output.components[-1].items
                self.assertEqual(shown[-1].label, identity)
                self.assertIn(f"Lifecycle status: {selected_task.status} —", shown[-1].value)
                self.assertEqual(tuple((item.label, item.value) for item in shown[:3]), (
                    ("Workflow step", projection.current_step_id or "none"),
                    ("Workflow step state", projection.current_state.value
                     if projection.current_state is not None else "no active step"),
                    ("Workflow next action", projection.next_safe_action.value)))
                self.assertEqual(adapter._picker.calls[1][1][-1].description,
                                 identity + " — " + selected_task.status + " — " + selected_task.goal)
                self.assertEqual(inspection.call_count, 2 * len(decisions))
                decision = decisions[-1]
                self.assertEqual((decision.action, decision.task_ref), (action, selected_task.ref))
                self.assertEqual(decision.material_refs, (material.ref,) if uses_material else ())
                self.assertEqual(decision.text, "Explicit question or new writing intent." if uses_material else None)
                self.assertEqual(tuple(item.label for item in adapter._picker.calls[0][1]),
                                 ("Discuss", "Hold", "Resume", "Discard", "New Task"))
                if uses_material:
                    self.assertEqual(adapter._picker.calls[2][0].name, "Whole input material")
                    self.assertEqual(adapter._picker.calls[2][1][1].description, material.text)
            adapter._picker = self._Picker(self._commit("discuss"), self._commit("independent"), self._commit("none"))
            adapter._prompt = self._Prompt(self._commit("Independent task-free question."))
            self.assertFalse(adapter.task(task_conversation))
            self.assertIsNone(decisions[-1].task_ref)
            self.assertEqual(decisions[-1].material_refs, ())
            self.assertEqual(decisions[-1].text, "Independent task-free question.")
            before = tuple(decisions)
            for outcome in (ControlOutcomeKind.CANCEL, ControlOutcomeKind.EOF):
                adapter._picker = self._Picker(self._commit("discuss"), ControlOutcome(outcome))
                self.assertEqual(adapter.task(task_conversation), outcome is ControlOutcomeKind.EOF)
                self.assertEqual(tuple(decisions), before)

    def test_ordinary_lead_progress_sink_finishes_before_lead_and_elapsed_output(self) -> None:
        session = RuntimeSession.__new__(RuntimeSession); runner = asyncio.Runner(); self.addCleanup(runner.close)
        progress, output, sinks = self._Progress(), self._Output(), []
        async def lead_turn(_session, cid, text, *, action_progress_sink):
            sinks.append(action_progress_sink); return self._terminal_turn(CollaborationStepState.COMPLETED)
        with mock.patch.object(RuntimeSession, "lead_turn", lead_turn):
            adapter = lead_policy_module.InteractiveLeadAdapter(session, runner, progress,
                self._Picker(), self._Prompt(), output)
            self.assertFalse(adapter.submit(_UNIQUE_C, "ordinary"))
        self.assertEqual(sinks, [progress.observe]); self.assertEqual(progress.begins, [()])
        self.assertIsInstance(output.components[-1], terminal_module.Notice)
        self.assertEqual(output.components[-1].text, "Elapsed: 00:00:00")

    def test_provider_invocation_failure_writes_error_notice_without_crashing(self) -> None:
        import io
        from story_writing_agents.interactive_terminal._input_lifecycle import inspect_live_decoder_spec
        session = RuntimeSession.__new__(RuntimeSession); runner = asyncio.Runner(); self.addCleanup(runner.close)
        texts = ("  first\nsecond  ", "next reply", "test prompt")
        for mode in ("callback", "fallback", "live"):
            stream = io.StringIO()
            terminal = (terminal_module.InteractiveTerminal(lambda _: "", stream.write) if mode == "callback"
                        else terminal_module.build_stream_terminal(lambda _: "", io.StringIO(), stream))
            driver = None if mode == "callback" else terminal._write_text.__self__
            if mode == "fallback": driver._input_latch.latch_fallback()
            if mode == "live": driver._input_latch.latch_live(inspect_live_decoder_spec(io.TextIOWrapper(io.BytesIO())))
            progress = self._Progress()
            async def run(operation):
                if driver is not None: driver._draw_progress_frame(lambda _: "working")
                try: return await operation
                finally:
                    if driver is not None: driver._clear_progress_frame()
            progress.run = run
            async def lead_turn(_session, cid, text, *, action_progress_sink):
                self.assertEqual(cid, _UNIQUE_C); self.assertIn(text, texts)
                rendered = stream.getvalue()
                self.assertEqual(rendered.count("User"), texts.index(text) + 1)
                self.assertIn(text.strip().splitlines()[0], rendered)
                if text == texts[-1]: raise BackendProtocolError("Antigravity CLI failed (status 1): 503 Overloaded")
                return self._terminal_turn(CollaborationStepState.COMPLETED)
            with mock.patch.object(RuntimeSession, "lead_turn", lead_turn):
                adapter = lead_policy_module.InteractiveLeadAdapter(session, runner, progress, self._Picker(), self._Prompt(), terminal_module.InteractiveSemanticOutput(terminal))
                for text in texts:
                    start = len(stream.getvalue())
                    self.assertFalse(adapter.submit(_UNIQUE_C, text))
                    rendered = stream.getvalue()[start:]
                    self.assertEqual(rendered.count("User"), 1); self.assertLess(rendered.index("User"), rendered.index("Lead"))
                    for line in text.splitlines(): self.assertIn(line.strip(), rendered)
                    if driver is not None:
                        self.assertLess(rendered.index("User"), rendered.index("working")); self.assertEqual(driver._progress_rows, 0)
            self.assertEqual(stream.getvalue().count("User"), 3); self.assertIn("503 Overloaded", rendered); self.assertIn("provider failure", rendered); self.assertIn("Elapsed: 00:00:00", rendered)

    def test_provider_invocation_failure_surfaces_transient_stream_error_in_final_output(self) -> None:
        session = RuntimeSession.__new__(RuntimeSession)
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        progress, output = self._Progress(), self._Output()
        progress.last_error_message = "Error: No capacity available for model gpt-oss-120b-medium on the server"

        async def failing_turn(_session, _cid, _text, *, action_progress_sink=None):
            raise BackendProtocolError("Antigravity CLI exited with status 1")

        with mock.patch.object(RuntimeSession, "lead_turn", failing_turn):
            adapter = lead_policy_module.InteractiveLeadAdapter(
                session, runner, progress,
                self._Picker(), self._Prompt(), output,
            )
            result = adapter.submit(_UNIQUE_C, "test prompt")

        self.assertFalse(result)
        self.assertEqual(len(output.components), 4)
        panel = output.components[1]
        self.assertIsInstance(panel, terminal_module.Panel)
        self.assertEqual(panel.state, terminal_module.ViewState.ERROR)
        self.assertIn("No capacity available for model gpt-oss-120b-medium on the server", panel.items[1].content)
        notice = output.components[2]
        self.assertIn("No capacity available for model gpt-oss-120b-medium on the server", notice.text)

    def test_confirmed_execution_writes_specialists_then_lead_then_elapsed(self) -> None:
        runner = asyncio.Runner(); self.addCleanup(runner.close); output = self._Output()
        adapter = lead_policy_module.InteractiveLeadAdapter(RuntimeSession.__new__(RuntimeSession),
            runner, self._Progress(), self._Picker(), self._Prompt(), output)
        specialists = (SimpleNamespace(name="first"), SimpleNamespace(name="second"))
        adapter._pending_specialist_panels = specialists; adapter._pending_elapsed = "00:00:07"
        lead = SimpleNamespace(name="lead")
        with mock.patch.object(lead_policy_module, "project_lead_turn_result", return_value=lead):
            self.assertFalse(adapter._present_and_decide(self._terminal_turn(CollaborationStepState.COMPLETED)))
        self.assertEqual(output.components[:3], [*specialists, lead])
        self.assertEqual(output.components[3].text, "Elapsed: 00:00:07")

    def test_recommendation_controls_route_exact_decisions_and_cancel_without_mutation(self) -> None:
        from story_writing_agents import conversations as c
        from tests.recommendation_fixture import RecommendationCase, decision as exact_decision

        case = RecommendationCase()
        case.setUp()
        for cleanup, args, kwargs in case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        case._cleanups.clear()
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        conversation_id, turn = runner.run(case.recommend())
        before = case.history(conversation_id)
        session = RuntimeSession.__new__(RuntimeSession)
        completed = replace(turn, kind=c.LeadOutcomeKind.ANSWER, recommendation_request=None,
                            next_action=c.CollaborationNextAction.CONTINUE_WITH_LEAD)
        calls = []
        routes = []
        picker_calls = []
        async def lead_turn(_session, cid, text, **_kwargs):
            calls.append(("lead", (cid, text)))
            return turn
        async def decide(_session, selected, **_kwargs):
            calls.append(("decision", selected))
            return completed
        original = lead_policy_module.InteractiveLeadAdapter._run_operation
        def routed(adapter, operation):
            routes.append(operation.cr_code.co_name)
            return original(adapter, operation)
        class Picker(self._Picker):
            def read_picker(inner, prompt, choices, **kwargs):
                picker_calls.append((prompt, choices, kwargs))
                return super().read_picker(prompt, choices, **kwargs)
        with (mock.patch.object(RuntimeSession, "lead_turn", lead_turn),
              mock.patch.object(RuntimeSession, "decide_lead_recommendation", decide),
              mock.patch.object(RuntimeSession, "decide_lead_step", side_effect=AssertionError("wrong decision API")),
              mock.patch.object(lead_policy_module.InteractiveLeadAdapter, "_run_operation", routed)):
            for kind in c.CollaborationConfirmationDecision:
                with self.subTest(kind=kind):
                    calls.clear(); routes.clear(); picker_calls.clear()
                    revision = "  Narrow only the preparation.  " if kind is c.CollaborationConfirmationDecision.REVISE else None
                    adapter = lead_policy_module.InteractiveLeadAdapter(
                        session, runner, self._Progress(), Picker(self._commit(kind.value)),
                        self._Prompt(*(() if revision is None else (self._commit(revision),))),
                        self._Output(), recover_authentication=lambda _provider: self.fail("unexpected native handoff"))
                    self.assertFalse(adapter.submit(conversation_id, " literal input "))
                    self.assertEqual(calls, [("lead", (conversation_id, " literal input ")),
                        ("decision", exact_decision(turn, kind, revision_request=revision))])
                    self.assertEqual(routes, ["lead_turn", "decide"])
                    prompt, choices, options = picker_calls[0]
                    self.assertEqual(prompt.name, "Lead recommendation decision")
                    self.assertEqual(tuple(item.identity for item in choices), ("confirm", "reject", "revise"))
                    self.assertIn("preparation only", choices[0].description)
                    self.assertIn("new Confirm", choices[2].description)
                    self.assertEqual(options["highlighted_id"], "revise")
                    self.assertEqual(case.history(conversation_id), before)
            for outcome in (ControlOutcomeKind.CANCEL, ControlOutcomeKind.INTERRUPT, ControlOutcomeKind.EOF):
                with self.subTest(outcome=outcome):
                    calls.clear(); routes.clear()
                    adapter = lead_policy_module.InteractiveLeadAdapter(
                        session, runner, self._Progress(), Picker(ControlOutcome(outcome)),
                        self._Prompt(), self._Output())
                    self.assertEqual(adapter.submit(conversation_id, "still pending"), outcome is ControlOutcomeKind.EOF)
                    self.assertEqual(calls, [("lead", (conversation_id, "still pending"))])
                    self.assertEqual(routes, ["lead_turn"])
                    self.assertEqual(case.history(conversation_id), before)
        case.assert_no_downstream(conversation_id)

    def test_recommendation_revision_cancel_and_eof_preserve_pending_request(self) -> None:
        from story_writing_agents import conversations as c
        from tests.recommendation_fixture import RecommendationCase

        case = RecommendationCase()
        case.setUp()
        for cleanup, args, kwargs in case._cleanups:
            self.addCleanup(cleanup, *args, **kwargs)
        case._cleanups.clear()
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        conversation_id, turn = runner.run(case.recommend())
        before, calls = case.history(conversation_id), len(case.backend.calls)
        session = RuntimeSession.__new__(RuntimeSession)
        for outcome in (ControlOutcome(ControlOutcomeKind.CANCEL),
                        ControlOutcome(ControlOutcomeKind.INTERRUPT),
                        ControlOutcome(ControlOutcomeKind.EOF), self._commit(""), self._commit("  ")):
            with self.subTest(outcome=outcome), mock.patch.object(
                    RuntimeSession, "decide_lead_recommendation", side_effect=AssertionError("unconfirmed revision")):
                output = self._Output()
                adapter = lead_policy_module.InteractiveLeadAdapter(
                    session, runner, self._Progress(), self._Picker(self._commit("revise")),
                    self._Prompt(outcome), output)
                self.assertEqual(adapter._present_and_decide(turn), outcome.kind is ControlOutcomeKind.EOF)
                self.assertEqual(case.history(conversation_id), before)
                self.assertEqual(len(case.backend.calls), calls)
                projection = case.projection(conversation_id)
                self.assertIs(projection.current_recommendation_state, c.LeadRecommendationState.PENDING)
                self.assertEqual(projection.current_recommendation_request, turn.recommendation_request)
                self.assertFalse(case.payload_events(conversation_id, c.LeadRecommendationDecisionPayload))
                if outcome.kind is not ControlOutcomeKind.EOF:
                    self.assertTrue(any(isinstance(item, terminal_module.Notice) for item in output.components))

    def test_failure_interruption_and_recovery_turns_never_replay_authority(
        self,
    ) -> None:
        session = RuntimeSession.__new__(RuntimeSession)
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        calls: list[tuple[str, str]] = []
        current: list[LeadTurnResult] = []

        async def lead_turn(
            _session: RuntimeSession,
            conversation_id: str,
            text: str,
            **_kwargs: object,
        ) -> LeadTurnResult:
            calls.append((conversation_id, text))
            return current[0]

        cases = (
            (
                "provider-failure",
                None,
                CollaborationNextAction.RETRY_LEAD_TURN,
            ),
            (
                "interrupted",
                CollaborationStepState.INTERRUPTED,
                CollaborationNextAction.CONTINUE_WITH_LEAD,
            ),
            (
                "outcome-unknown",
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            ),
            (
                "recovery",
                CollaborationStepState.RECOVERY_REQUIRED,
                CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
            ),
        )
        with mock.patch.object(RuntimeSession, "lead_turn", lead_turn):
            for label, state, next_action in cases:
                with self.subTest(label=label):
                    calls.clear()
                    current[:] = [
                        LeadTurnResult(
                            "session",
                            _UNIQUE_C,
                            f"turn-{label}",
                            f"step-{label}",
                            LeadOutcomeKind.RECOMMENDATION,
                            f"Visible {label} result.",
                            None,
                            state,
                            None,
                            next_action,
                        )
                    ]
                    output = self._Output()
                    adapter = lead_policy_module.InteractiveLeadAdapter(
                        session,
                        runner,
                        self._Progress(),  # type: ignore[arg-type]
                        self._Picker(),  # type: ignore[arg-type]
                        self._Prompt(),  # type: ignore[arg-type]
                        output,  # type: ignore[arg-type]
                    )

                    self.assertFalse(adapter.submit(_UNIQUE_C, label))

                    self.assertEqual(calls, [(_UNIQUE_C, label)])
                    self.assertEqual(len(output.components), 3)

    def test_retry_lead_turn_keeps_fixed_diagnostic_copy_and_renders_error_role(self) -> None:
        session = RuntimeSession.__new__(RuntimeSession)
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        turn = LeadTurnResult(
            "session",
            _UNIQUE_C,
            "turn-err",
            "step-err",
            LeadOutcomeKind.RECOMMENDATION,
            "The Lead provider attempt failed without a usable result.",
            None,
            None,
            None,
            CollaborationNextAction.RETRY_LEAD_TURN,
        )

        async def lead_turn(*_args: object, **_kwargs: object) -> LeadTurnResult:
            return turn

        progress = self._Progress()
        progress.last_error_message = "Antigravity CLI exited with status 1: No capacity available"
        output = self._Output()
        with mock.patch.object(RuntimeSession, "lead_turn", lead_turn):
            adapter = lead_policy_module.InteractiveLeadAdapter(
                session, runner, progress, self._Picker(), self._Prompt(), output,
            )
            self.assertFalse(adapter.submit(_UNIQUE_C, "test prompt"))
            panel = output.components[1]
            self.assertIsInstance(panel, terminal_module.Panel)
            self.assertIs(panel.state, terminal_module.ViewState.ERROR)
            message_item = next(item for item in panel.items if isinstance(item, terminal_module.Message))
            self.assertIs(message_item.role, terminal_module.SemanticRole.ERROR)
            self.assertIn("Inspect /history and /exchanges before continuing.", message_item.content)
            self.assertNotIn("No capacity available", message_item.content)

        from tests.test_lead_orchestration import _task_fixture, _discussion_reply, _submitted_calls
        corrections = (_discussion_reply("Corrected public text."),
                       _discussion_reply("Original public text."), '{"analysis":{"message":"PRIVATE"}}')
        for correction in corrections:
            case = _task_fixture(self)
            conversation = case.conversation("Per-call literal public visibility")
            invalid = json.loads(_discussion_reply("Original public text."))
            invalid["agent_action"]["task_result"]["used_evidence_ids"] = "wrong array type"
            case.backend.enqueue(json.dumps(invalid))
            case.backend.enqueue(correction)
            result = runner.run(case.runtime.decide_lead_task(task.LeadTaskDecision.for_conversation(
                case.session_id, conversation, task.LeadTaskAction.DISCUSS, text="Discuss; do not draft.",
            )))
            output = self._Output()
            adapter = lead_policy_module.InteractiveLeadAdapter(
                case.runtime.session(case.session_id), runner, self._Progress(),
                self._Picker(), self._Prompt(), output,
                read_events=case.history,
            )
            adapter._present_and_decide(result.turn)
            rendered = "\n".join(render_plain(component, width=400).text for component in output.components)
            self.assertIn("Original public text.", rendered)
            self.assertIn("rejected", rendered)
            self.assertIn("Call validation", rendered)
            self.assertNotIn("PRIVATE", rendered)
            self.assertEqual(rendered.count("Public response disposition"), len(result.turn.public_responses))
            if '"response_text"' in correction:
                corrected = json.loads(correction)["agent_action"]["task_result"]["response_text"]
                self.assertEqual(rendered.count(corrected), 2 if corrected == "Original public text." else 1)
                self.assertIn("accepted", rendered)
                self.assertIn("Repairs call", rendered)
            else:
                self.assertIn("Public response unavailable", rendered)
                self.assertIn(_submitted_calls(case.history(conversation))[-1].call_id, rendered)
                notice = f"for rejected call {case.backend.calls[-1].call_id}:"
                self.assertIn(notice + " no bounded literal final response_text was recoverable.", rendered)

    def test_pending_commit_redisclosure_reuses_exact_picker_without_apply(self):
        digest = "sha256:" + "a" * 64
        request = SimpleNamespace(confirmation_request_id="pending-commit", application_result_event_id="applied-event", candidate=SimpleNamespace(candidate_id="b" * 64), request_digest=digest)
        turn = self._terminal_turn(CollaborationStepState.COMPLETED)
        object.__setattr__(turn, "next_action", CollaborationNextAction.SUBMIT_COMMIT_DECISION)
        object.__setattr__(turn, "story_commit_request", request)
        session = RuntimeSession.__new__(RuntimeSession)
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        calls = []
        async def lead_turn(*args, **_kwargs):
            return turn
        async def commit_decision(_session, decision):
            calls.append(decision)
            return self._raw(StoryCommitDecisionResult)
        with mock.patch.object(RuntimeSession, "lead_turn", lead_turn), mock.patch.object(RuntimeSession, "decide_artifact_application", side_effect=AssertionError("Apply must not be re-entered")), mock.patch.object(RuntimeSession, "decide_story_commit", commit_decision), mock.patch.object(lead_policy_module, "project_lead_turn_result", return_value=SimpleNamespace()), mock.patch.object(lead_policy_module, "project_story_commit_decision_result", return_value=SimpleNamespace()):
            for kind in (StoryCommitDecision.COMMIT, StoryCommitDecision.REJECT):
                lead_policy_module.InteractiveLeadAdapter(session, runner, self._Progress(), self._Picker(self._commit(kind.value)), self._Prompt(), self._Output()).submit(_UNIQUE_C, "Continue.")
                sent = calls[-1]
                self.assertIs(sent.kind, kind)
                self.assertEqual(sent.confirmation_request_id, request.confirmation_request_id)
                self.assertEqual(sent.application_result_event_id, request.application_result_event_id)
                self.assertEqual(sent.request_digest, digest)
            before = list(calls)
            lead_policy_module.InteractiveLeadAdapter(session, runner, self._Progress(), self._Picker(ControlOutcome(ControlOutcomeKind.CANCEL)), self._Prompt(), self._Output()).submit(_UNIQUE_C, "Continue.")
            self.assertEqual(calls, before)


    def test_artifact_and_commit_controls_are_distinct_and_correlated(self) -> None:
        digest = "sha256:" + "a" * 64
        candidate_id = "b" * 64
        reviewed = SimpleNamespace(
            proposal_id="proposal",
            proposal_revision=1,
            proposal_digest=digest,
        )
        application = SimpleNamespace(
            candidate_id="candidate",
            candidate_revision=1,
            candidate_digest=digest,
            application_id="application",
        )
        request = SimpleNamespace(
            confirmation_request_id="application-confirmation",
            reviewed_proposal=reviewed,
            application_request=application,
            application_request_digest=digest,
        )
        turn = self._raw(
            LeadTurnResult,
            session_id="session",
            conversation_id=_UNIQUE_C,
            turn_id="turn",
            step_id=STEP_ID,
            kind=LeadOutcomeKind.RECOMMENDATION,
            message="Review the artifact decision.",
            proposal=None,
            state=CollaborationStepState.COMPLETED,
            confirmation_request_id=None,
            next_action=CollaborationNextAction.SUBMIT_APPLICATION_DECISION,
            evidence=None,
            artifact_proposal=None,
            artifact_application_request=request,
            story_commit_request=None,
        )
        commit_request = SimpleNamespace(
            confirmation_request_id="commit-confirmation",
            application_result_event_id="application-event",
            candidate=SimpleNamespace(candidate_id=candidate_id),
            request_digest=digest,
        )
        session = RuntimeSession.__new__(RuntimeSession)
        runner = asyncio.Runner()
        self.addCleanup(runner.close)
        calls: list[tuple[str, object]] = []

        async def lead_turn(*_args: object, **_kwargs: object) -> LeadTurnResult:
            return turn  # type: ignore[return-value]

        async def application_decision(
            _session: RuntimeSession,
            decision: object,
        ) -> ArtifactApplicationDecisionResult:
            calls.append(("application", decision))
            commit_tail = (
                commit_request
                if decision.kind is ArtifactApplicationDecisionKind.APPLY  # type: ignore[attr-defined]
                else None
            )
            return self._raw(
                ArtifactApplicationDecisionResult,
                commit_request=commit_tail,
            )  # type: ignore[return-value]

        async def commit_decision(
            _session: RuntimeSession,
            decision: object,
        ) -> StoryCommitDecisionResult:
            calls.append(("commit", decision))
            return self._raw(StoryCommitDecisionResult)  # type: ignore[return-value]

        with (
            mock.patch.object(RuntimeSession, "lead_turn", lead_turn),
            mock.patch.object(RuntimeSession, "decide_artifact_application", application_decision),
            mock.patch.object(RuntimeSession, "decide_story_commit", commit_decision),
            _object_return_patch(lead_policy_module, "project_lead_turn_result", SimpleNamespace()),
            _object_return_patch(lead_policy_module, "project_artifact_application_decision_result",
                SimpleNamespace()),
            _object_return_patch(lead_policy_module, "project_story_commit_decision_result",
                SimpleNamespace()),
        ):
            for kind, revision in (
                (ArtifactApplicationDecisionKind.REJECT, None),
                (ArtifactApplicationDecisionKind.REVISE, "Revise the artifact."),
            ):
                with self.subTest(application=kind):
                    calls.clear()
                    prompts = (self._commit(revision),) if revision else ()
                    lead_policy_module.InteractiveLeadAdapter(
                        session,
                        runner,
                        self._Progress(),  # type: ignore[arg-type]
                        self._Picker(self._commit(kind.value)),  # type: ignore[arg-type]
                        self._Prompt(*prompts),  # type: ignore[arg-type]
                        self._Output(),  # type: ignore[arg-type]
                    ).submit(_UNIQUE_C, "artifact decision")
                    sent = calls[0][1]
                    self.assertIs(sent.kind, kind)  # type: ignore[attr-defined]
                    self.assertEqual(sent.revision_request, revision)  # type: ignore[attr-defined]
                    self.assertEqual(len(calls), 1)

            calls.clear()
            lead_policy_module.InteractiveLeadAdapter(
                session,
                runner,
                self._Progress(),  # type: ignore[arg-type]
                self._Picker(ControlOutcome(ControlOutcomeKind.CANCEL)),  # type: ignore[arg-type]
                self._Prompt(),  # type: ignore[arg-type]
                self._Output(),  # type: ignore[arg-type]
            ).submit(_UNIQUE_C, "cancel artifact")
            self.assertEqual(calls, [])

            for kind, revision in (
                (StoryCommitDecision.COMMIT, None),
                (StoryCommitDecision.REJECT, None),
                (StoryCommitDecision.REVISE, "Revise the commit."),
            ):
                with self.subTest(commit=kind):
                    calls.clear()
                    prompts = (self._commit(revision),) if revision else ()
                    lead_policy_module.InteractiveLeadAdapter(
                        session,
                        runner,
                        self._Progress(),  # type: ignore[arg-type]
                        self._Picker(
                            self._commit(ArtifactApplicationDecisionKind.APPLY.value),
                            self._commit(kind.value),
                        ),  # type: ignore[arg-type]
                        self._Prompt(*prompts),  # type: ignore[arg-type]
                        self._Output(),  # type: ignore[arg-type]
                    ).submit(_UNIQUE_C, "commit decision")
                    application_sent, commit_sent = (item[1] for item in calls)
                    self.assertIs(
                        application_sent.kind,  # type: ignore[attr-defined]
                        ArtifactApplicationDecisionKind.APPLY,
                    )
                    self.assertIs(commit_sent.kind, kind)  # type: ignore[attr-defined]
                    self.assertEqual(
                        commit_sent.revision_request,  # type: ignore[attr-defined]
                        revision,
                    )
                    self.assertEqual(
                        commit_sent.confirmation_request_id,  # type: ignore[attr-defined]
                        "commit-confirmation",
                    )
                    self.assertEqual(
                        commit_sent.application_result_event_id,  # type: ignore[attr-defined]
                        "application-event",
                    )
                    self.assertEqual(commit_sent.candidate_id, candidate_id)  # type: ignore[attr-defined]
                    self.assertRegex(
                        commit_sent.confirmation_id,  # type: ignore[attr-defined]
                        r"^c_[0-9a-f]{32}$",
                    )

            calls.clear()
            lead_policy_module.InteractiveLeadAdapter(
                session,
                runner,
                self._Progress(),  # type: ignore[arg-type]
                self._Picker(
                    self._commit(ArtifactApplicationDecisionKind.APPLY.value),
                    ControlOutcome(ControlOutcomeKind.CANCEL),
                ),  # type: ignore[arg-type]
                self._Prompt(),  # type: ignore[arg-type]
                self._Output(),  # type: ignore[arg-type]
            ).submit(_UNIQUE_C, "cancel commit")
            self.assertEqual(tuple(label for label, _value in calls), ("application",))

    def test_run_operation_returns_task_result_when_task_completes_after_auth_ready(self) -> None:
        """Verify _run_operation reads authoritative task result if task completes during progress cleanup."""
        import asyncio
        from story_writing_agents.interactive_controller._lead_policy import InteractiveLeadAdapter

        expected_result = object()

        async def fake_op():
            return expected_result

        runner = asyncio.Runner()
        self.addCleanup(runner.close)

        class FakeProgress:
            def run(self, coro, **kwargs):
                async def wrapper():
                    await coro
                    # Simulate progress returning None (e.g. from wait_for_operation_or_auth when auth was ready)
                    return None
                return wrapper()

        session = RuntimeSession.__new__(RuntimeSession)
        adapter = InteractiveLeadAdapter(
            session,
            runner,
            FakeProgress(),  # type: ignore[arg-type]
            mock.MagicMock(),
            mock.MagicMock(),
            mock.MagicMock(),
            recover_authentication=lambda _provider: True,
        )

        res = adapter._run_operation(fake_op())
        self.assertIs(res, expected_result)

    def test_unexpected_exception_in_submit_propagates_to_fatal_boundary(self) -> None:
        """Verify unexpected/fatal errors in submit() propagate rather than being swallowed."""
        from story_writing_agents.interactive_controller._lead_policy import InteractiveLeadAdapter

        session = mock.MagicMock(spec=RuntimeSession)
        session.lead_turn = mock.Mock()
        runner = asyncio.Runner()
        self.addCleanup(runner.close)

        adapter = InteractiveLeadAdapter(
            session,
            runner,
            mock.MagicMock(),
            mock.MagicMock(),
            mock.MagicMock(),
            mock.MagicMock(),
        )

        with mock.patch.object(InteractiveLeadAdapter, "_run_turn", side_effect=RuntimeError("terminal restoration crashed")):
            with self.assertRaises(RuntimeError) as ctx:
                adapter.submit("conv_1", "test request")
            self.assertIn("terminal restoration crashed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
