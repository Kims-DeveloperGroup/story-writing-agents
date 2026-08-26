"""Session-lobby controller contracts, transitions, and cleanup tests."""

from __future__ import annotations

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
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import story_writing_agents
from story_writing_agents.agents import (
    AGENT_SPECS,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
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
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversation_storage.provisioning import (
    ConversationStorageProvisioner,
)
from story_writing_agents.conversation_storage.selection import (
    ConversationStorageSelectionFailure,
    ConversationStorageSelectionReport,
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
from story_writing_agents.interactive_controller import (
    _runtime_policy as runtime_policy_module,
)
from story_writing_agents.interactive_controller import (
    _session_policy as session_policy_module,
)
from story_writing_agents import interactive_settings as settings_module
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
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
from story_writing_agents.interactive_terminal._reference_activation import (
    ReferenceInputFailure,
    ReferenceInputFailureKind,
    ReferenceInputScope,
)
from story_writing_agents.models import (
    DebateMessage,
    DebateProgressEvent,
    DebateProgressPhase,
    InputKind,
)
from story_writing_agents.session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
from story_writing_agents.workspace import (
    Workspace,
)
from story_writing_agents.writing_artifacts import MAX_VIEWER_UTF8_BYTES

from tests.helpers import initialized_workspace


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


def _confirmation_prompt(message: str, *, default: bool) -> str:
    """Return one exact callback-profile semantic confirmation prompt."""

    suffix = "[Y/n]" if default else "[y/N]"
    default_text = "yes" if default else "no"
    return (
        f"Question: {message}\n"
        "Choices: y/yes or n/no\n"
        f"Default: {default_text}\n"
        f"confirm {suffix}> "
    )


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
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
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
        if path.is_file()
        and "debug_logs" not in path.relative_to(root).parts
    }


def _manifest_relative(session_id: str) -> str:
    """Return the backend-owned manifest path for one safe test session."""

    return f"{_SESSION_DIRECTORY}/{session_id}.json"


def _access_relative(session_id: str) -> str:
    """Return the backend-owned access path for one safe test session."""

    return f"{_SESSION_DIRECTORY}/{session_id}.access.json"


def _default_relative(session_id: str) -> str:
    """Return one per-session conversation-selection path for assertions."""

    return f"{_CONVERSATION_DEFAULT_DIRECTORY}/{session_id}.json"


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
    """Return one exact framed conversation-scope callback record."""

    fields = [
        terminal_module.Field("Session", session_id),
        terminal_module.Field("Conversation", conversation_id or "none"),
    ]
    if subject is not None:
        fields.append(terminal_module.Field("Subject", subject))
    fields.append(terminal_module.Field("Agent", "lead"))
    component = terminal_module.Panel("Conversation scope", tuple(fields))
    return f"{render_plain(component, width=width).text}\n"


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


def _stored_transcript_event(
    conversation_id: str,
    sequence: int,
    *,
    session_id: str = "session",
    content: str | None = None,
    kind: ConversationEventKind = ConversationEventKind.USER_MESSAGE,
    sender: str = "user",
    recipient: str = "lead_orchestrator",
    turn_id: str | None = None,
) -> ConversationEvent:
    """Build one valid stored event for controller page-validation tests."""

    return ConversationEvent(
        schema_version=CONVERSATION_EVENT_SCHEMA_VERSION,
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"turn_{sequence}" if turn_id is None else turn_id,
        sequence=sequence,
        timestamp=_CREATED,
        sender=sender,
        recipient=recipient,
        kind=kind,
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
        durable_state: SessionDurableState,
    ) -> None:
        """Persist one ready or paused session without retaining access."""

        backend = AgentBackend(workspace, executable=sys.executable)
        try:
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
            controller_keywords["conversation_catalog"] = conversation_catalog
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
                "conversation_catalog",
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
        catalog_parameter = constructor.parameters["conversation_catalog"]
        self.assertIs(
            catalog_parameter.kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        self.assertIsNone(catalog_parameter.default)
        services_parameter = constructor.parameters[
            "conversation_storage_services"
        ]
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
                "conversation_catalog": ConversationCatalog | None,
                "conversation_storage_services": ConversationStorageServices | None,
                "auth_coordinator": InteractiveAuthCoordinator | None,
                "provider_catalog": ProviderCatalogService | None,
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

        with mock.patch.object(
            backend,
            "close",
            wraps=backend.close,
        ) as close:
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
        self.assertIsNone(controller._conversation_memory_service)

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
        failed_log.trace.side_effect = KeyboardInterrupt(
            "diagnostic trace failure"
        )
        controller._debug_log = failed_log

        with mock.patch.object(
            backend,
            "close",
            wraps=backend.close,
        ) as close:
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
        self.assertIsNone(controller._conversation_memory_service)

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

    def test_controller_and_composer_have_no_generic_status_sites(
        self,
    ) -> None:
        source_root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
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
            Path(__file__).resolve().parents[1]
            / "src"
            / "story_writing_agents"
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
                "Pure conversation fact interpretation for the interactive "
                "controller."
            ),
            "interactive_controller._runtime_policy": (
                "Pure runtime fact interpretation for the interactive controller."
            ),
        }
        expected_dependencies = {
            "interactive_controller": {"interactive_controller._core"},
            "interactive_controller._core": {
                "agents",
                "backend",
                "cli_context",
                "conversation_retrieval",
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
                "interactive_controller._runtime_policy",
                "interactive_controller._session_policy",
                "interactive_terminal",
                "models",
                "orchestrator",
                "provider_auth",
                "provider_catalog",
                "run_inputs",
                "session_lifecycle",
                "workspace",
                "writing_artifacts",
            },
            "interactive_controller._session_policy": {
                "errors",
                "interactive_terminal",
                "session_lifecycle",
            },
            "interactive_controller._conversation_policy": {
                "conversation_search",
                "conversations",
                "errors",
                "interactive_terminal",
                "workspace",
            },
            "interactive_controller._runtime_policy": {
                "conversation_retrieval",
                "conversation_search",
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
                self.assertEqual(ast.get_docstring(tree), expected_docstrings[module_name])
                self.assertEqual(dependencies, expected_dependencies[module_name])

        expected_policy_exports = {
            session_policy_module: [
                "RECOVERABLE_SESSION_ERROR_CODES",
                "classify_session_failure",
                "next_session_action",
                "require_correlated_state",
                "require_lifecycle_state",
                "require_ready_access_state",
                "require_released_state",
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
                "validate_transcript_page",
                "visible_conversation_handle",
            ],
            runtime_policy_module: [
                "DebateContextInspectionFacts",
                "DebateInspectionFacts",
                "classify_advisory_turn_failure",
                "classify_consensus_execution_failure",
                "classify_inspection_failure",
                "classify_progress_callback_failure",
                "interactive_code_for_debate_failure",
                "validate_consensus_result",
                "validate_conversation_retrieval_progress",
                "validate_conversation_retrieval_result",
                "validate_debate_context_inspection",
                "validate_debate_inspection",
                "validate_debate_progress",
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
            "interactive_controller._session_policy": set(),
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
                    [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
                )
                self.assertFalse(
                    [node for node in ast.walk(tree) if isinstance(node, (ast.Global, ast.Nonlocal))]
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
            for node in callable_nodes:
                with self.subTest(module=module_name, callable=node.name):
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

        module_names = {path: import_name(path) for path in module_paths}
        modules = set(module_names.values())
        graph: dict[str, set[str]] = {name: set() for name in modules}
        for path, module_name in module_names.items():
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(parsed):
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
        self.assertEqual(len(modules), 69)
        self.assertEqual(sum(len(dependencies) for dependencies in graph.values()), 271)
        self.assertTrue(
            {
                "story_writing_agents.conversation_storage.archive",
                "story_writing_agents.conversation_storage.filesystem_archive",
                "story_writing_agents.conversation_storage.migration",
                "story_writing_agents.conversation_storage.sqlite_database",
                "story_writing_agents.conversation_storage.sqlite",
                "story_writing_agents.conversation_storage.sqlite_search_index",
                "story_writing_agents.conversation_literal_search",
                "story_writing_agents.provider_catalog",
            }
            <= modules
        )

    def test_reference_callbacks_are_scoped_fresh_bounded_and_content_free(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        focused = _conversation_metadata("c_0123456789", "Focused")
        controller._attached_session_id = "session"
        controller._focused_conversation = focused

        scope_cases = (
            ("ordinary $", 10, ReferenceInputScope(0, 10)),
            ("@plot revise $", 14, ReferenceInputScope(6, 14)),
            ("/say revise $", 13, ReferenceInputScope(5, 13)),
            ("/run --fresh revise $", 21, ReferenceInputScope(13, 21)),
            ("/status $", 9, None),
            ("/run --output part.md $", 18, None),
        )
        for buffer, cursor, expected in scope_cases:
            with self.subTest(buffer=buffer):
                self.assertEqual(
                    controller._main_reference_scope(buffer, cursor),
                    expected,
                )
        controller._focused_conversation = None
        self.assertIsNone(controller._main_reference_scope("ordinary $", 10))
        controller._focused_conversation = focused
        controller._attached_session_id = None
        self.assertIsNone(controller._main_reference_scope("ordinary $", 10))
        self.assertEqual(
            controller._composition_reference_scope("line $", 3),
            ReferenceInputScope(0, 6),
        )

        repository = controller._runtime.artifacts
        snapshots = (
            ("core_documents/global_outline.md",),
            ("core_documents/character_sheets.md",),
        )
        with mock.patch.object(
            repository,
            "list_paths",
            side_effect=snapshots,
        ) as list_paths:
            self.assertEqual(controller._reference_paths(), snapshots[0])
            self.assertEqual(controller._reference_paths(), snapshots[1])
        self.assertEqual(list_paths.call_count, 2)

        path = snapshots[0][0]
        with mock.patch.object(
            repository,
            "read_text",
            return_value="private referenced bytes",
        ) as read_text:
            self.assertEqual(
                controller._reference_text(path),
                "private referenced bytes",
            )
        read_text.assert_called_once_with(path, max_bytes=MAX_VIEWER_UTF8_BYTES)

        failures = (
            (
                "list_paths",
                OSError("catalog secret"),
                ReferenceInputFailureKind.UNAVAILABLE,
            ),
            (
                "read_text",
                WorkspaceSecurityError("race secret"),
                ReferenceInputFailureKind.STALE,
            ),
            (
                "read_text",
                WorkspaceFormatError("decode secret"),
                ReferenceInputFailureKind.UNREADABLE,
            ),
            (
                "read_text",
                OSError("storage secret"),
                ReferenceInputFailureKind.UNAVAILABLE,
            ),
        )
        for method, failure, expected in failures:
            with self.subTest(method=method, expected=expected), mock.patch.object(
                repository,
                method,
                side_effect=failure,
            ):
                with self.assertRaises(ReferenceInputFailure) as raised:
                    if method == "list_paths":
                        controller._reference_paths()
                    else:
                        controller._reference_text(path)
                self.assertIs(raised.exception.kind, expected)
                self.assertEqual(str(raised.exception), expected.value)
                self.assertNotIn("secret", str(raised.exception))


class ControllerDebateVisibilityTests(ControllerTestCase):
    """Verify debate handoffs and blackboard message presentation."""

    def _event(
        self,
        phase: DebateProgressPhase,
        *,
        input_kind: InputKind = InputKind.PROMPT,
        revision_id: str | None = None,
        round_number: int = 0,
    ) -> DebateProgressEvent:
        """Build one valid event for direct visibility projections."""

        return DebateProgressEvent(
            session_id="session",
            debate_id="debate",
            input_kind=input_kind,
            phase=phase,
            revision_id=revision_id,
            round_number=round_number,
        )

    def test_draft_and_synthesis_handoffs_match_runtime_flow(self) -> None:
        workspace = self.make_workspace()
        controller, backend, _callbacks = self.make_controller(workspace)

        draft_start = controller._debate_handoff_for_phase(
            self._event(DebateProgressPhase.STARTED, input_kind=InputKind.DRAFT)
        )
        self.assertEqual(
            draft_start,
            (
                ("user",),
                (EXECUTION_SPECIALIST,),
                "Ask Execution Specialist to author revision 0 from the supplied draft.",
            ),
        )
        drafted = (
            (EXECUTION_SPECIALIST,),
            tuple(SPECIALIST_IDS),
            "Draft complete; request specialist audits.",
        )
        self.assertEqual(
            controller._debate_handoff_for_phase(
                self._event(
                    DebateProgressPhase.DRAFTED,
                    input_kind=InputKind.DRAFT,
                    revision_id="r0",
                )
            ),
            drafted,
        )
        self.assertEqual(
            controller._debate_handoff_for_phase(
                self._event(
                    DebateProgressPhase.DRAFTED,
                    input_kind=InputKind.PROMPT,
                    revision_id="r0",
                )
            ),
            drafted,
        )
        synthesized = controller._debate_handoff_for_phase(
            self._event(
                DebateProgressPhase.SYNTHESIZED,
                revision_id="r1",
                round_number=1,
            )
        )
        self.assertEqual(synthesized[0], ("lead_orchestrator",))
        self.assertEqual(synthesized[1], tuple(SPECIALIST_IDS))
        backend.close()

    def test_agent_panel_filters_user_entries_and_redacts_errors(self) -> None:
        workspace = self.make_workspace()
        controller, backend, callbacks = self.make_controller(workspace)
        for message in (
            DebateMessage(
                "user", "started", 0, "", "user", "private prompt", _CREATED
            ),
            DebateMessage(
                PLOT_ARCHITECT,
                "audit_completed",
                0,
                "r0",
                "critique",
                "visible critique",
                _CREATED,
            ),
            DebateMessage(
                PLOT_ARCHITECT,
                "failed",
                0,
                "r0",
                "error",
                "provider secret details",
                _CREATED,
            ),
        ):
            workspace.append_debate_message("session", "debate", message)

        controller._render_debate_agent_sayings(
            self._event(DebateProgressPhase.STARTED)
        )
        rendered = "".join(callbacks.records)
        self.assertIn("visible critique", rendered)
        self.assertIn("Agent error details are unavailable.", rendered)
        self.assertNotIn("private prompt", rendered)
        self.assertNotIn("provider secret details", rendered)
        agent_records = [
            record for record in callbacks.records if "Agent saying" in record
        ]
        self.assertEqual(len(agent_records), 1)
        self.assertTrue(agent_records[0].startswith("--- Agent saying [completed]"))
        self.assertTrue(agent_records[0].endswith(f"{'-' * 80}\n"))

        callbacks.records.clear()
        controller._render_debate_agent_sayings(
            self._event(DebateProgressPhase.DRAFTED, revision_id="r0")
        )
        self.assertNotIn("Agent saying", "".join(callbacks.records))
        backend.close()

    def test_malformed_agent_log_emits_safe_warning(self) -> None:
        workspace = self.make_workspace()
        controller, backend, callbacks = self.make_controller(workspace)
        with mock.patch.object(
            workspace,
            "read_debate_messages",
            side_effect=WorkspaceFormatError("raw provider details"),
        ):
            controller._render_debate_agent_sayings(
                self._event(DebateProgressPhase.STARTED)
            )
        rendered = "".join(callbacks.records)
        self.assertIn("Agent sayings unavailable; the debate log could not be read.", rendered)
        self.assertNotIn("raw provider details", rendered)
        backend.close()


class ControllerStartupTests(ControllerTestCase):
    """Verify explicit, defaulted, confirmed, and failed startup paths."""

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

    def test_explicit_sessions_create_open_and_reopen_without_confirmation(
        self,
    ) -> None:
        cases = (
            ("missing", None, "created"),
            ("ready", SessionDurableState.READY, "opened"),
            ("paused", SessionDurableState.PAUSED, "reopened"),
        )
        for session_id, initial_state, attachment in cases:
            with self.subTest(session=session_id):
                workspace = self.make_workspace()
                if initial_state is not None:
                    self.seed_session(
                        workspace,
                        session_id,
                        durable_state=initial_state,
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
                    load_cli_context(workspace).default_session_id,
                    session_id,
                )
                self.assertEqual(
                    workspace.read_json(_manifest_relative(session_id))[
                        "durable_state"
                    ],
                    "ready",
                )
                self.assertFalse(
                    workspace.path(_access_relative(session_id)).exists()
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

    def test_saved_ready_opens_without_rewriting_default(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "selected", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "selected")
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
        self.assertIn("Session: selected (opened)\n", callbacks.records[0])
        self.assertEqual(callbacks.prompts, ["story[selected:lead]> "])

    def test_saved_paused_confirmation_outcomes_are_distinct(self) -> None:
        cases = (
            ("default_yes", "", "ready", False, "Session: saved (reopened)"),
            ("no", "no", "paused", False, "Session resume cancelled."),
            (
                "interrupt",
                KeyboardInterrupt(),
                "paused",
                False,
                "Session resume cancelled.",
            ),
            ("eof", EOFError(), "paused", True, None),
        )
        for label, decision, durable_state, exits_at_prompt, marker in cases:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(workspace, "saved", durable_state=SessionDurableState.PAUSED)
                save_default_session(workspace, "saved")
                before = _tree_snapshot(workspace.root)
                reads = (decision,) if exits_at_prompt else (decision, "/quit")
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    *reads,
                )
                self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(
                    callbacks.prompts[0],
                    _confirmation_prompt(
                        "Resume default session saved?",
                        default=True,
                    ),
                )
                self.assertEqual(
                    workspace.read_json(_manifest_relative("saved"))[
                        "durable_state"
                    ],
                    durable_state,
                )
                self.assertEqual(
                    load_cli_context(workspace).default_session_id,
                    "saved",
                )
                if marker is not None:
                    self.assertIn(marker, "".join(callbacks.records))
                if durable_state == "paused":
                    self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertFalse(
                    workspace.path(_access_relative("saved")).exists()
                )

    def test_startup_failures_use_typed_safe_mapping(self) -> None:
        cases: tuple[
            tuple[type[Exception], InteractiveErrorCode, bool], ...
        ] = (
            (SessionIdError, InteractiveErrorCode.SESSION_INVALID, True),
            (SessionExistsError, InteractiveErrorCode.SESSION_EXISTS, True),
            (SessionNotFoundError, InteractiveErrorCode.SESSION_NOT_FOUND, True),
            (SessionPausedError, InteractiveErrorCode.SESSION_PAUSED, True),
            (SessionAccessBlockedError, InteractiveErrorCode.SESSION_ACCESS_BLOCKED, True),
            (
                SessionOwnershipUnknownError,
                InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN,
                True,
            ),
            (
                WorkspaceVersionUnsupportedError,
                InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED,
                True,
            ),
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
                    terminal_width=200,
                )
                failure = failure_type("secret-token-from-backend")
                with mock.patch.object(
                    backend,
                    "get_session_lifecycle",
                    side_effect=failure,
                ):
                    result = controller.run_session_lobby(
                        requested_session="target"
                    )
                self.assertEqual(result, 0 if recoverable else 2)
                rendered = "".join(callbacks.records)
                self.assertIn(_error_record(code, width=200), rendered)
                self.assertNotIn("secret-token-from-backend", rendered)
                self.assertFalse(workspace.path(_CONTEXT_PATH).exists())
                if recoverable:
                    self.assertIn("Session: lobby\n", rendered)
                else:
                    self.assertNotIn("Session: lobby\n", rendered)

    def test_missing_default_and_paused_open_race_return_to_lobby(
        self,
    ) -> None:
        workspace = self.make_workspace()
        save_default_session(workspace, "missing")
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
        self.seed_session(workspace, "raced", durable_state=SessionDurableState.READY)
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/quit",
            backend=backend,
        )

        def pause_then_fail(session_id: str) -> SessionLifecycleState:
            manifest = workspace.read_json(_manifest_relative(session_id))
            manifest["durable_state"] = "paused"
            workspace.write_json(_manifest_relative(session_id), manifest)
            raise SessionPausedError("paused-race-secret")

        with mock.patch.object(
            backend,
            "acquire_session_access",
            side_effect=pause_then_fail,
        ):
            self.assertEqual(
                controller.run_session_lobby(requested_session="raced"),
                0,
            )
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.SESSION_PAUSED), rendered)
        self.assertNotIn("paused-race-secret", rendered)
        self.assertIn("Session: lobby\n", rendered)
        self.assertFalse(workspace.path(_CONTEXT_PATH).exists())

    def test_acquisition_rejects_mismatched_or_unowned_summaries(self) -> None:
        mutations: tuple[
            tuple[str, Callable[[SessionLifecycleState], object]], ...
        ] = (
            ("wrong_id", lambda value: replace(value, session_id="other")),
            ("paused", lambda value: replace(value, durable_state=SessionDurableState.PAUSED)),
            (
                "available",
                lambda value: replace(
                    value,
                    access_state=SessionAccessState.CLAIMABLE,
                ),
            ),
            ("wrong_type", lambda _value: object()),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(workspace, "target", durable_state=SessionDurableState.READY)
                backend = self.make_backend(workspace)
                original_attach = backend.acquire_session_access

                def invalid_attach(session_id: str) -> object:
                    acquired = original_attach(session_id)
                    return mutate(acquired)

                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    "/quit",
                    backend=backend,
                )
                with mock.patch.object(
                    backend,
                    "acquire_session_access",
                    side_effect=invalid_attach,
                ):
                    self.assertEqual(
                        controller.run_session_lobby(
                            requested_session="target"
                        ),
                        0,
                    )
                self.assertIn(
                    _error_record(InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED),
                    callbacks.records,
                )
                self.assertFalse(workspace.path(_CONTEXT_PATH).exists())
                self.assertFalse(
                    workspace.path(_access_relative("target")).exists()
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
                self.seed_session(workspace, "session", durable_state=SessionDurableState.READY)
                save_default_session(workspace, "session")
                catalog = MemoryConversationCatalog(
                    () if entry is None else (entry,)
                )
                if persist_file:
                    save_default_conversation(
                        workspace,
                        "session",
                        selected_id,
                    )
                    selection_path = workspace.path(
                        _default_relative("session")
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
                    if record.startswith("--- Conversation scope")
                ]
                if label == "active":
                    self.assertEqual(len(scopes), 1)
                    for fact in (
                        "Session: session",
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
                            _conversation_scope_record(
                                "session",
                                None,
                                None,
                                width=80,
                            )
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
                        workspace.path(_default_relative("session")).exists()
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
                self.seed_session(workspace, "session", durable_state=SessionDurableState.READY)
                save_default_session(workspace, "session")
                selection_path = workspace.root / _default_relative("session")
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
                    workspace.path(_access_relative("session")).exists()
                )
                self.assertEqual(callbacks.prompts, [])

    def test_injected_startup_metadata_tampering_is_internal_and_fatal(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "session", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "session")
        save_default_conversation(workspace, "session", _UNIQUE_C)
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
        self.assertFalse(workspace.path(_access_relative("session")).exists())

    def test_switch_validates_destination_conversation_before_session_commit(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(workspace, session_id, durable_state=SessionDurableState.READY)
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
        save_default_session(workspace, "source")
        save_default_conversation(workspace, "source", source.conversation_id)
        save_default_conversation(
            workspace,
            "destination",
            destination.conversation_id,
        )
        selection_snapshots = {
            session_id: workspace.path(_default_relative(session_id)).read_bytes()
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
        original_attach = backend.acquire_session_access
        original_detach = backend.release_session_access
        original_load = controller_core_module.load_default_conversation
        original_select = controller_core_module.save_default_session
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
            mock.patch.object(backend, "acquire_session_access", side_effect=attach),
            mock.patch.object(backend, "release_session_access", side_effect=detach),
            mock.patch.object(
                controller_core_module,
                "load_default_conversation",
                side_effect=load_selection,
            ),
            mock.patch.object(
                controller_core_module,
                "save_default_session",
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
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("--- Conversation scope")
            ],
            [
                _conversation_scope_record(
                    "source",
                    source.conversation_id[:12],
                    "Source subject",
                    width=80,
                ),
                _conversation_scope_record(
                    "destination",
                    destination.conversation_id[:12],
                    "Destination subject",
                    width=80,
                ),
            ],
        )
        self.assertEqual(
            load_cli_context(workspace).default_session_id,
            "destination",
        )
        for session_id, expected in selection_snapshots.items():
            self.assertEqual(
                workspace.path(_default_relative(session_id)).read_bytes(),
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
            self.seed_session(workspace, session_id, durable_state=SessionDurableState.READY)
        source = _conversation_metadata(
            _UNIQUE_C,
            "Source subject",
            session_id="source",
        )
        catalog = MemoryConversationCatalog((source,))
        save_default_session(workspace, "source")
        save_default_conversation(workspace, "source", source.conversation_id)
        malformed = workspace.root / _default_relative("destination")
        malformed.parent.mkdir(parents=True, exist_ok=True)
        malformed.write_text("{not-json}\n", encoding="utf-8")
        source_selection = workspace.path(
            _default_relative("source")
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
        original_detach = backend.release_session_access

        def detach(session_id: str):
            events.append(session_id)
            return original_detach(session_id)

        with mock.patch.object(backend, "release_session_access", side_effect=detach):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(events, ["destination", "source"])
        self.assertEqual(load_cli_context(workspace).default_session_id, "source")
        self.assertEqual(
            workspace.path(_default_relative("source")).read_bytes(),
            source_selection,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID),
            rendered,
        )
        self.assertEqual(
            callbacks.records.count(
                _conversation_scope_record(
                    "source",
                    source.conversation_id[:12],
                    "Source subject",
                    width=80,
                )
            ),
            1,
        )

    def test_archived_destination_selection_switches_to_lobby_without_rewrite(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(workspace, session_id, durable_state=SessionDurableState.READY)
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
        save_default_session(workspace, "source")
        for session_id, selected_id in (
            ("source", source.conversation_id),
            ("destination", destination.conversation_id),
        ):
            save_default_conversation(workspace, session_id, selected_id)
        selection_snapshots = {
            session_id: (
                workspace.path(_default_relative(session_id)).read_bytes(),
                workspace.path(_default_relative(session_id)).stat().st_mtime_ns,
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
            load_cli_context(workspace).default_session_id,
            "destination",
        )
        for session_id, snapshot in selection_snapshots.items():
            path = workspace.path(_default_relative(session_id))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)
        rendered = "".join(callbacks.records)
        self.assertIn("Subject: Source subject", rendered)
        self.assertIn(
            _conversation_scope_record("destination", None, None),
            rendered,
        )
        self.assertNotIn("[error conversation_archived]", rendered)


class ControllerCommandTests(ControllerTestCase):
    """Verify global commands and intentionally unavailable shell domains."""

    def test_handled_error_then_success_has_independent_terminal_records(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/search PRIVATE_SEARCH_TEXT",
            "/help",
            "/quit",
        )

        self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            callbacks.records.count(
                _error_record(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            ),
            1,
        )
        raw = workspace.path("debug_logs/runtime.jsonl").read_text(
            encoding="utf-8"
        )
        records = [
            record
            for record in (json.loads(line) for line in raw.splitlines())
            if record["component"] == "interactive_controller"
            and record["operation"] == "interactive_command"
        ]
        self.assertEqual(
            [
                (
                    record["level"],
                    record["phase"],
                    record.get("outcome"),
                )
                for record in records
            ],
            [
                ("trace", "started", None),
                ("trace", "failed", "rejected"),
                ("trace", "started", None),
                ("trace", "succeeded", "completed"),
                ("trace", "started", None),
                ("trace", "succeeded", "completed"),
            ],
        )
        for record in records:
            self.assertNotIn("session_id", record)
            self.assertNotIn("conversation_id", record)
            self.assertNotIn("agent_id", record)
        for prohibited in (
            "PRIVATE_SEARCH_TEXT",
            "/search",
            str(workspace.root),
        ):
            self.assertNotIn(prohibited, raw)

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
            "/session pause",
            "/exit",
        )
        with (
            mock.patch.object(
                controller_core_module,
                "parse_interactive_line",
                wraps=controller_core_module.parse_interactive_line,
            ) as parse_line,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(
            parse_line.call_args_list,
            [
                mock.call(" "),
                mock.call("/? session"),
                mock.call("/sesion list"),
                mock.call("ordinary provider message"),
                mock.call("/run create artifact"),
                mock.call("/inspect last"),
                mock.call("/to plot"),
                mock.call("/session list"),
                mock.call("/session pause"),
                mock.call("/exit"),
            ],
        )
        self.assertEqual(_tree_snapshot(workspace.root), before)

        rendered = "".join(callbacks.records)
        self.assertIn("[cancelled] No action was taken.\n", rendered)
        self.assertIn(
            "Syntax: /session list | create NAME | resume [NAME] | pause",
            rendered,
        )
        self.assertIn("[error command_invalid] unknown command: /sesion\n", rendered)
        self.assertIn("[hint] Try /session, or type /help.\n", rendered)
        unavailable = (
            "[error command_invalid] This command is not available in the "
            "session lobby.\n"
        )
        self.assertEqual(rendered.count(unavailable), 0)
        self.assertEqual(rendered.count("[error session_not_attached]"), 5)
        self.assertIn("[session] No story sessions found.\n", rendered)
        self.assertIn(
            _error_record(InteractiveErrorCode.SESSION_NOT_ATTACHED),
            rendered,
        )

    def test_session_list_preserves_order_and_hides_access_owner_facts(self) -> None:
        workspace = self.make_workspace()
        backend = self.make_backend(workspace)
        summaries = (
            _lifecycle("beta", durable_state=SessionDurableState.PAUSED),
            _lifecycle(
                "alpha",
                access_state=SessionAccessState.OWNERSHIP_UNKNOWN,
                hostname="remote\x1bhost",
                pid=321,
            ),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session list",
            "/quit",
            context=CliContext(default_session_id="alpha"),
            backend=backend,
            terminal_width=240,
        )
        before = _tree_snapshot(workspace.root)
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            """Retain exact controller trees while preserving real rendering."""

            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                backend,
                "list_session_lifecycles",
                return_value=summaries,
            ) as list_session_lifecycles,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        list_session_lifecycles.assert_called_once_with()
        self.assertEqual(_tree_snapshot(workspace.root), before)
        session_records = [
            record
            for record in callbacks.records
            if record.startswith("Story sessions (")
        ]
        self.assertEqual(len(session_records), 2)
        session_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "Story sessions"
        ]
        expected_views = [
            terminal_module.ListView(
                "Story sessions",
                (
                    terminal_module.Record(
                        "session",
                        fields=(
                            terminal_module.Field("Session", "beta"),
                            terminal_module.Field("Durable state", "paused"),
                            terminal_module.Field("Process access", "claimable"),
                            terminal_module.Field("Default session", "no"),
                            terminal_module.Field("Provider readiness", "no"),
                            terminal_module.Field(
                                "Next action",
                                "Resume the session to make it ready.",
                            ),
                        ),
                        state=terminal_module.ViewState.COMPLETED,
                    ),
                ),
                total_count=2,
                position=1,
                state=terminal_module.ViewState.COMPLETED,
            ),
            terminal_module.ListView(
                "Story sessions",
                (
                    terminal_module.Record(
                        "session",
                        fields=(
                            terminal_module.Field("Session", "alpha"),
                            terminal_module.Field("Durable state", "ready"),
                            terminal_module.Field(
                                "Process access",
                                "ownership_unknown",
                            ),
                            terminal_module.Field("Default session", "yes"),
                            terminal_module.Field("Provider readiness", "no"),
                            terminal_module.Field(
                                "Next action",
                                "Verify ownership; do not remove the access claim.",
                            ),
                        ),
                        state=terminal_module.ViewState.COMPLETED,
                    ),
                ),
                total_count=2,
                position=2,
                state=terminal_module.ViewState.COMPLETED,
            ),
        ]
        self.assertEqual(session_views, expected_views)
        self.assertEqual(
            session_records,
            [
                f"{render_plain(view, width=240).text}\n"
                for view in expected_views
            ],
        )
        beta, alpha = session_records
        for fact in (
            "beta",
            "Durable state: paused",
            "Process access: claimable",
            "Default session: no",
            "Provider readiness: no",
            "Next action: Resume the session to make it ready.",
        ):
            with self.subTest(session="beta", fact=fact):
                self.assertIn(fact, beta)
        for fact in (
            "alpha",
            "Durable state: ready",
            "Process access: ownership_unknown",
            "Default session: yes",
            "Provider readiness: no",
            "Next action: Verify ownership; do not remove the access claim.",
        ):
            with self.subTest(session="alpha", fact=fact):
                self.assertIn(fact, alpha)
        self.assertNotIn("321", alpha)
        self.assertNotIn("remote", alpha)
        self.assertFalse(
            any(
                component.state is terminal_module.ViewState.LOADING
                for component in session_views
            )
        )

    def test_empty_session_list_is_one_explicit_semantic_callback(self) -> None:
        workspace = self.make_workspace()
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session list",
            "/quit",
            backend=backend,
        )
        before = _tree_snapshot(workspace.root)
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                backend,
                "list_session_lifecycles",
                return_value=(),
            ) as list_session_lifecycles,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        list_session_lifecycles.assert_called_once_with()
        self.assertEqual(_tree_snapshot(workspace.root), before)
        expected = terminal_module.ListView(
            "Story sessions",
            (),
            total_count=0,
            empty_notice=terminal_module.Notice(
                "No story sessions found.",
                label="session",
                role=terminal_module.SemanticRole.MUTED,
                state=terminal_module.ViewState.EMPTY,
            ),
            state=terminal_module.ViewState.EMPTY,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.ListView
            ],
            [expected],
        )
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("Story sessions (")
            ],
            [f"{render_plain(expected, width=200).text}\n"],
        )

    def test_main_session_resume_provider_is_live_canonical_and_immutable(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("current", "saved"):
            self.seed_session(
                workspace,
                session_id,
                durable_state=SessionDurableState.READY,
            )
        save_default_session(workspace, "current")
        backend = self.make_backend(workspace)
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            backend=backend,
        )
        states = (
            _lifecycle("saved", durable_state=SessionDurableState.PAUSED),
            _lifecycle("current"),
        )
        captured: dict[str, object] = {}

        def capture_provider(
            _main_input: object,
            provider: Callable[[str, int], object],
            **_kwargs: object,
        ) -> terminal_module.TerminalAction:
            controller._context = CliContext(default_session_id="saved")
            captured["picker"] = provider("/session resume", 15)
            captured["explicit"] = provider("/session resume saved", 21)
            captured["midline"] = provider("/session resume", 8)
            list_lifecycles.return_value = ()
            captured["empty"] = provider("/session resume", 15)
            list_lifecycles.side_effect = OSError("snapshot-secret")
            captured["failure"] = provider("/session resume", 15)
            return terminal_module.TerminalAction(
                terminal_module.TerminalActionKind.LINE,
                text="/quit",
            )

        with (
            mock.patch.object(
                backend,
                "list_session_lifecycles",
                return_value=states,
            ) as list_lifecycles,
            mock.patch.object(
                terminal_module.InteractiveMainInput,
                "read_main",
                autospec=True,
                side_effect=capture_provider,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        picker = captured["picker"]
        self.assertEqual(
            picker,
            terminal_module.Picker(
                terminal_module.Prompt(
                    "Resume story session",
                    instruction="Choose a persisted session to resume.",
                ),
                (
                    terminal_module.Choice(
                        "/session resume saved",
                        "saved",
                        description="Durable: paused; access: claimable",
                    ),
                    terminal_module.Choice(
                        "/session resume current",
                        "current",
                        description="Durable: ready; access: claimable",
                    ),
                ),
                highlighted_id="/session resume current",
            ),
        )
        self.assertIsInstance(picker, terminal_module.Picker)
        assert isinstance(picker, terminal_module.Picker)
        self.assertIsInstance(picker.choices, tuple)
        with self.assertRaises(TypeError):
            picker.choices[0] = picker.choices[0]  # type: ignore[index]
        self.assertIsNone(captured["explicit"])
        self.assertIsNone(captured["midline"])
        self.assertEqual(
            captured["empty"],
            terminal_module.Picker(
                terminal_module.Prompt(
                    "Resume story session",
                    instruction="Choose a persisted session to resume.",
                ),
                (),
            ),
        )
        self.assertIsNone(captured["failure"])

    def test_live_session_resume_identity_reaches_parser_and_explicit_handler(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id in ("source", "destination"):
            self.seed_session(
                workspace,
                session_id,
                durable_state=SessionDurableState.READY,
            )
        save_default_session(workspace, "source")
        backend = self.make_backend(workspace)
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            backend=backend,
        )
        reads = 0

        def commit_live_choice(
            _main_input: object,
            provider: Callable[[str, int], object],
            **_kwargs: object,
        ) -> terminal_module.TerminalAction:
            nonlocal reads
            reads += 1
            if reads == 1:
                picker = provider("/session resume", 15)
                assert isinstance(picker, terminal_module.Picker)
                return terminal_module.TerminalAction(
                    terminal_module.TerminalActionKind.LINE,
                    text=picker.choices[1].identity,
                )
            return terminal_module.TerminalAction(
                terminal_module.TerminalActionKind.LINE,
                text="/quit",
            )

        with (
            mock.patch.object(
                backend,
                "list_session_lifecycles",
                return_value=(
                    _lifecycle("source"),
                    _lifecycle("destination"),
                ),
            ),
            mock.patch.object(
                terminal_module.InteractiveMainInput,
                "read_main",
                autospec=True,
                side_effect=commit_live_choice,
            ),
            mock.patch.object(
                controller_core_module,
                "parse_interactive_line",
                wraps=controller_core_module.parse_interactive_line,
            ) as parse,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            parse.call_args_list.count(
                mock.call("/session resume destination")
            ),
            1,
        )
        self.assertEqual(
            load_cli_context(workspace).default_session_id,
            "destination",
        )

    def test_open_without_name_and_same_session_noop(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume",
            "/session create action_session",
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        self.assertIn("[session] No story sessions found.\n", rendered)
        self.assertNotIn("Resume story session", "".join(callbacks.prompts))
        self.assertIn("Session: action_session (created)\n", rendered)
        self.assertEqual(
            load_cli_context(workspace).default_session_id,
            "action_session",
        )

        workspace = self.make_workspace()
        self.seed_session(workspace, "source", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "source")
        context_path = workspace.path(_CONTEXT_PATH)
        context_before = (
            context_path.read_bytes(),
            context_path.stat().st_mtime_ns,
        )
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume",
            "",
            "/quit",
            backend=backend,
        )
        with (
            mock.patch.object(
                backend,
                "acquire_session_access",
                wraps=backend.acquire_session_access,
            ) as attach,
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
            mock.patch.object(
                controller_core_module,
                "save_default_session",
                wraps=save_default_session,
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
        self.assertEqual(rendered.count("Session: source (opened)\n"), 1)
        self.assertIn("[session] Already attached to source.\n", rendered)

    def test_omitted_open_picker_preserves_order_rows_and_default_highlight(
        self,
    ) -> None:
        workspace = self.make_workspace()
        for session_id, durable_state in (
            ("alpha", SessionDurableState.READY),
            ("beta", SessionDurableState.PAUSED),
        ):
            self.seed_session(
                workspace,
                session_id,
                durable_state=durable_state,
            )
        save_default_session(workspace, "beta")
        backend = self.make_backend(workspace)
        lifecycles = (
            _lifecycle("alpha"),
            _lifecycle("beta", durable_state=SessionDurableState.PAUSED),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "n",
            "/session resume",
            "",
            "/quit",
            backend=backend,
            terminal_width=120,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker

        with (
            mock.patch.object(
                backend,
                "list_session_lifecycles",
                return_value=lifecycles,
            ) as list_lifecycles,
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=original_picker,
            ) as picker,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        list_lifecycles.assert_called_once_with()
        picker.assert_called_once()
        picker_call = picker.call_args
        self.assertEqual(
            picker_call.args[1],
            terminal_module.Prompt(
                "Resume story session",
                instruction="Choose a persisted session to resume.",
            ),
        )
        self.assertEqual(
            picker_call.args[2],
            (
                terminal_module.Choice(
                    "alpha",
                    "alpha",
                    description="Durable: ready; access: claimable",
                ),
                terminal_module.Choice(
                    "beta",
                    "beta",
                    description="Durable: paused; access: claimable",
                ),
            ),
        )
        self.assertEqual(picker_call.kwargs["highlighted_id"], "beta")
        picker_prompt = next(
            prompt for prompt in callbacks.prompts if "Resume story session" in prompt
        )
        self.assertLess(picker_prompt.index("alpha"), picker_prompt.index("beta"))
        self.assertIn("1. [available] alpha", picker_prompt)
        self.assertIn("Durable: ready; access: claimable", picker_prompt)
        self.assertIn("2. [highlighted] beta", picker_prompt)
        self.assertIn("Durable: paused; access: claimable", picker_prompt)
        self.assertEqual(
            workspace.read_json(_manifest_relative("beta"))["durable_state"],
            "ready",
        )
        self.assertIn("Session: beta (reopened)\n", "".join(callbacks.records))

    def test_omitted_open_prefers_current_highlight_over_saved_default(self) -> None:
        workspace = self.make_workspace()
        for session_id in ("current", "saved"):
            self.seed_session(
                workspace,
                session_id,
                durable_state=SessionDurableState.READY,
            )
        save_default_session(workspace, "current")
        before = _tree_snapshot(workspace.root)
        backend = self.make_backend(workspace)
        controller: InteractiveController

        def open_with_distinct_saved_default(_prompt: str) -> str:
            controller._context = CliContext(default_session_id="saved")
            return "/session resume"

        controller, _backend, callbacks = self.make_controller(
            workspace,
            open_with_distinct_saved_default,
            "/cancel",
            "/quit",
            backend=backend,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=original_picker,
        ) as picker:
            self.assertEqual(controller.run_session_lobby(), 0)

        picker.assert_called_once()
        self.assertEqual(picker.call_args.kwargs["highlighted_id"], "current")
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertIn(
            "[cancelled] Session resume cancelled.\n",
            "".join(callbacks.records),
        )

    def test_omitted_open_fallback_ordinal_selects_ready_session(self) -> None:
        workspace = self.make_workspace()
        for session_id in ("alpha", "beta"):
            self.seed_session(
                workspace,
                session_id,
                durable_state=SessionDurableState.READY,
            )
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume",
            "2",
            "/quit",
            backend=backend,
        )
        with mock.patch.object(
            backend,
            "list_session_lifecycles",
            return_value=(_lifecycle("alpha"), _lifecycle("beta")),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(load_cli_context(workspace).default_session_id, "beta")
        self.assertIn("Session: beta (opened)\n", "".join(callbacks.records))
        self.assertFalse(workspace.path(_access_relative("beta")).exists())

    def test_omitted_open_cancel_interrupt_and_eof_are_nonmutating(self) -> None:
        cases = (
            ("cancel", "/cancel", False),
            ("interrupt", KeyboardInterrupt(), False),
            ("eof", EOFError(), True),
        )
        for label, picker_input, exits_from_picker in cases:
            with self.subTest(case=label):
                workspace = self.make_workspace()
                self.seed_session(
                    workspace,
                    "source",
                    durable_state=SessionDurableState.READY,
                )
                save_default_session(workspace, "source")
                before = _tree_snapshot(workspace.root)
                reads = (
                    ("/session resume", picker_input)
                    if exits_from_picker
                    else ("/session resume", picker_input, "/quit")
                )
                controller, backend, callbacks = self.make_controller(
                    workspace,
                    *reads,
                )

                self.assertEqual(controller.run_session_lobby(), 0)

                self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertTrue(backend._closed)
                self.assertFalse(
                    workspace.path(_access_relative("source")).exists()
                )
                rendered = "".join(callbacks.records)
                if exits_from_picker:
                    self.assertNotIn("Session resume cancelled.", rendered)
                    self.assertEqual(
                        callbacks.prompts[-1].splitlines()[-1],
                        "Resume story session > ",
                    )
                else:
                    self.assertIn(
                        "[cancelled] Session resume cancelled.\n",
                        rendered,
                    )

    def test_omitted_open_stale_selection_uses_existing_missing_error_path(
        self,
    ) -> None:
        workspace = self.make_workspace()
        before = _tree_snapshot(workspace.root)
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume",
            "",
            "/quit",
            backend=backend,
            terminal_width=200,
        )
        with mock.patch.object(
            backend,
            "list_session_lifecycles",
            return_value=(_lifecycle("stale"),),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(_tree_snapshot(workspace.root), before)
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.SESSION_NOT_FOUND, width=200),
            rendered,
        )
        self.assertIn("Session: lobby\n", rendered)
        self.assertFalse(workspace.path(_CONTEXT_PATH).exists())

    def test_declined_default_session_can_be_opened_by_omitted_command(self) -> None:
        workspace = self.make_workspace()
        self.seed_session(workspace, "saved", durable_state=SessionDurableState.PAUSED)
        save_default_session(workspace, "saved")
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "n",
            "/session resume",
            "",
            "/quit",
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            workspace.read_json(_manifest_relative("saved"))["durable_state"],
            "ready",
        )
        rendered = "".join(callbacks.records)
        self.assertIn("[cancelled] Session resume cancelled.\n", rendered)
        self.assertIn("Session: saved (reopened)\n", rendered)
        self.assertEqual(
            callbacks.prompts.count(
                _confirmation_prompt(
                    "Resume default session saved?",
                    default=True,
                )
            ),
            1,
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
            results=(
                InteractiveAuthResult(InteractiveAuthResultKind.CANCEL, both),
            ),
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


class ControllerSettingsTests(ControllerTestCase):
    """Verify isolated settings UI, validation, persistence, and cancellation."""

    VALUE_SAVE_GUIDANCE = (
        "Entered changes remain pending in memory and are not saved; choose Save in "
        "Settings to persist them."
    )

    def assert_value_save_guidance(self, instruction: str) -> None:
        """Require one exact terminal-visible persistence warning."""

        self.assertEqual(instruction.count(self.VALUE_SAVE_GUIDANCE), 1)
        self.assertTrue(instruction.endswith(self.VALUE_SAVE_GUIDANCE))

    @staticmethod
    def expected_pending_save_notice() -> terminal_module.Notice:
        return terminal_module.Notice(
            "Choose Save to persist pending changes",
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
                            provider if agent.backend is not None else f"{provider} (default)",
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
                role=terminal_module.SemanticRole.WARNING,
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
            items = (
                terminal_module.Section("Current", current_fields),
                terminal_module.Section("Pending", pending_fields),
                terminal_module.Notice(
                    "Changed values apply only to the next shell after Save.",
                    label="activation",
                    role=terminal_module.SemanticRole.MUTED,
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

    def test_provider_assignment_requires_fresh_auth_before_candidate_change(
        self,
    ) -> None:
        workspace = self.make_workspace()
        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        both = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(codex_only, codex_only, both, both, both),
            results=(_auth_result(both, "antigravity"),),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "5",
            "2",
            "1",
            "2",
            "/cancel",
            "/cancel",
            "/cancel",
            "6",
            "1",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=160,
        )
        self.assertEqual(controller.run_session_lobby(), 0)

        configuration = settings_module.load_settings_state(workspace).pending
        self.assertEqual(configuration.system_defaults.default_backend, "codex")
        self.assertEqual(
            configuration.agent_configs[PLOT_ARCHITECT].backend,
            "antigravity",
        )
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
                ("probe_all", None),
            ],
        )
        self.assertIn("Settings saved", "".join(callbacks.records))
        self.assertEqual(
            "".join(callbacks.prompts).count(
                "[entered] Pending Provider entered: Antigravity."
            ),
            1,
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
            "5",
            "2",
            "1",
            "2",
            "/cancel",
            "/cancel",
            "/cancel",
            "7",
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
            mock.patch.object(
                InteractiveController,
                "_edit_settings_agent_provider",
                autospec=True,
                side_effect=capture_returned_state,
            ),
            mock.patch.object(
                controller_core_module,
                "establish_settings_default_backend",
                wraps=controller_core_module.establish_settings_default_backend,
            ) as establish_default,
            mock.patch.object(
                controller_core_module,
                "select_conversation_storage",
            ) as select_storage,
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
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "5",
            "2",
            "1",
            "1",
            "2",
            "1",
            "1",
            "/cancel",
            "/cancel",
            "7",
            "1",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=160,
        )
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
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "5",
            "2",
            "1",
            "1",
            "2",
            "1",
            "1",
            "/cancel",
            "/cancel",
            "7",
            "1",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=160,
        )
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
            if prompt.endswith("Provider [Plot & Structure Architect] > ")
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
                choose_antigravity = terminal_module.ControlOutcome(
                    terminal_module.ControlOutcomeKind.COMMIT,
                    text="antigravity",
                )
                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    return_value=choose_antigravity,
                ):
                    unchanged, whole_cancel = (
                        controller._edit_settings_agent_provider(
                            settings_module.load_settings_state(workspace),
                            PLOT_ARCHITECT,
                        )
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

    def test_save_freshly_gates_each_distinct_effective_provider_once(self) -> None:
        workspace = self.make_workspace()
        settings_module.establish_default_backend(workspace, "codex")
        state = settings_module.update_agent_backend(
            settings_module.load_settings_state(workspace),
            PLOT_ARCHITECT,
            "antigravity",
        )
        settings_module.save_settings_state(workspace, state)
        codex_only = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        both = _auth_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        coordinator = ScriptedAuthCoordinator(
            snapshots=(codex_only, both),
            results=(_auth_result(both, "antigravity"),),
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "6",
            "1",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=160,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            coordinator.events,
            [
                ("probe_all", None),
                ("run", "antigravity"),
                ("probe_all", None),
            ],
        )
        self.assertIn("Settings saved", "".join(callbacks.records))

        second_workspace = self.make_workspace()
        settings_module.establish_default_backend(second_workspace, "codex")
        state = settings_module.update_agent_backend(
            settings_module.load_settings_state(second_workspace),
            PLOT_ARCHITECT,
            "antigravity",
        )
        settings_module.save_settings_state(second_workspace, state)
        coordinator = ScriptedAuthCoordinator(
            snapshots=(codex_only, codex_only),
            results=(_auth_result(both, "antigravity"),),
        )
        controller, _backend, callbacks = self.make_controller(
            second_workspace,
            "/settings",
            "6",
            "1",
            "7",
            "1",
            "/quit",
            auth_coordinator=coordinator,
            terminal_width=160,
        )
        self.assertEqual(controller.run_session_lobby(), 0)
        rendered = "".join(callbacks.records)
        self.assertIn("not authenticated", rendered)
        self.assertNotIn("Settings saved", rendered)
        self.assertEqual(
            coordinator.events,
            [
                ("probe_all", None),
                ("run", "antigravity"),
                ("probe_all", None),
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
        controller, _backend, _callbacks = self.make_controller(workspace)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.COMMIT,
                text=value,
            )

        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=(
                commit("language"),
                commit(changed),
                cancel,
                commit("language"),
                commit(baseline),
                cancel,
                cancel,
            ),
        ) as picker:
            controller._run_settings()

        pending_notice = self.expected_pending_save_notice()
        top_level_calls = [
            call
            for call in picker.call_args_list
            if call.args[1].name == "Settings"
        ]
        self.assertEqual(
            tuple(call.kwargs.get("notice") for call in top_level_calls),
            (None, pending_notice, None),
        )
        self.assertEqual(
            tuple(
                tuple(choice.description for choice in call.args[2])
                for call in top_level_calls
            ),
            (
                (
                    f"Current: {baseline}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    None,
                    None,
                    None,
                ),
                (
                    f"Pending: {changed}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    None,
                    None,
                    "Pending changes will be discarded.",
                ),
                (
                    f"Current: {baseline}",
                    "Current: default_kim_eun_hee",
                    "Current: 1",
                    "Current: filesystem",
                    None,
                    None,
                    None,
                ),
            ),
        )
        self.assertEqual(
            tuple(
                next(
                    choice
                    for choice in call.args[2]
                    if choice.identity == "save"
                ).warning_suffix
                for call in top_level_calls
            ),
            (None, "(*Required)", None),
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

    def test_agents_root_description_counts_every_ordered_pending_agent_value(
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

        def agents_description(state) -> str | None:
            controller, _backend, _callbacks = self.make_controller(workspace)
            interrupt = terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.INTERRUPT
            )
            with (
                mock.patch.object(
                    controller_core_module,
                    "load_settings_state",
                    return_value=state,
                ),
                mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    return_value=interrupt,
                ) as picker,
                mock.patch.object(
                    controller_core_module,
                    "select_conversation_storage",
                ) as select_storage,
            ):
                controller._run_settings()
            select_storage.assert_not_called()
            choices = picker.call_args.args[2]
            return next(
                choice.description
                for choice in choices
                if choice.identity == "providers"
            )

        self.assertIsNone(agents_description(baseline))
        for label, edit in edits:
            with self.subTest(label=label):
                self.assertEqual(agents_description(edit(baseline)), "1 pending change")

        plural = baseline
        for _label, edit in edits:
            plural = edit(plural)
        self.assertEqual(agents_description(plural), "5 pending changes")

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
            "7",
            "/quit",
            provider_catalog=catalog,
            terminal_width=120,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=original_picker,
            ) as picker,
            mock.patch.object(
                controller_core_module,
                "parse_interactive_line",
                wraps=controller_core_module.parse_interactive_line,
            ) as parse_line,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(parse_line.call_args_list, [mock.call("/settings"), mock.call("/quit")])
        popen.assert_not_called()
        run.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(catalog.events, [])
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertIn("Current: KO", callbacks.prompts[1])
        self.assertIn("Current: default_kim_eun_hee", callbacks.prompts[1])
        self.assertIn("Current: 1", callbacks.prompts[1])
        self.assertIn("Current: filesystem", callbacks.prompts[1])
        settings_call = picker.call_args_list[0]
        self.assertEqual(settings_call.args[1].name, "Settings")
        self.assertIsNone(settings_call.kwargs["notice"])
        self.assertEqual(
            callbacks.prompts[1],
            "Choices (highlighted 1/7) [open]\n"
            "  1. [highlighted] Language — Current: KO\n"
            "  2. [available] Persona — Current: default_kim_eun_hee\n"
            "  3. [available] Maximum debate rounds — Current: 1\n"
            "  4. [available] Conversation storage — Current: filesystem\n"
            "  5. [available] Agents\n"
            "  6. [available] Save\n"
            "  7. [available] Cancel\n"
            "[input] Enter a choice number, press Enter for the highlighted choice, or enter /cancel.\n"
            "Choose a setting to edit, save, or cancel.\n"
            "Settings > ",
        )
        self.assertEqual(
            tuple(choice.identity for choice in settings_call.args[2]),
            (
                "language",
                "persona",
                "max_debate_rounds",
                "conversation_storage",
                "providers",
                "save",
                "cancel",
            ),
        )
        self.assertEqual(
            tuple(choice.label for choice in settings_call.args[2]),
            (
                "Language",
                "Persona",
                "Maximum debate rounds",
                "Conversation storage",
                "Agents",
                "Save",
                "Cancel",
            ),
        )
        self.assertEqual(
            tuple(choice.description for choice in settings_call.args[2]),
            (
                "Current: KO",
                "Current: default_kim_eun_hee",
                "Current: 1",
                "Current: filesystem",
                None,
                None,
                None,
            ),
        )
        self.assertTrue(
            all(choice.warning_suffix is None for choice in settings_call.args[2])
        )
        self.assertFalse(
            any("Cancel settings?" in prompt for prompt in callbacks.prompts)
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
            "5",
            "2",
            "1",
            "1",
            "/cancel",
            "/cancel",
            "/cancel",
            "7",
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
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        state = settings_module.load_settings_state(workspace)
        try:
            with mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(cancel, cancel, cancel),
            ):
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
        persona_values = tuple(
            entry.name for entry in state.baseline.persona_catalog
        )
        self.assertTrue(persona_values)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.COMMIT,
                text=value,
            )

        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
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
                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    side_effect=(commit(values[0]), commit(values[1]), cancel),
                ) as picker:
                    updated, whole_cancel = editor(state)

                self.assertFalse(whole_cancel)
                self.assertEqual(pending_value(updated), values[1])
                expected_calls = 2 if values[0] == values[1] else 3
                self.assertEqual(picker.call_count, expected_calls)
                self.assertIn("Enter applies", picker.call_args_list[0].args[1].instruction)
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

    def test_rounds_editor_retains_valid_value_and_clears_stale_success(
        self,
    ) -> None:
        workspace = self.make_workspace()
        controller, _backend, callbacks = self.make_controller(workspace)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.COMMIT,
                text=value,
            )

        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with mock.patch.object(
            terminal_module.InteractivePromptInput,
            "read_prompt",
            autospec=True,
            side_effect=(commit("2"), commit("bad"), commit("3"), cancel),
        ) as prompt:
            updated, whole_cancel = controller._edit_settings_rounds(
                settings_module.load_settings_state(workspace)
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(updated.pending.system_defaults.max_debate_rounds, 3)
        self.assertEqual(prompt.call_count, 4)
        self.assertIn("Enter applies", prompt.call_args_list[0].args[1].instruction)
        self.assertIn("press Esc", prompt.call_args_list[0].args[1].instruction)
        self.assert_value_save_guidance(
            prompt.call_args_list[0].args[1].instruction
        )
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

        with mock.patch.object(
            terminal_module.InteractivePromptInput,
            "read_prompt",
            autospec=True,
            side_effect=(commit("1"), commit("1")),
        ) as unchanged_prompt:
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
                call.kwargs["initial_value"]
                for call in unchanged_prompt.call_args_list
            ),
            ("1", "1"),
        )

    def test_provider_editor_reopens_with_latest_notice_and_selection(self) -> None:
        workspace = self.make_workspace()
        controller, _backend, _callbacks = self.make_controller(workspace)
        commit_antigravity = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="antigravity",
        )
        commit_codex = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="codex",
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=(commit_antigravity, commit_codex, cancel),
        ) as picker:
            updated, whole_cancel = controller._edit_settings_agent_provider(
                settings_module.load_settings_state(workspace),
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(
            updated.pending.agent_configs[PLOT_ARCHITECT].backend,
            "codex",
        )
        self.assert_value_save_guidance(
            picker.call_args_list[0].args[1].instruction
        )
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
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            return_value=cancel,
        ) as authenticated_picker:
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
        catalog = ScriptedProviderCatalog(
            _ready_provider_catalog("codex", *models)
        )
        controller, _backend, callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
        )
        state = settings_module.load_settings_state(workspace)
        persona_values = tuple(
            entry.name for entry in state.baseline.persona_catalog
        )
        self.assertTrue(persona_values)

        def commit(value: str) -> terminal_module.ControlOutcome:
            return terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.COMMIT,
                text=value,
            )

        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )

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
                    mock.patch.object(
                        terminal_module.InteractivePickerInput,
                        "read_picker",
                        autospec=True,
                        side_effect=(
                            commit(first),
                            commit(second),
                            commit(second),
                        ),
                    ) as picker,
                    mock.patch.object(
                        controller_core_module,
                        updater_name,
                        wraps=updater,
                    ) as update,
                ):
                    updated, whole_cancel = editor(candidate)
                self.assertFalse(whole_cancel)
                self.assertEqual(picker.call_count, 3)
                self.assertEqual(update.call_count, 2)
                self.assertEqual(pending_value(updated), expected_value)

            with self.subTest(editor=label, phase="reentry"):
                updater = getattr(controller_core_module, updater_name)
                with (
                    mock.patch.object(
                        terminal_module.InteractivePickerInput,
                        "read_picker",
                        autospec=True,
                        side_effect=(commit(second), cancel),
                    ) as picker,
                    mock.patch.object(
                        controller_core_module,
                        updater_name,
                        wraps=updater,
                    ) as update,
                ):
                    reentered, whole_cancel = editor(updated)
                self.assertFalse(whole_cancel)
                self.assertEqual(picker.call_count, 2)
                self.assertEqual(update.call_count, 1)
                self.assertEqual(reentered, updated)
            return updated

        with mock.patch.object(
            controller_core_module,
            "select_conversation_storage",
        ) as select_storage:
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
            with mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(
                    commit(persona_values[0]),
                    commit(persona_values[0]),
                ),
            ) as picker, mock.patch.object(
                controller_core_module,
                "update_persona",
                wraps=controller_core_module.update_persona,
            ) as update:
                persona_state, whole_cancel = controller._edit_settings_persona(
                    state
                )
            self.assertFalse(whole_cancel)
            self.assertEqual(picker.call_count, 2)
            self.assertEqual(update.call_count, 1)
            with mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(commit(persona_values[0]), cancel),
            ) as picker, mock.patch.object(
                controller_core_module,
                "update_persona",
                wraps=controller_core_module.update_persona,
            ) as update:
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
                state.pending.agent_configs[
                    PLOT_ARCHITECT
                ].codex_reasoning_effort,
                "high",
            )

            with mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=(commit("02"), commit("3"), commit("03")),
            ) as prompt, mock.patch.object(
                controller_core_module,
                "update_max_debate_rounds",
                wraps=controller_core_module.update_max_debate_rounds,
            ) as update:
                state, whole_cancel = controller._edit_settings_rounds(state)
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 2)
            self.assertEqual(state.pending.system_defaults.max_debate_rounds, 3)

            with mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=(commit("bad"), commit("03"), commit("3")),
            ) as prompt, mock.patch.object(
                controller_core_module,
                "update_max_debate_rounds",
                wraps=controller_core_module.update_max_debate_rounds,
            ) as update:
                reentered, whole_cancel = controller._edit_settings_rounds(state)
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(reentered, state)

            with mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=(commit("custom-a"), commit(""), commit("")),
            ) as prompt, mock.patch.object(
                controller_core_module,
                "update_agent_codex_model",
                wraps=controller_core_module.update_agent_codex_model,
            ) as update:
                state, whole_cancel = controller._edit_settings_custom_model(
                    state,
                    PLOT_ARCHITECT,
                    "codex",
                )
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 2)
            self.assertIsNone(
                state.pending.agent_configs[PLOT_ARCHITECT].codex_model
            )

            with mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=(commit("-unsafe"), commit(""), commit("")),
            ) as prompt, mock.patch.object(
                controller_core_module,
                "update_agent_codex_model",
                wraps=controller_core_module.update_agent_codex_model,
            ) as update:
                reentered, whole_cancel = controller._edit_settings_custom_model(
                    state,
                    PLOT_ARCHITECT,
                    "codex",
                )
            self.assertFalse(whole_cancel)
            self.assertEqual(prompt.call_count, 3)
            self.assertEqual(update.call_count, 2)
            self.assertEqual(reentered, state)
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
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
        )
        commit = lambda value: terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text=value,
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
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
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=picker_outcomes,
        ) as picker:
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
            tuple(
                calls[index].kwargs["highlighted_id"]
                for index in (1, 3, 5, 7, 9)
            ),
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
        commit_gpt_b = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="settings:model:1",
        )
        commit_gpt_a = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="settings:model:0",
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=(commit_gpt_b, commit_gpt_a, cancel),
        ) as picker:
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
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            return_value=cancel,
        ) as picker:
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
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
        )
        state = settings_module.update_agent_codex_model(
            settings_module.load_settings_state(workspace),
            PLOT_ARCHITECT,
            "existing-custom",
        )
        choose_custom = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="settings:model:custom",
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(choose_custom, cancel),
            ) as picker,
            mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                return_value=cancel,
            ) as prompt,
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
        controller, _backend, callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
        )

        def commit(value: str) -> terminal_module.ControlOutcome:
            return terminal_module.ControlOutcome(
                terminal_module.ControlOutcomeKind.COMMIT,
                text=value,
            )

        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=(commit("settings:model:custom"), cancel),
            ) as picker,
            mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=(
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
        self.assert_value_save_guidance(
            prompt.call_args_list[0].args[1].instruction
        )
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
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
        )
        state = settings_module.update_agent_codex_reasoning_effort(
            settings_module.update_agent_codex_model(
                settings_module.load_settings_state(workspace),
                PLOT_ARCHITECT,
                "gpt-b",
            ),
            PLOT_ARCHITECT,
            "future_2",
        )
        choose_low = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="settings:effort:0",
        )
        choose_future = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="settings:effort:1",
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=(choose_low, choose_future, cancel),
        ) as picker:
            updated, whole_cancel = controller._edit_settings_agent_reasoning(
                state,
                PLOT_ARCHITECT,
            )

        self.assertFalse(whole_cancel)
        self.assertEqual(catalog.events, ["codex"])
        choices = picker.call_args_list[0].args[2]
        self.assert_value_save_guidance(
            picker.call_args_list[0].args[1].instruction
        )
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
        keep_current = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.COMMIT,
            text="settings:effort:current-compatibility",
        )
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=(keep_current, cancel),
        ) as picker:
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
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            provider_catalog=catalog,
        )
        cancel = terminal_module.ControlOutcome(
            terminal_module.ControlOutcomeKind.CANCEL
        )
        state = settings_module.load_settings_state(workspace)
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            return_value=cancel,
        ):
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
                controller, _backend, _callbacks = self.make_controller(
                    workspace,
                    provider_catalog=catalog,
                )
                state = settings_module.load_settings_state(workspace)
                cancel = terminal_module.ControlOutcome(
                    terminal_module.ControlOutcomeKind.CANCEL
                )
                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    side_effect=(cancel, cancel),
                ) as picker:
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
                controller, _backend, _callbacks = self.make_controller(
                    workspace,
                    provider_catalog=catalog,
                )
                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    return_value=terminal_module.ControlOutcome(
                        terminal_module.ControlOutcomeKind.CANCEL
                    ),
                ) as picker:
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
                controller, _backend, _callbacks = self.make_controller(
                    workspace,
                    provider_catalog=catalog,
                )
                state = settings_module.load_settings_state(workspace)
                outcome = terminal_module.ControlOutcome(kind)
                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    side_effect=(outcome, outcome),
                ):
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
            "agents": ("5",),
            "agent-settings": ("5", "2"),
            "provider": ("5", "2", "1"),
            "model": ("5", "2", "2"),
            "effort": ("5", "2", "3"),
            "custom-model": ("5", "2", "2", "3"),
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
            "6",
            "1",
            "/quit",
            terminal_width=120,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        original_prompt = terminal_module.InteractivePromptInput.read_prompt
        with (
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=original_picker,
            ) as picker,
            mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=original_prompt,
            ) as prompt,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertGreaterEqual(len(picker.call_args_list), 2)
        self.assertTrue(
            all(call.kwargs.get("transient") is True for call in picker.call_args_list)
        )
        self.assertEqual(len(prompt.call_args_list), 2)
        self.assertTrue(
            all(call.kwargs.get("transient") is True for call in prompt.call_args_list)
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
            mock.patch.object(
                terminal_module.InteractivePickerInput,
                "read_picker",
                autospec=True,
                side_effect=original_picker,
            ) as ordinary_picker,
            mock.patch.object(
                terminal_module.InteractivePromptInput,
                "read_prompt",
                autospec=True,
                side_effect=original_prompt,
            ) as ordinary_prompt,
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
            "6",
            "1",
            "/quit",
            terminal_width=48,
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=original_picker,
        ) as picker:
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
        confirmation_prompt = next(
            prompt for prompt in callbacks.prompts if "Save settings?" in prompt
        )
        self.assertIn("YES", confirmation_prompt)
        expected_record = f"{render_plain(expected, width=48).text}\n"
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("--- Settings [open]")
            ],
            [expected_record],
        )
        self.assertIn(
            "[saved] Settings saved.",
            "".join(callbacks.records),
        )
        self.assertEqual(
            settings_module.load_settings_state(workspace).pending.system_defaults.language,
            "EN",
        )

    def test_settings_save_and_cancel_confirmation_controls_are_call_local(
        self,
    ) -> None:
        cases = (
            (
                "save-no-then-clean-cancel",
                ("/settings", "6", "2", "7", "/quit"),
                1,
                False,
                0,
            ),
            (
                "save-fallback-cancel-then-clean-cancel",
                ("/settings", "6", "/cancel", "7", "/quit"),
                1,
                False,
                0,
            ),
            ("clean-explicit-cancel", ("/settings", "7", "/quit"), 0, False, 0),
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
                    "7",
                    "2",
                    "7",
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
                    "7",
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
                    "7",
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
                ("/settings", "6", KeyboardInterrupt(), "/quit"),
                1,
                False,
                0,
            ),
            (
                "save-eof",
                ("/settings", "6", EOFError(), "/quit"),
                1,
                False,
                0,
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
                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    side_effect=original_picker,
                ) as picker:
                    self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(_tree_snapshot(workspace.root), before)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    rendered.count(
                        "[cancelled] Pending changes discarded.\n"
                    ),
                    discard_count,
                )
                confirmation_prompts = [
                    prompt
                    for prompt in callbacks.prompts
                    if "Save settings?" in prompt or "Cancel settings?" in prompt
                ]
                self.assertEqual(len(confirmation_prompts), confirmation_count)
                if discard_count:
                    dirty_settings_calls = [
                        call
                        for call in picker.call_args_list
                        if call.args[1].name == "Settings"
                        and next(
                            choice
                            for choice in call.args[2]
                            if choice.identity == "cancel"
                        ).description
                        == "Pending changes will be discarded."
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
            "5",
            "2",
            "2",
            "3",
            "/cancel",
            "/cancel",
            "/cancel",
            "5",
            "2",
            "3",
            "3",
            "/cancel",
            "/cancel",
            "/cancel",
            "5",
            "2",
            "2",
            "4",
            "/cancel",
            "\x1b",
            "/cancel",
            "/cancel",
            "/cancel",
            "6",
            "1",
            "/quit",
            provider_catalog=catalog,
            terminal_width=48,
        )
        real_select = controller_core_module.select_conversation_storage

        def select_after_isolation(*args, **kwargs):
            self.assertEqual(_tree_snapshot(workspace.root), before)
            return real_select(*args, **kwargs)

        with (
            mock.patch.object(
                controller_core_module,
                "select_conversation_storage",
                side_effect=select_after_isolation,
            ) as select,
            mock.patch.object(subprocess, "Popen") as popen,
            mock.patch.object(subprocess, "run") as run,
            mock.patch.object(socket, "create_connection") as connect,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
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
                "[saved]Settingssaved.Changesapplytothenextshell."
            ),
            1,
        )
        self.assertEqual(catalog.events, ["codex", "codex", "codex"])
        compact_prompts = "".join("".join(prompt.split()) for prompt in callbacks.prompts)
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
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "5",
            "2",
            "1",
            "2",
            "/cancel",
            "/cancel",
            "/cancel",
            "5",
            "2",
            "2",
            "4",
            "-unsafe",
            "agy-model",
            "\x1b",
            "/cancel",
            "/cancel",
            "/cancel",
            "5",
            "2",
            "3",
            "4",
            "/cancel",
            "/cancel",
            "/cancel",
            "6",
            "1",
            "/quit",
            provider_catalog=catalog,
            terminal_width=160,
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

        agent = settings_module.load_settings_state(
            workspace
        ).pending.agent_configs[PLOT_ARCHITECT]
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
            if "Effort [Plot & Structure Architect · Antigravity]" in prompt
        )
        self.assertIn("[available] low", effort_prompt)
        self.assertIn("[available] medium", effort_prompt)
        self.assertIn("[available] high", effort_prompt)
        self.assertNotIn("minimal", effort_prompt)
        self.assertNotIn("xhigh", effort_prompt)
        rendered = "".join(callbacks.records)
        self.assertIn(
            "[validation] Antigravity model must be safe nonempty text, "
            "or blank to inherit.\n",
            rendered,
        )
        self.assertIn("Antigravity model", rendered)
        self.assertIn("[saved] Settings saved", rendered)
        self.assertEqual(catalog.events, ["antigravity", "antigravity"])

    def test_nested_return_but_interrupt_and_eof_cancel_all(self) -> None:
        scenarios = (
            (
                (
                    "/settings",
                    "5",
                    "2",
                    "2",
                    "/cancel",
                    "/cancel",
                    "/cancel",
                    "7",
                    "1",
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
                    any("Choose a setting to edit" in prompt for prompt in callbacks.prompts)
                )
                self.assertEqual(
                    rendered.count(
                        "[cancelled] Pending changes discarded.\n"
                    ),
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
            "6",
            "1",
            "/quit",
            terminal_width=160,
        )
        bundle = controller._conversation_storage_services
        self.assertIsNotNone(bundle)
        self.assertIs(bundle.backend, ConversationStorageBackend.FILESYSTEM)
        with mock.patch.object(
            ConversationStorageProvisioner,
            "install",
            autospec=True,
            side_effect=AssertionError("a real installer must not execute"),
        ) as install:
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
            "Changes apply to the next shell",
            "".join(callbacks.records),
        )

    def test_persona_catalog_race_reports_conflict_and_preserves_config(self) -> None:
        workspace = self.make_workspace()
        system_before = workspace.path("config/system_defaults.json").read_bytes()
        agents_before = workspace.path("config/agent_configs.json").read_bytes()
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/settings",
            "1",
            "2",
            "/cancel",
            "6",
            "1",
            "7",
            "1",
            "/quit",
            terminal_width=160,
        )
        real_select = controller_core_module.select_conversation_storage

        def race_persona_catalog(*args, **kwargs):
            workspace.write_text(
                "config/persona_templates/alternative_templates/raced.md",
                "raced persona",
            )
            self.assertTrue(callable(kwargs.get("precommit_guard")))
            return real_select(*args, **kwargs)

        with mock.patch.object(
            controller_core_module,
            "select_conversation_storage",
            side_effect=race_persona_catalog,
        ) as select:
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

    def test_each_selection_save_failure_retains_candidate_and_redacts_details(self) -> None:
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
                controller, _backend, callbacks = self.make_controller(
                    workspace,
                    "/settings",
                    "6",
                    "1",
                    "7",
                    "1",
                    "/quit",
                    terminal_width=160,
                )
                with mock.patch.object(
                    controller_core_module,
                    "select_conversation_storage",
                    return_value=ConversationStorageSelectionReport.failed(
                        failure,
                        desired_backend="filesystem",
                    ),
                ) as select:
                    self.assertEqual(controller.run_session_lobby(), 0)
                select.assert_called_once()
                self.assertEqual(_tree_snapshot(workspace.root), before)
                rendered = "".join(callbacks.records)
                self.assertEqual(
                    rendered.count(f"[conversation storage] {message}\n"),
                    1,
                )
                self.assertNotIn("settings validation failed", rendered)
                self.assertEqual(
                    rendered.count(
                        "[cancelled] Pending changes discarded.\n"
                    ),
                    0,
                )


class ControllerSearchAndTranscriptTests(ControllerTestCase):
    """Verify provider-free memory commands and attachment-scoped wiring."""

    def test_one_explicit_bundle_identity_reaches_controller_runtime_and_memory(
        self,
    ) -> None:
        workspace = self.make_workspace()
        self.seed_session(
            workspace,
            "session",
            durable_state=SessionDurableState.READY,
        )
        bundle = compose_filesystem_conversation_storage(workspace)
        metadata = bundle.catalog.create("session", "Bundle identity")
        save_default_session(workspace, "session")
        save_default_conversation(
            workspace,
            "session",
            metadata.conversation_id,
        )
        controller, _backend, _callbacks = self.make_controller(
            workspace,
            "/search identity",
            "/quit",
            conversation_storage_services=bundle,
        )

        with mock.patch.object(
            controller_core_module,
            "ConversationMemoryService",
            wraps=controller_core_module.ConversationMemoryService,
        ) as memory_factory:
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertIs(controller._conversation_storage_services, bundle)
        self.assertIs(controller._runtime.conversation_storage_services, bundle)
        self.assertIs(controller._conversation_catalog, bundle.catalog)
        self.assertIs(controller._conversation_event_store, bundle.event_store)
        memory_factory.assert_called_once()
        self.assertIs(
            memory_factory.call_args.kwargs["conversation_storage_services"],
            bundle,
        )

    def test_bundle_and_legacy_catalog_injection_conflict_before_runtime_use(
        self,
    ) -> None:
        workspace = self.make_workspace()
        bundle = compose_filesystem_conversation_storage(workspace)
        backend = self.make_backend(workspace)
        callbacks = ScriptedTerminalCallbacks("/quit")
        terminal = InteractiveTerminal(callbacks.read_line, callbacks.write_text)
        with self.assertRaises(TypeError):
            InteractiveController(
                workspace,
                load_cli_context(workspace),
                backend,
                terminal,
                conversation_catalog=bundle.catalog,
                conversation_storage_services=bundle,
            )

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
        self.seed_session(workspace, "session", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "session")
        if selected_id is not None:
            save_default_conversation(workspace, "session", selected_id)
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

        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                controller_core_module,
                "ConversationMemoryService",
                return_value=memory,
            ) as memory_factory,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
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
        request = terminal_module.Record(
            "context",
            title="Search request",
            fields=(
                terminal_module.Field("Query", "council"),
                terminal_module.Field("Scope", "active"),
            ),
            state=terminal_module.ViewState.COMPLETED,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.title == "Search request"
            ],
            [request],
        )
        search_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "Search results"
        ]
        expected_views = [
            terminal_module.ListView(
                "Search results",
                (
                    terminal_module.Record(
                        f"search {exact.conversation_id[:12]}",
                        fields=(
                            terminal_module.Field("Status", "active"),
                            terminal_module.Field("Match", "exact_subject"),
                            terminal_module.Field("Subject", "Council"),
                            terminal_module.Field(
                                "Citation",
                                f"conversation {exact.conversation_id[:12]}",
                            ),
                            terminal_module.Field("Snippet", "council"),
                        ),
                        state=terminal_module.ViewState.COMPLETED,
                    ),
                ),
                total_count=2,
                position=1,
                state=terminal_module.ViewState.COMPLETED,
            ),
            terminal_module.ListView(
                "Search results",
                (
                    terminal_module.Record(
                        f"search {prefix.conversation_id[:12]}",
                        fields=(
                            terminal_module.Field("Status", "active"),
                            terminal_module.Field("Match", "subject_prefix"),
                            terminal_module.Field("Subject", "Council vessel"),
                            terminal_module.Field(
                                "Citation",
                                f"conversation {prefix.conversation_id[:12]}",
                            ),
                            terminal_module.Field("Snippet", "council vessel"),
                        ),
                        state=terminal_module.ViewState.COMPLETED,
                    ),
                ),
                total_count=2,
                position=2,
                state=terminal_module.ViewState.COMPLETED,
            ),
        ]
        self.assertEqual(search_views, expected_views)
        self.assertLess(components.index(request), components.index(search_views[0]))
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("Search results (")
            ],
            [
                f"{render_plain(view, width=240).text}\n"
                for view in expected_views
            ],
        )
        self.assertLess(
            rendered.index("Search results (1/2)"),
            rendered.index("Search results (2/2)"),
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
        result_notices = [
            component
            for component in components
            if type(component) is terminal_module.Notice
            and component.label in {"result", "hint"}
        ]
        self.assertEqual(
            result_notices,
            [
                terminal_module.Notice(
                    "Search returned 2 of 2 matching conversations.",
                    label="result",
                    role=terminal_module.SemanticRole.SUCCESS,
                    state=terminal_module.ViewState.COMPLETED,
                ),
                terminal_module.Notice(
                    "Search was truncated by fixed scan or result limits; "
                    "refine the query.",
                    label="hint",
                    role=terminal_module.SemanticRole.INFO,
                    state=terminal_module.ViewState.COMPLETED,
                ),
            ],
        )
        self.assertFalse(
            any(
                component.state is terminal_module.ViewState.LOADING
                for component in (*search_views, *result_notices)
            )
        )
        self.assertFalse(
            workspace.path(_default_relative("session")).exists()
        )
        self.assertIsNone(controller._conversation_memory_service)

    def test_empty_search_has_one_empty_result_and_no_header_or_row(self) -> None:
        query = ConversationSearchQuery("missing")
        result = ConversationSearchResult(
            query,
            "missing",
            (),
            0,
            0,
            0,
            False,
            False,
        )
        memory = ScriptedConversationMemoryService(search_outcomes=(result,))
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_attached_controller(
                "/search missing",
                "/quit",
            )
        )
        before = _tree_snapshot(workspace.root)
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                controller_core_module,
                "ConversationMemoryService",
                return_value=memory,
            ),
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(memory.search_calls, [query])
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.title == "Search request"
            ],
            [
                terminal_module.Record(
                    "context",
                    title="Search request",
                    fields=(
                        terminal_module.Field("Query", "missing"),
                        terminal_module.Field("Scope", "active"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                )
            ],
        )
        self.assertFalse(
            any(
                type(component) is terminal_module.ListView
                and component.title == "Search results"
                for component in components
            )
        )
        expected = terminal_module.Notice(
            "Search returned 0 of 0 matching conversations.",
            label="result",
            role=terminal_module.SemanticRole.MUTED,
            state=terminal_module.ViewState.EMPTY,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Notice
                and component.label == "result"
            ],
            [expected],
        )
        self.assertEqual(
            callbacks.records.count(
                "[result] Search returned 0 of 0 matching conversations.\n"
            ),
            1,
        )

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
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                controller_core_module,
                "ConversationMemoryService",
                return_value=memory,
            ),
            mock.patch.object(controller_core_module.asyncio, "run") as async_run,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
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
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.title in {"Search request", "Transcript request"}
            ],
            [
                terminal_module.Record(
                    "context",
                    title="Search request",
                    fields=(
                        terminal_module.Field("Query", "council"),
                        terminal_module.Field("Scope", "active"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
                terminal_module.Record(
                    "context",
                    title="Search request",
                    fields=(
                        terminal_module.Field("Query", "valid"),
                        terminal_module.Field("Scope", "active"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
                terminal_module.Record(
                    "context",
                    title="Transcript request",
                    fields=(
                        terminal_module.Field(
                            "Conversation",
                            entry.conversation_id[:12],
                        ),
                        terminal_module.Field("Start", "1"),
                        terminal_module.Field("Count", "24"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
            ],
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.COMMAND_INVALID, width=240),
            rendered,
        )
        self.assertIn(
            "[cancelled] Search cancelled; attached state is unchanged.\n",
            rendered,
        )
        self.assertIn(
            "[cancelled] Transcript read cancelled; attached state is "
            "unchanged.\n",
            rendered,
        )
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID, width=240),
            rendered,
        )
        self.assertEqual(workspace.path(_CONTEXT_PATH).read_bytes(), context_before)
        self.assertFalse(
            workspace.path(_default_relative("session")).exists()
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
                original_lifecycle = backend.get_session_lifecycle
                lifecycle_calls = 0

                def interrupt_recovery_proof(
                    session_id: str,
                ) -> SessionLifecycleState:
                    """Allow canonical startup, then interrupt recovery proof."""

                    nonlocal lifecycle_calls
                    lifecycle_calls += 1
                    if lifecycle_calls <= 4:
                        return original_lifecycle(session_id)
                    raise KeyboardInterrupt

                with (
                    mock.patch.object(
                        controller_core_module,
                        "ConversationMemoryService",
                        return_value=memory,
                    ),
                    mock.patch.object(
                        backend,
                        "get_session_lifecycle",
                        side_effect=interrupt_recovery_proof,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        wraps=backend.release_session_access,
                    ) as detach,
                    mock.patch.object(
                        controller_core_module.asyncio,
                        "run",
                    ) as async_run,
                    mock.patch.object(
                        controller._runtime,
                        "converse",
                    ) as converse,
                    mock.patch.object(
                        controller_core_module,
                        "save_default_session",
                    ) as persist_session,
                    mock.patch.object(
                        controller_core_module,
                        "save_default_conversation",
                    ) as persist_conversation,
                    mock.patch.object(
                        controller._conversation_event_store,
                        "append",
                    ) as append,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                self.assertEqual(lifecycle_calls, 5)
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
                self.assertIsNone(controller._focused_conversation)
                self.assertIsNone(controller._conversation_memory_service)
                self.assertFalse(
                    workspace.path(_access_relative("session")).exists()
                )
                self.assertFalse(
                    workspace.path(_default_relative("session")).exists()
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
            "get_session_lifecycle",
            side_effect=SystemExit(17),
        ) as summary:
            with self.assertRaises(SystemExit) as raised:
                controller._attachment_is_safely_retained("session")

        self.assertEqual(raised.exception.code, 17)
        summary.assert_called_once_with("session")

    def test_fresh_transcript_pages_accept_complete_and_bounded_shapes(
        self,
    ) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Subject")
        raw_reply = '{"answer":"界","items":[1, true]}'
        first = _stored_transcript_event(
            entry.conversation_id,
            1,
            content=raw_reply,
            kind=ConversationEventKind.AGENT_REPLY,
            sender="lead_orchestrator",
            recipient="user",
            turn_id="turn_1",
        )
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

        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                controller_core_module,
                "ConversationMemoryService",
                return_value=memory,
            ),
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
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
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.title == "Transcript request"
            ],
            [
                terminal_module.Record(
                    "context",
                    title="Transcript request",
                    fields=(
                        terminal_module.Field("Conversation", handle),
                        terminal_module.Field("Start", start),
                        terminal_module.Field("Count", count),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                )
                for start, count in (
                    ("1", "24"),
                    ("3", "24"),
                    ("1", "1"),
                    ("1", "24"),
                    ("1", "24"),
                )
            ],
        )
        rendered = "".join(callbacks.records)
        transcript_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "Transcript"
        ]
        self.assertEqual(
            [
                (view.position, view.total_count, len(view.items))
                for view in transcript_views
            ],
            [
                (1, 1, 1),
                (1, 1, 1),
                (1, 2, 1),
                (2, 2, 1),
            ],
        )
        self.assertTrue(
            all(
                type(view.items[0]) is terminal_module.Record
                and view.items[0].state is terminal_module.ViewState.COMPLETED
                and view.state is terminal_module.ViewState.COMPLETED
                for view in transcript_views
            )
        )
        agent_records = [
            item
            for view in transcript_views
            for item in view.items
            if type(item) is terminal_module.Record
            and item.role is terminal_module.SemanticRole.AGENT
        ]
        self.assertTrue(agent_records)
        self.assertEqual(
            agent_records[0].content,
            '{\n'
            '  "answer": "界",\n'
            '  "items": [\n'
            '    1,\n'
            '    true\n'
            '  ]\n'
            '}',
        )
        transcript_notices = [
            component
            for component in components
            if type(component) is terminal_module.Notice
            and component.label in {"result", "hint"}
            and (
                component.text.startswith("Transcript page returned")
                or component.text.startswith("Continue with /transcript")
            )
        ]
        self.assertEqual(
            [
                (component.label, component.role, component.state)
                for component in transcript_notices
            ],
            [
                (
                    "result",
                    terminal_module.SemanticRole.MUTED,
                    terminal_module.ViewState.EMPTY,
                ),
                (
                    "result",
                    terminal_module.SemanticRole.MUTED,
                    terminal_module.ViewState.EMPTY,
                ),
                (
                    "result",
                    terminal_module.SemanticRole.SUCCESS,
                    terminal_module.ViewState.COMPLETED,
                ),
                (
                    "hint",
                    terminal_module.SemanticRole.INFO,
                    terminal_module.ViewState.COMPLETED,
                ),
                (
                    "result",
                    terminal_module.SemanticRole.SUCCESS,
                    terminal_module.ViewState.COMPLETED,
                ),
                (
                    "hint",
                    terminal_module.SemanticRole.INFO,
                    terminal_module.ViewState.COMPLETED,
                ),
                (
                    "result",
                    terminal_module.SemanticRole.SUCCESS,
                    terminal_module.ViewState.COMPLETED,
                ),
            ],
        )
        self.assertFalse(
            any(
                component.state is terminal_module.ViewState.LOADING
                for component in (*transcript_views, *transcript_notices)
            )
        )
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

    def test_history_pretty_prints_agent_reply_without_rewriting_stored_bytes(
        self,
    ) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Subject")
        raw_reply = '{"z":"界","a":[1,true]}'
        stored_user = _stored_transcript_event(
            entry.conversation_id,
            1,
            content="question",
        )
        stored_event = _stored_transcript_event(
            entry.conversation_id,
            2,
            content=raw_reply,
            kind=ConversationEventKind.AGENT_REPLY,
            sender="lead_orchestrator",
            recipient="user",
            turn_id="turn_1",
        )
        _workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_attached_controller(
                entries=(entry,),
                selected_id=entry.conversation_id,
            )
        )
        controller._attached_session_id = "session"
        controller._focused_conversation = entry
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with (
            mock.patch.object(
                controller._conversation_event_store,
                "read",
                return_value=(stored_user, stored_event),
            ) as read,
            mock.patch.object(
                terminal_module.InteractiveSemanticOutput,
                "write_component",
                autospec=True,
                side_effect=capture_component,
            ),
        ):
            controller._write_focused_conversation_history(
                InteractiveAction(ActionKind.HISTORY)
            )

        read.assert_called_once_with("session", entry.conversation_id)
        history_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "History"
        ]
        self.assertEqual(len(history_views), 2)
        records = [
            view.items[0]
            for view in history_views
            if type(view.items[0]) is terminal_module.Record
            and view.items[0].role is terminal_module.SemanticRole.AGENT
        ]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIs(type(record), terminal_module.Record)
        self.assertIs(record.role, terminal_module.SemanticRole.AGENT)
        self.assertEqual(
            record.content,
            '{\n'
            '  "z": "界",\n'
            '  "a": [\n'
            '    1,\n'
            '    true\n'
            '  ]\n'
            '}',
        )
        self.assertEqual(stored_event.content, raw_reply)
        self.assertIn('"z": "界"', "".join(callbacks.records))

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
                    controller_core_module,
                    "ConversationMemoryService",
                    return_value=memory,
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)
                rendered = "".join(callbacks.records)
                request_context = (
                    "[context] Transcript request [completed]\n"
                    f"  Conversation: {conversation_id[:12]}\n"
                    "  Start: 1\n"
                    "  Count: 24\n"
                )
                self.assertEqual(rendered.count(request_context), 1)
                self.assertEqual(
                    rendered.count("[error transcript_invalid]"),
                    1,
                )
                self.assertLess(
                    rendered.index(request_context),
                    rendered.index("[error transcript_invalid]"),
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
                f"/conversation resume {second.conversation_id}",
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
            controller_core_module,
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
                    self.seed_session(workspace, session_id, durable_state=SessionDurableState.READY)
                save_default_session(workspace, "source")
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

                original_detach = backend.release_session_access

                def record_detach(session_id: str):
                    """Record and delegate one concrete session detach."""

                    events.append(f"detach:{session_id}")
                    return original_detach(session_id)

                with (
                    mock.patch.object(
                        controller_core_module,
                        "ConversationMemoryService",
                        side_effect=build_memory_service,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
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
                        load_cli_context(workspace).default_session_id,
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
                        load_cli_context(workspace).default_session_id,
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
                "/session pause",
                "yes",
                observe_cleared_scope,
            )
        )
        holder["controller"] = controller
        with mock.patch.object(
            controller_core_module,
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

    def test_status_with_prior_run_emits_one_exact_framed_result_panel(
        self,
    ) -> None:
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation new Opening scene",
                "/status",
                "/quit",
            )
        )
        controller._last_started_debate_by_session["session"] = "debate_1"
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with mock.patch.object(
            terminal_module.InteractiveSemanticOutput,
            "write_component",
            autospec=True,
            side_effect=capture_component,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        result_panels = [
            component
            for component in components
            if type(component) is terminal_module.Panel
            and component.title == "Result"
        ]
        self.assertEqual(len(result_panels), 1)
        result = result_panels[0]
        self.assertIs(result.state, terminal_module.ViewState.COMPLETED)
        self.assertEqual(
            tuple(type(item) for item in result.items),
            (terminal_module.Field, terminal_module.Section),
        )
        expected_record = f"{render_plain(result, width=200).text}\n"
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("--- Result")
            ],
            [expected_record],
        )
        self.assertTrue(expected_record.endswith(f"{'-' * 200}\n"))
        self.assertFalse(workspace.path(_access_relative("session")).exists())

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
            load_default_conversation(workspace, "session"),
            catalog.entries[1].conversation_id,
        )
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("--- Conversation scope")
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
            "  Subject: Opening scene\n  Agent: lead\n",
            rendered,
        )
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
                    "release_session_access",
                    wraps=backend.release_session_access,
                ) as detach:
                    self.assertEqual(controller.run_session_lobby(), 0)
                detach.assert_called_once_with("session")
                self.assertEqual(catalog.entries, [])
                self.assertFalse(
                    workspace.path(_default_relative("session")).exists()
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
        self.assertFalse(workspace.path(_default_relative("session")).exists())
        self.assertIn(
            _error_record(InteractiveErrorCode.CONVERSATION_INVALID, width=200),
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
            _error_record(
                InteractiveErrorCode.CONVERSATION_CREATE_FAILED,
                width=200,
            ),
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
                controller_core_module,
                "save_default_conversation",
                side_effect=OSError("selection-secret"),
            ),
            mock.patch.object(
                backend,
                "get_session_lifecycle",
                wraps=backend.get_session_lifecycle,
            ) as summary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertGreaterEqual(summary.call_count, 2)
        self.assertEqual(len(catalog.entries), 1)
        self.assertFalse(workspace.path(_default_relative("session")).exists())
        rendered = "".join(callbacks.records)
        self.assertIn("was created but did not become the default", rendered)
        self.assertIn("[error workspace_invalid]", rendered)
        self.assertNotIn("selection-secret", rendered)
        self.assertIn("  Conversation: none\n  Agent: lead\n", rendered)

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
        selection_path = workspace.path(_default_relative("session"))
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with mock.patch.object(
            terminal_module.InteractiveSemanticOutput,
            "write_component",
            autospec=True,
            side_effect=capture_component,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.Record
                and component.title == "Conversation list request"
            ],
            [
                terminal_module.Record(
                    "context",
                    title="Conversation list request",
                    fields=(
                        terminal_module.Field("Query", "motivation"),
                        terminal_module.Field("Scope", "all"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
                terminal_module.Record(
                    "context",
                    title="Conversation list request",
                    fields=(
                        terminal_module.Field("Query", "opening"),
                        terminal_module.Field("Scope", "active"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                ),
            ],
        )
        self.assertEqual(
            callbacks.records.count(
                _conversation_scope_record(
                    "session",
                    _COLLIDING_A,
                    "Ｍara   Motivation",
                )
            ),
            1,
        )

        def conversation_view(
            handle: str,
            subject: str,
            *,
            archived: bool,
            focused: bool,
            position: int,
            total_count: int,
        ) -> terminal_module.ListView:
            return terminal_module.ListView(
                "Conversations",
                (
                    terminal_module.Record(
                        f"conversation {handle}",
                        fields=(
                            terminal_module.Field(
                                "Status",
                                "archived" if archived else "active",
                            ),
                            terminal_module.Field(
                                "Focus",
                                "focused" if focused else "not focused",
                            ),
                            terminal_module.Field("Subject", subject),
                        ),
                        role=(
                            terminal_module.SemanticRole.MUTED
                            if archived
                            else terminal_module.SemanticRole.DEFAULT
                        ),
                        state=terminal_module.ViewState.COMPLETED,
                    ),
                ),
                total_count=total_count,
                position=position,
                state=terminal_module.ViewState.COMPLETED,
            )

        expected_views = [
            conversation_view(
                _COLLIDING_A,
                "Ｍara   Motivation",
                archived=False,
                focused=True,
                position=1,
                total_count=2,
            ),
            conversation_view(
                "c_fedcba9876",
                "Opening Scene",
                archived=False,
                focused=False,
                position=2,
                total_count=2,
            ),
            conversation_view(
                _COLLIDING_A,
                "Ｍara   Motivation",
                archived=False,
                focused=True,
                position=1,
                total_count=2,
            ),
            conversation_view(
                _COLLIDING_B,
                "Archived motivation",
                archived=True,
                focused=False,
                position=2,
                total_count=2,
            ),
            conversation_view(
                "c_fedcba9876",
                "Opening Scene",
                archived=False,
                focused=False,
                position=1,
                total_count=1,
            ),
        ]
        conversation_views = [
            component
            for component in components
            if type(component) is terminal_module.ListView
            and component.title == "Conversations"
        ]
        self.assertEqual(conversation_views, expected_views)
        rows = [
            record
            for record in callbacks.records
            if record.startswith("Conversations (")
        ]
        self.assertEqual(
            rows,
            [
                f"{render_plain(view, width=200).text}\n"
                for view in expected_views
            ],
        )
        self.assertTrue(
            all(
                view.items[0].fields[1].state
                is terminal_module.ViewState.IDLE
                for view in conversation_views
            )
        )
        self.assertFalse(
            any(
                view.state is terminal_module.ViewState.LOADING
                for view in conversation_views
            )
        )
        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )

    def test_empty_conversation_list_is_one_explicit_semantic_callback(
        self,
    ) -> None:
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation list",
                "/quit",
            )
        )
        before = _tree_snapshot(workspace.root)
        components: list[object] = []
        real_write_component = terminal_module.InteractiveSemanticOutput.write_component

        def capture_component(output, component):
            components.append(component)
            return real_write_component(output, component)

        with mock.patch.object(
            terminal_module.InteractiveSemanticOutput,
            "write_component",
            autospec=True,
            side_effect=capture_component,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(_tree_snapshot(workspace.root), before)
        expected = terminal_module.ListView(
            "Conversations",
            (),
            total_count=0,
            empty_notice=terminal_module.Notice(
                "No conversations found.",
                label="conversation",
                role=terminal_module.SemanticRole.MUTED,
                state=terminal_module.ViewState.EMPTY,
            ),
            state=terminal_module.ViewState.EMPTY,
        )
        self.assertEqual(
            [
                component
                for component in components
                if type(component) is terminal_module.ListView
                and component.title == "Conversations"
            ],
            [expected],
        )
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("Conversations (")
            ],
            [f"{render_plain(expected, width=200).text}\n"],
        )

    def test_filtered_conversation_list_context_precedes_empty_and_error(
        self,
    ) -> None:
        cases = (
            ("empty", None, False),
            ("catalog error", OSError("catalog-secret"), True),
        )
        for label, failure, expects_error in cases:
            with self.subTest(case=label):
                workspace, _backend, catalog, controller, callbacks = (
                    self.prepared_controller(
                        "/conversation list --all  MISSING  ",
                        "/quit",
                    )
                )
                components: list[object] = []
                real_write_component = (
                    terminal_module.InteractiveSemanticOutput.write_component
                )

                def capture_component(output, component):
                    components.append(component)
                    return real_write_component(output, component)

                list_patch = (
                    mock.patch.object(catalog, "list", side_effect=failure)
                    if failure is not None
                    else mock.patch.object(catalog, "list", wraps=catalog.list)
                )
                with (
                    list_patch,
                    mock.patch.object(
                        terminal_module.InteractiveSemanticOutput,
                        "write_component",
                        autospec=True,
                        side_effect=capture_component,
                    ),
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)

                expected_context = terminal_module.Record(
                    "context",
                    title="Conversation list request",
                    fields=(
                        terminal_module.Field("Query", "missing"),
                        terminal_module.Field("Scope", "all"),
                    ),
                    state=terminal_module.ViewState.COMPLETED,
                )
                self.assertEqual(
                    [
                        component
                        for component in components
                        if type(component) is terminal_module.Record
                        and component.title == "Conversation list request"
                    ],
                    [expected_context],
                )
                rendered = "".join(callbacks.records)
                context_text = render_plain(expected_context, width=200).text
                self.assertIn(context_text, rendered)
                if expects_error:
                    self.assertNotIn("catalog-secret", rendered)
                    self.assertNotIn("No conversations found.", rendered)
                    self.assertLess(
                        rendered.index(context_text),
                        rendered.index("[error workspace_invalid]"),
                    )
                else:
                    self.assertIn("No conversations found.", rendered)
                    self.assertLess(
                        rendered.index(context_text),
                        rendered.index("No conversations found."),
                    )
                self.assertFalse(
                    workspace.path(_default_relative("session")).exists()
                )

    def test_main_conversation_resume_provider_is_live_and_freshly_validated(
        self,
    ) -> None:
        prior = _conversation_metadata(_COLLIDING_A, "Shared subject")
        archived = _conversation_metadata(
            _COLLIDING_B,
            "Archived subject",
            status=ConversationStatus.ARCHIVED,
        )
        target = _conversation_metadata(_UNIQUE_C, "Target subject")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                entries=(prior, archived, target),
                selected_id=prior.conversation_id,
            )
        )
        reads = 0
        captured: dict[str, object] = {}

        def commit_live_choice(
            _main_input: object,
            provider: Callable[[str, int], object],
            **_kwargs: object,
        ) -> terminal_module.TerminalAction:
            nonlocal reads
            reads += 1
            if reads == 1:
                captured["current"] = provider("/conversation resume", 20)
                save_default_conversation(
                    workspace,
                    "session",
                    target.conversation_id,
                )
                captured["current_precedence"] = provider(
                    "/conversation resume",
                    20,
                )
                controller._focused_conversation = None
                captured["default_precedence"] = provider(
                    "/conversation resume",
                    20,
                )
                captured["explicit"] = provider(
                    f"/conversation resume {target.conversation_id}",
                    20 + len(target.conversation_id) + 1,
                )
                captured["midline"] = provider("/conversation resume", 8)
                saved_entries = list(catalog.entries)
                catalog.entries.clear()
                captured["empty"] = provider("/conversation resume", 20)
                catalog.entries[:] = saved_entries
                with mock.patch.object(
                    catalog,
                    "list",
                    side_effect=OSError("snapshot-secret"),
                ):
                    captured["failure"] = provider(
                        "/conversation resume",
                        20,
                    )
                catalog.entries[-1] = replace(
                    catalog.entries[-1],
                    subject="Fresh subject",
                )
                picker = captured["default_precedence"]
                assert isinstance(picker, terminal_module.Picker)
                return terminal_module.TerminalAction(
                    terminal_module.TerminalActionKind.LINE,
                    text=picker.choices[1].identity,
                )
            return terminal_module.TerminalAction(
                terminal_module.TerminalActionKind.LINE,
                text="/quit",
            )

        with (
            mock.patch.object(
                terminal_module.InteractiveMainInput,
                "read_main",
                autospec=True,
                side_effect=commit_live_choice,
            ),
            mock.patch.object(
                controller_core_module,
                "parse_interactive_line",
                wraps=controller_core_module.parse_interactive_line,
            ) as parse,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        expected_choices = (
            terminal_module.Choice(
                f"/conversation resume {prior.conversation_id}",
                "Shared subject",
                description=f"Handle: {prior.conversation_id}",
            ),
            terminal_module.Choice(
                f"/conversation resume {target.conversation_id}",
                "Target subject",
                description=f"Handle: {target.conversation_id[:12]}",
            ),
        )
        current = captured["current"]
        self.assertEqual(
            current,
            terminal_module.Picker(
                terminal_module.Prompt(
                    "Resume conversation",
                    instruction="Choose an active conversation to resume.",
                ),
                expected_choices,
                highlighted_id=(
                    f"/conversation resume {prior.conversation_id}"
                ),
            ),
        )
        self.assertEqual(captured["current_precedence"], current)
        self.assertEqual(
            captured["default_precedence"],
            terminal_module.Picker(
                terminal_module.Prompt(
                    "Resume conversation",
                    instruction="Choose an active conversation to resume.",
                ),
                expected_choices,
                highlighted_id=(
                    f"/conversation resume {target.conversation_id}"
                ),
            ),
        )
        self.assertIsNone(captured["explicit"])
        self.assertIsNone(captured["midline"])
        self.assertEqual(
            captured["empty"],
            terminal_module.Picker(
                terminal_module.Prompt(
                    "Resume conversation",
                    instruction="Choose an active conversation to resume.",
                ),
                (),
            ),
        )
        self.assertIsNone(captured["failure"])
        self.assertEqual(
            parse.call_args_list.count(
                mock.call(
                    f"/conversation resume {target.conversation_id}"
                )
            ),
            1,
        )
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            target.conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertIn("Subject: Fresh subject", rendered)
        self.assertNotIn("snapshot-secret", rendered)

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
                f"/conversation resume {_UNIQUE_C[:11]}",
                "/conversation resume c_0123456789",
                "/conversation resume c_9999999999",
                f"/conversation resume {_COLLIDING_B}",
                f"/conversation resume {_COLLIDING_A}",
                f"/conversation resume {_UNIQUE_C[:12]}",
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
                self.assertIn(_error_record(code, width=200), rendered)
        self.assertIn(f"Current focus: {_COLLIDING_A} | Subject: First", rendered)
        self.assertIn("Current focus: c_fedcba9876 | Subject: Unique", rendered)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            _UNIQUE_C,
        )

    def test_omitted_use_picker_preserves_active_order_handles_and_current_highlight(
        self,
    ) -> None:
        entries = (
            _conversation_metadata(_COLLIDING_A, "Shared subject"),
            _conversation_metadata(
                _COLLIDING_B,
                "Archived subject",
                status=ConversationStatus.ARCHIVED,
            ),
            _conversation_metadata(_UNIQUE_C, "Shared subject"),
        )
        workspace: Workspace

        def change_saved_default(_prompt: str) -> str:
            save_default_conversation(workspace, "session", _UNIQUE_C)
            return "/conversation resume"

        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                change_saved_default,
                "2",
                "/quit",
                entries=entries,
                selected_id=_COLLIDING_A,
            )
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=original_picker,
        ) as picker:
            self.assertEqual(controller.run_session_lobby(), 0)

        picker.assert_called_once()
        picker_call = picker.call_args
        self.assertEqual(
            picker_call.args[1],
            terminal_module.Prompt(
                "Resume conversation",
                instruction="Choose an active conversation to resume.",
            ),
        )
        self.assertEqual(
            picker_call.args[2],
            (
                terminal_module.Choice(
                    _COLLIDING_A,
                    "Shared subject",
                    description=f"Handle: {_COLLIDING_A}",
                ),
                terminal_module.Choice(
                    _UNIQUE_C,
                    "Shared subject",
                    description=f"Handle: {_UNIQUE_C[:12]}",
                ),
            ),
        )
        self.assertEqual(
            picker_call.kwargs["highlighted_id"],
            _COLLIDING_A,
        )
        picker_prompt = next(
            prompt for prompt in callbacks.prompts if "Resume conversation" in prompt
        )
        self.assertLess(
            picker_prompt.index(f"Handle: {_COLLIDING_A}"),
            picker_prompt.index(f"Handle: {_UNIQUE_C[:12]}"),
        )
        self.assertIn("1. [highlighted] Shared subject", picker_prompt)
        self.assertIn("2. [available] Shared subject", picker_prompt)
        self.assertNotIn("Archived subject", picker_prompt)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            _UNIQUE_C,
        )
        self.assertIn(
            "Current focus: c_fedcba9876 | Subject: Shared subject",
            "".join(callbacks.records),
        )

    def test_omitted_use_blank_commits_saved_default_when_focus_is_absent(
        self,
    ) -> None:
        prior = _conversation_metadata(_COLLIDING_A, "Prior")
        saved = _conversation_metadata(_UNIQUE_C, "Saved")
        controller: InteractiveController

        def clear_current_focus(_prompt: str) -> str:
            controller._focused_conversation = None
            return "/conversation resume"

        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                clear_current_focus,
                "",
                "/quit",
                entries=(prior, saved),
                selected_id=saved.conversation_id,
            )
        )
        original_picker = terminal_module.InteractivePickerInput.read_picker
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
            autospec=True,
            side_effect=original_picker,
        ) as picker:
            self.assertEqual(controller.run_session_lobby(), 0)

        picker.assert_called_once()
        self.assertEqual(
            picker.call_args.kwargs["highlighted_id"],
            saved.conversation_id,
        )
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            saved.conversation_id,
        )
        self.assertIn(
            "Current focus: c_fedcba9876 | Subject: Saved",
            "".join(callbacks.records),
        )

    def test_omitted_use_cancel_interrupt_and_eof_are_nonmutating(self) -> None:
        cases = (
            ("cancel", "/cancel", False),
            ("interrupt", KeyboardInterrupt(), False),
            ("eof", EOFError(), True),
        )
        for label, picker_input, exits_from_picker in cases:
            with self.subTest(case=label):
                selected = _conversation_metadata(_COLLIDING_A, "Selected")
                other = _conversation_metadata(_UNIQUE_C, "Other")
                reads = (
                    ("/conversation resume", picker_input)
                    if exits_from_picker
                    else ("/conversation resume", picker_input, "/quit")
                )
                workspace, backend, catalog, controller, callbacks = (
                    self.prepared_controller(
                        *reads,
                        entries=(selected, other),
                        selected_id=selected.conversation_id,
                    )
                )
                before = _tree_snapshot(workspace.root)
                with mock.patch.object(
                    backend,
                    "release_session_access",
                    wraps=backend.release_session_access,
                ) as detach:
                    self.assertEqual(controller.run_session_lobby(), 0)

                detach.assert_called_once_with("session")
                self.assertEqual(_tree_snapshot(workspace.root), before)
                self.assertEqual(
                    load_default_conversation(workspace, "session"),
                    selected.conversation_id,
                )
                self.assertFalse(
                    any(
                        event.startswith(("create:", "rename:", "archive:"))
                        for event in catalog.events
                    )
                )
                rendered = "".join(callbacks.records)
                if exits_from_picker:
                    self.assertNotIn("Conversation focus cancelled.", rendered)
                    self.assertEqual(
                        callbacks.prompts[-1].splitlines()[-1],
                        "Resume conversation > ",
                    )
                else:
                    self.assertIn(
                        "[cancelled] Conversation focus cancelled.",
                        rendered,
                    )

    def test_omitted_use_empty_active_catalog_does_not_open_picker(self) -> None:
        archived = _conversation_metadata(
            _COLLIDING_B,
            "Archived",
            status=ConversationStatus.ARCHIVED,
        )
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation resume",
                "/quit",
                entries=(archived,),
            )
        )
        before = _tree_snapshot(workspace.root)
        with mock.patch.object(
            terminal_module.InteractivePickerInput,
            "read_picker",
        ) as picker:
            self.assertEqual(controller.run_session_lobby(), 0)

        picker.assert_not_called()
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertIn(
            "[conversation] No active conversations found.",
            "".join(callbacks.records),
        )
        self.assertEqual(
            [prompt for prompt in callbacks.prompts if prompt.startswith("story[")],
            ["story[session:lead]> ", "story[session:lead]> "],
        )

    def test_omitted_use_freshly_resolves_picker_commit_before_selection(
        self,
    ) -> None:
        cases = (
            ("missing", InteractiveErrorCode.CONVERSATION_NOT_FOUND),
            ("archived", InteractiveErrorCode.CONVERSATION_ARCHIVED),
            ("refreshed", None),
        )
        for change, expected_error in cases:
            with self.subTest(change=change):
                prior = _conversation_metadata(_COLLIDING_A, "Prior")
                target = _conversation_metadata(_UNIQUE_C, "Snapshot subject")
                workspace, _backend, catalog, controller, callbacks = (
                    self.prepared_controller(
                        "/conversation resume",
                        "/quit",
                        entries=(prior, target),
                        selected_id=prior.conversation_id,
                    )
                )
                selection_path = workspace.path(_default_relative("session"))
                selection_before = (
                    selection_path.read_bytes(),
                    selection_path.stat().st_mtime_ns,
                )

                def mutate_after_snapshot(
                    _picker_input: object,
                    _prompt: object,
                    _choices: object,
                    **_kwargs: object,
                ) -> terminal_module.ControlOutcome:
                    if change == "missing":
                        catalog.entries.pop()
                    elif change == "archived":
                        catalog.entries[-1] = replace(
                            catalog.entries[-1],
                            status=ConversationStatus.ARCHIVED,
                        )
                    else:
                        catalog.entries[-1] = replace(
                            catalog.entries[-1],
                            subject="Fresh subject",
                        )
                    return terminal_module.ControlOutcome(
                        terminal_module.ControlOutcomeKind.COMMIT,
                        text=target.conversation_id,
                    )

                with mock.patch.object(
                    terminal_module.InteractivePickerInput,
                    "read_picker",
                    autospec=True,
                    side_effect=mutate_after_snapshot,
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)

                rendered = "".join(callbacks.records)
                if expected_error is None:
                    self.assertEqual(
                        load_default_conversation(workspace, "session"),
                        target.conversation_id,
                    )
                    self.assertIn("Subject: Fresh subject", rendered)
                    self.assertNotIn(
                        "Current focus: c_fedcba9876 | Subject: Snapshot subject",
                        rendered,
                    )
                else:
                    self.assertEqual(
                        (
                            selection_path.read_bytes(),
                            selection_path.stat().st_mtime_ns,
                        ),
                        selection_before,
                    )
                    self.assertIn(
                        _error_record(expected_error, width=200),
                        rendered,
                    )
                    self.assertEqual(
                        callbacks.records.count(
                            _conversation_scope_record(
                                "session",
                                prior.conversation_id[:12],
                                "Prior",
                            )
                        ),
                        1,
                    )

    def test_omitted_use_selection_write_failure_preserves_prior_choice(
        self,
    ) -> None:
        prior = _conversation_metadata(_COLLIDING_A, "Prior")
        target = _conversation_metadata(_UNIQUE_C, "Target")
        workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                "/conversation resume",
                "2",
                "/quit",
                entries=(prior, target),
                selected_id=prior.conversation_id,
            )
        )
        selection_path = workspace.path(_default_relative("session"))
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )
        with mock.patch.object(
            controller_core_module,
            "save_default_conversation",
            side_effect=OSError("selection-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            (selection_path.read_bytes(), selection_path.stat().st_mtime_ns),
            selection_before,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID, width=200),
            rendered,
        )
        self.assertEqual(
            callbacks.records.count(
                _conversation_scope_record(
                    "session",
                    prior.conversation_id[:12],
                    "Prior",
                )
            ),
            1,
        )
        self.assertNotIn("selection-secret", rendered)

    def test_use_selection_write_failure_preserves_prior_effective_choice(
        self,
    ) -> None:
        prior = _conversation_metadata(_COLLIDING_A, "Prior")
        target = _conversation_metadata(_UNIQUE_C, "Target")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                f"/conversation resume {target.conversation_id}",
                "/quit",
                entries=(prior, target),
                selected_id=prior.conversation_id,
            )
        )
        selection_path = workspace.path(_default_relative("session"))
        selection_before = (
            selection_path.read_bytes(),
            selection_path.stat().st_mtime_ns,
        )
        with mock.patch.object(
            controller_core_module,
            "save_default_conversation",
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
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID, width=200),
            rendered,
        )
        self.assertEqual(
            callbacks.records.count(
                _conversation_scope_record(
                    "session",
                    prior.conversation_id[:12],
                    "Prior",
                )
            ),
            1,
        )
        self.assertNotIn("selection-secret", rendered)

    def test_external_rename_archive_or_delete_is_detected_at_next_prompt(
        self,
    ) -> None:
        cases = (
            ("renamed", None),
            ("archived", InteractiveErrorCode.CONVERSATION_ARCHIVED),
            ("deleted", InteractiveErrorCode.CONVERSATION_NOT_FOUND),
        )
        for change, expected_code in cases:
            with self.subTest(change=change):
                selected = _conversation_metadata(_UNIQUE_C, "Selected")
                catalog = MemoryConversationCatalog((selected,))

                def change_catalog(_prompt: str) -> str:
                    if change == "deleted":
                        catalog.entries.clear()
                    elif change == "renamed":
                        catalog.entries[0] = replace(
                            catalog.entries[0],
                            subject="Externally revised",
                        )
                    else:
                        catalog.entries[0] = replace(
                            catalog.entries[0],
                            status=ConversationStatus.ARCHIVED,
                        )
                    return "/help"

                workspace, _backend, _catalog, controller, callbacks = (
                    self.prepared_controller(
                        change_catalog,
                        "/quit",
                        catalog=catalog,
                        selected_id=selected.conversation_id,
                    )
                )
                selection_path = workspace.path(_default_relative("session"))
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
                expected_scopes = [
                    _conversation_scope_record(
                        "session",
                        selected.conversation_id[:12],
                        "Selected",
                    )
                ]
                if expected_code is None:
                    expected_scopes.append(
                        _conversation_scope_record(
                            "session",
                            selected.conversation_id[:12],
                            "Externally revised",
                        )
                    )
                    self.assertNotIn("[error conversation_", rendered)
                else:
                    expected_scopes.append(
                        _conversation_scope_record("session", None, None)
                    )
                    self.assertIn(
                        _error_record(expected_code, width=200),
                        rendered,
                    )
                self.assertEqual(
                    [
                        record
                        for record in callbacks.records
                        if record.startswith("--- Conversation scope")
                    ],
                    expected_scopes,
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
            controller_core_module,
            "save_default_conversation",
            side_effect=OSError("selection-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertIs(catalog.entries[0].status, ConversationStatus.ACTIVE)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            selected.conversation_id,
        )
        self.assertIn(
            f"unarchive:session:{selected.conversation_id}",
            catalog.events,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.WORKSPACE_INVALID, width=200),
            rendered,
        )
        self.assertIn("original conversation remains focused", rendered)
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
                controller_core_module,
                "save_default_conversation",
                side_effect=OSError("selection-secret"),
            ),
            mock.patch.object(
                catalog,
                "unarchive",
                side_effect=OSError("compensation-secret"),
            ),
            mock.patch.object(
                backend,
                "release_session_access",
                wraps=backend.release_session_access,
            ) as detach,
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        detach.assert_not_called()
        self.assertIs(catalog.entries[0].status, ConversationStatus.ARCHIVED)
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            selected.conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("selection-secret", rendered)
        self.assertNotIn("compensation-secret", rendered)
        self.assertFalse(workspace.path(_access_relative("session")).exists())

        restarted, _backend, restarted_callbacks = self.make_controller(
            workspace,
            "/quit",
            conversation_catalog=catalog,
            terminal_width=200,
        )
        self.assertEqual(restarted.run_session_lobby(), 0)
        self.assertIn(
            _conversation_scope_record("session", None, None),
            restarted_callbacks.records,
        )
        self.assertEqual(
            load_default_conversation(workspace, "session"),
            selected.conversation_id,
        )

    def test_command_storage_failure_requires_public_held_access_proof(
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
                original_lifecycle = backend.get_session_lifecycle
                lifecycle_calls = 0

                def summarize(
                    session_id: str,
                ) -> SessionLifecycleState:
                    nonlocal lifecycle_calls
                    lifecycle_calls += 1
                    if retained or lifecycle_calls != 5:
                        return original_lifecycle(session_id)
                    return _lifecycle(
                        session_id,
                        durable_state=SessionDurableState.READY,
                        access_state=SessionAccessState.CLAIMABLE,
                    )

                with (
                    mock.patch.object(
                        catalog,
                        "list",
                        side_effect=OSError("catalog-secret"),
                    ),
                    mock.patch.object(
                        backend,
                        "get_session_lifecycle",
                        side_effect=summarize,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        wraps=backend.release_session_access,
                    ) as detach,
                ):
                    self.assertEqual(
                        controller.run_session_lobby(),
                        0 if retained else 2,
                    )

                self.assertEqual(lifecycle_calls, 6 if retained else 5)
                rendered = "".join(callbacks.records)
                self.assertNotIn("catalog-secret", rendered)
                if retained:
                    detach.assert_called_once_with("session")
                    self.assertIn(
                        _error_record(
                            InteractiveErrorCode.WORKSPACE_INVALID,
                            width=200,
                        ),
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
                    workspace.path(_access_relative("session")).exists()
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
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(
                        InteractiveErrorCode.INTERNAL_ERROR,
                        width=200,
                    ),
                    rendered,
                )
                self.assertNotIn("foreign-secret", rendered)
                self.assertFalse(
                    workspace.path(_access_relative("session")).exists()
                )

    def test_later_feature_allowlists_are_provider_and_artifact_free(
        self,
    ) -> None:
        conversation_actions = (
            "/conversation new Subject",
            "/conversation list",
            "/conversation list --all",
            f"/conversation resume {_UNIQUE_C}",
            "/conversation subject Revised",
            "/conversation archive",
        )
        future_actions = (
            "/compose",
            "/send",
            "/cancel",
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
                rendered.count("[error session_not_attached]"),
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
                    f"/conversation resume {_UNIQUE_C}",
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
            load_default_conversation(workspace, "session"),
            entry.conversation_id,
        )
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(InteractiveErrorCode.CONVERSATION_INVALID, width=200),
            rendered,
        )
        self.assertIn("Renamed c_fedcba9876 | Subject: Revised subject", rendered)
        self.assertEqual(
            [
                record
                for record in callbacks.records
                if record.startswith("--- Conversation scope")
            ],
            [
                _conversation_scope_record(
                    "session",
                    entry.conversation_id[:12],
                    "Original",
                ),
                _conversation_scope_record(
                    "session",
                    entry.conversation_id[:12],
                    "Revised subject",
                ),
            ],
        )

    def test_failed_scope_write_is_not_cached_then_success_is_cached(self) -> None:
        entry = _conversation_metadata(_UNIQUE_C, "Selected")
        _workspace, _backend, _catalog, controller, callbacks = (
            self.prepared_controller(
                entries=(entry,),
                selected_id=entry.conversation_id,
            )
        )
        controller._attached_session_id = "session"
        controller._focused_conversation = entry
        attempts = 0
        real_write_scope = InteractiveTerminal.write_conversation_scope

        def fail_once(output: object, **kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("scope writer unavailable")
            real_write_scope(output, **kwargs)

        unpresented = object()
        with mock.patch.object(
            InteractiveTerminal,
            "write_conversation_scope",
            autospec=True,
            side_effect=fail_once,
        ):
            with self.assertRaisesRegex(OSError, "scope writer unavailable"):
                controller._write_attached_conversation_scope(unpresented)
            presented = controller._write_attached_conversation_scope(
                unpresented
            )
            repeated = controller._write_attached_conversation_scope(presented)

        self.assertEqual(repeated, presented)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            callbacks.records,
            [
                _conversation_scope_record(
                    "session",
                    entry.conversation_id[:12],
                    "Selected",
                )
            ],
        )

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
                    load_default_conversation(workspace, "session"),
                    expected,
                )
                self.assertIn(
                    'Archive current conversation "Selected"',
                    callbacks.prompts[1],
                )
                rendered = "".join(callbacks.records)
                if has_successor:
                    self.assertIn("current focus: c_fedcba9876", rendered)
                else:
                    self.assertIn("current focus is none", rendered)

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
            load_default_conversation(workspace, "session"),
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
                    "release_session_access",
                    wraps=backend.release_session_access,
                ) as detach:
                    self.assertEqual(controller.run_session_lobby(), 0)
                detach.assert_called_once_with("session")
                self.assertIs(
                    catalog.entries[0].status,
                    ConversationStatus.ACTIVE,
                )
                self.assertEqual(
                    load_default_conversation(workspace, "session"),
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
                            f"/conversation resume {target.conversation_id}",
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

                real_write_component = (
                    terminal_module.InteractiveSemanticOutput.write_component
                )

                def fail_committed_notice(output, component):
                    """Fail only the post-commit conversation acknowledgement."""

                    if (
                        type(component) is terminal_module.Notice
                        and component.label == "conversation"
                    ):
                        raise RuntimeError("writer-secret")
                    return real_write_component(output, component)

                with (
                    mock.patch.object(
                        terminal_module.InteractiveSemanticOutput,
                        "write_component",
                        autospec=True,
                        side_effect=fail_committed_notice,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
                        wraps=backend.release_session_access,
                    ) as detach,
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)

                detach.assert_called_once_with("session")
                rendered = "".join(callbacks.records)
                self.assertIn(
                    _error_record(
                        InteractiveErrorCode.INTERNAL_ERROR,
                        width=200,
                    ),
                    rendered,
                )
                self.assertNotIn("writer-secret", rendered)
                self.assertFalse(
                    workspace.path(_access_relative("session")).exists()
                )
                if operation == "create":
                    self.assertEqual(len(catalog.entries), 1)
                    self.assertEqual(
                        load_default_conversation(workspace, "session"),
                        catalog.entries[0].conversation_id,
                    )
                elif operation == "use":
                    self.assertEqual(
                        load_default_conversation(workspace, "session"),
                        target.conversation_id,
                    )
                elif operation == "rename":
                    self.assertEqual(catalog.entries[0].subject, "Revised")
                    self.assertEqual(
                        load_default_conversation(workspace, "session"),
                        selected.conversation_id,
                    )
                else:
                    self.assertIs(
                        catalog.entries[0].status,
                        ConversationStatus.ARCHIVED,
                    )
                    self.assertEqual(
                        load_default_conversation(workspace, "session"),
                        successor.conversation_id,
                    )

    def test_session_pause_and_open_restore_the_same_durable_conversation(
        self,
    ) -> None:
        selected = _conversation_metadata(_UNIQUE_C, "Persistent subject")
        workspace, _backend, catalog, controller, callbacks = (
            self.prepared_controller(
                "/session pause",
                "yes",
                "/session resume",
                "",
                "/quit",
                entries=(selected,),
                selected_id=selected.conversation_id,
            )
        )
        selection_path = workspace.path(_default_relative("session"))
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
            load_default_conversation(workspace, "session"),
            selected.conversation_id,
        )
        self.assertEqual(
            workspace.read_json(_manifest_relative("session"))["durable_state"],
            "ready",
        )
        rendered = "".join(callbacks.records)
        self.assertEqual(
            callbacks.records.count(
                _conversation_scope_record(
                    "session",
                    selected.conversation_id[:12],
                    "Persistent subject",
                )
            ),
            2,
        )
        self.assertIn("Paused session; default retained", rendered)
        self.assertIn("Session: session (reopened)", rendered)
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
        """Create ready source and destination sessions with a source default."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "source", durable_state=SessionDurableState.READY)
        self.seed_session(workspace, "destination", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "source")
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            "/session resume destination",
            "/quit",
            backend=backend,
            terminal_width=200,
        )
        return workspace, backend, controller, callbacks

    def test_successful_switch_orders_acquire_default_release_and_cleanup(self) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        events: list[str] = []
        original_get = backend.get_session_lifecycle
        original_attach = backend.acquire_session_access
        original_detach = backend.release_session_access
        original_close = backend.close

        def record_get(session_id: str):
            events.append(f"get:{session_id}")
            return original_get(session_id)

        def record_attach(session_id: str):
            events.append(f"attach:{session_id}")
            return original_attach(session_id)

        def record_detach(session_id: str):
            events.append(f"detach:{session_id}")
            return original_detach(session_id)

        def record_select(workspace_value: Workspace, session_id: str):
            events.append(f"select:{session_id}")
            return save_default_session(workspace_value, session_id)

        def record_close() -> None:
            events.append("close")
            original_close()

        with (
            mock.patch.object(backend, "get_session_lifecycle", side_effect=record_get),
            mock.patch.object(backend, "acquire_session_access", side_effect=record_attach),
            mock.patch.object(backend, "release_session_access", side_effect=record_detach),
            mock.patch.object(backend, "close", side_effect=record_close),
            mock.patch.object(
                controller_core_module,
                "save_default_session",
                side_effect=record_select,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)

        self.assertEqual(
            events,
            [
                "get:source",
                "get:source",
                "attach:source",
                "get:source",
                "get:source",
                "get:destination",
                "attach:destination",
                "get:destination",
                "get:destination",
                "select:destination",
                "detach:source",
                "get:source",
                "detach:destination",
                "get:destination",
                "close",
            ],
        )
        self.assertEqual(
            load_cli_context(workspace).default_session_id,
            "destination",
        )
        rendered = "".join(callbacks.records)
        self.assertLess(
            rendered.index("Session: source (opened)"),
            rendered.index("Session: destination (opened)"),
        )

    def test_destination_failure_preserves_source_default_and_files(self) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        before = _tree_snapshot(workspace.root)
        original_attach = backend.acquire_session_access
        original_detach = backend.release_session_access
        events: list[str] = []

        def attach(session_id: str):
            events.append(f"attach:{session_id}")
            if session_id == "destination":
                raise SessionAccessBlockedError("secret-owner-token")
            return original_attach(session_id)

        def detach(session_id: str):
            events.append(f"detach:{session_id}")
            return original_detach(session_id)

        with (
            mock.patch.object(backend, "acquire_session_access", side_effect=attach),
            mock.patch.object(backend, "release_session_access", side_effect=detach),
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(
            events,
            ["attach:source", "attach:destination", "detach:source"],
        )
        self.assertEqual(_tree_snapshot(workspace.root), before)
        self.assertEqual(load_cli_context(workspace).default_session_id, "source")
        rendered = "".join(callbacks.records)
        self.assertIn(
            _error_record(
                InteractiveErrorCode.SESSION_ACCESS_BLOCKED,
                width=200,
            ),
            rendered,
        )
        self.assertNotIn("secret-owner-token", rendered)
        self.assertEqual(rendered.count("Session: source (opened)\n"), 1)

    def test_default_failure_releases_destination_before_source_cleanup(self) -> None:
        cases = (
            (SessionIdError("selection-secret"), 0),
            (OSError("selection-secret"), 2),
        )
        for failure, expected_code in cases:
            with self.subTest(failure=type(failure).__name__):
                workspace, backend, controller, callbacks = self.prepare_switch()
                original_detach = backend.release_session_access
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
                        controller_core_module,
                        "save_default_session",
                        side_effect=fail_destination_selection,
                    ),
                    mock.patch.object(
                        backend,
                        "release_session_access",
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
                    load_cli_context(workspace).default_session_id,
                    "source",
                )
                self.assertFalse(
                    workspace.path(_access_relative("destination")).exists()
                )
                rendered = "".join(callbacks.records)
                expected_error = (
                    InteractiveErrorCode.SESSION_INVALID
                    if expected_code == 0
                    else InteractiveErrorCode.WORKSPACE_INVALID
                )
                self.assertIn(_error_record(expected_error, width=200), rendered)
                self.assertNotIn("selection-secret", rendered)

    def test_failed_destination_release_is_fatal_and_defers_to_close(self) -> None:
        workspace, backend, controller, callbacks = self.prepare_switch()
        original_detach = backend.release_session_access
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
                controller_core_module,
                "save_default_session",
                side_effect=fail_selection,
            ),
            mock.patch.object(
                backend,
                "release_session_access",
                side_effect=fail_destination_detach,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        self.assertEqual(events, ["select:destination", "detach:destination"])
        self.assertEqual(load_cli_context(workspace).default_session_id, "source")
        self.assertFalse(workspace.path(_access_relative("source")).exists())
        self.assertFalse(workspace.path(_access_relative("destination")).exists())
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
        original_detach = backend.release_session_access
        events: list[str] = []

        def record_select(workspace_value: Workspace, session_id: str):
            events.append(f"select:{session_id}")
            return save_default_session(workspace_value, session_id)

        def fail_source_detach(session_id: str):
            events.append(f"detach:{session_id}")
            if session_id == "source":
                raise OSError("source cleanup secret")
            return original_detach(session_id)

        with (
            mock.patch.object(
                controller_core_module,
                "save_default_session",
                side_effect=record_select,
            ),
            mock.patch.object(
                backend,
                "release_session_access",
                side_effect=fail_source_detach,
            ),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        self.assertEqual(events, ["select:destination", "detach:source"])
        self.assertEqual(
            load_cli_context(workspace).default_session_id,
            "destination",
        )
        self.assertFalse(workspace.path(_access_relative("source")).exists())
        self.assertFalse(workspace.path(_access_relative("destination")).exists())
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("source cleanup secret", rendered)
        self.assertNotIn("Session: destination (opened)", rendered)


class ControllerEndAndCleanupTests(ControllerTestCase):
    """Verify pause confirmation, state verification, and exit precedence."""

    def prepared_attached_controller(
        self,
        *reads: object,
    ) -> tuple[
        Workspace,
        AgentBackend,
        InteractiveController,
        ScriptedTerminalCallbacks,
    ]:
        """Create a default ready session and controller with scripted input."""

        workspace = self.make_workspace()
        self.seed_session(workspace, "session", durable_state=SessionDurableState.READY)
        save_default_session(workspace, "session")
        backend = self.make_backend(workspace)
        controller, _backend, callbacks = self.make_controller(
            workspace,
            *reads,
            backend=backend,
        )
        return workspace, backend, controller, callbacks

    def test_pause_cancellation_and_eof_preserve_ready_session(self) -> None:
        cases = (
            ("blank", "", False),
            ("no", "NO", False),
            ("interrupt", KeyboardInterrupt(), False),
            ("eof", EOFError(), True),
        )
        for label, decision, exits in cases:
            with self.subTest(case=label):
                reads = ("/session pause", decision)
                if not exits:
                    reads += ("/quit",)
                workspace, backend, controller, callbacks = (
                    self.prepared_attached_controller(*reads)
                )
                with mock.patch.object(
                    backend,
                    "pause_session_and_release_access",
                    wraps=backend.pause_session_and_release_access,
                ) as end:
                    self.assertEqual(controller.run_session_lobby(), 0)
                end.assert_not_called()
                self.assertEqual(
                    workspace.read_json(_manifest_relative("session"))[
                        "durable_state"
                    ],
                    "ready",
                )
                self.assertFalse(
                    workspace.path(_access_relative("session")).exists()
                )
                self.assertIn(
                    _confirmation_prompt(
                        "Pause session session?",
                        default=False,
                    ),
                    callbacks.prompts,
                )
                rendered = "".join(callbacks.records)
                if exits:
                    self.assertNotIn("Session pause cancelled.", rendered)
                else:
                    self.assertIn(
                        "[cancelled] Session pause cancelled.\n",
                        rendered,
                    )

    def test_successful_pause_retains_default_and_returns_to_lobby(self) -> None:
        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller(
                "/session pause",
                "yes",
                "/quit",
            )
        )
        with mock.patch.object(
            backend,
            "release_session_access",
            wraps=backend.release_session_access,
        ) as detach:
            self.assertEqual(controller.run_session_lobby(), 0)
        detach.assert_not_called()
        self.assertEqual(
            workspace.read_json(_manifest_relative("session"))["durable_state"],
            "paused",
        )
        self.assertEqual(load_cli_context(workspace).default_session_id, "session")
        rendered = "".join(callbacks.records)
        self.assertIn("[session] Paused session; default retained.\n", rendered)
        self.assertIn("Session: lobby\n", rendered)

    def test_confirmed_ready_held_pause_failure_is_recoverable_and_safe(self) -> None:
        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller(
                "/session pause",
                "yes",
                "/quit",
            )
        )
        with (
            mock.patch.object(
                backend,
                "pause_session_and_release_access",
                side_effect=OSError("end-secret"),
            ),
            mock.patch.object(
                backend,
                "get_session_lifecycle",
                wraps=backend.get_session_lifecycle,
            ) as summary,
        ):
            self.assertEqual(controller.run_session_lobby(), 0)
        self.assertEqual(summary.call_count, 6)
        self.assertEqual(
            workspace.read_json(_manifest_relative("session"))["durable_state"],
            "ready",
        )
        rendered = "".join(callbacks.records)
        self.assertIn(_error_record(InteractiveErrorCode.WORKSPACE_INVALID), rendered)
        self.assertNotIn("end-secret", rendered)
        self.assertEqual(rendered.count("Session: session (opened)\n"), 1)

    def test_unconfirmed_pause_state_is_fatal_cleanup_uncertainty(self) -> None:
        def raise_lifecycle_read(_value: SessionLifecycleState) -> object:
            """Simulate a lifecycle read that cannot confirm process access."""

            raise WorkspaceFormatError("summary-read-secret")

        cases: tuple[tuple[str, Callable[[SessionLifecycleState], object]], ...] = (
            ("paused", lambda value: replace(value, durable_state=SessionDurableState.PAUSED)),
            (
                "claimable",
                lambda value: replace(
                    value,
                    access_state=SessionAccessState.CLAIMABLE,
                ),
            ),
            ("foreign", lambda value: replace(value, session_id="other")),
            ("malformed", lambda _value: object()),
            ("read_failure", raise_lifecycle_read),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                workspace, backend, controller, callbacks = (
                    self.prepared_attached_controller(
                        "/session pause",
                        "yes",
                    )
                )
                original_get = backend.get_session_lifecycle
                calls = 0

                def changing_lifecycle(
                    session_id: str,
                ) -> object:
                    nonlocal calls
                    calls += 1
                    value = original_get(session_id)
                    return value if calls <= 4 else mutate(value)

                with (
                    mock.patch.object(
                        backend,
                        "get_session_lifecycle",
                        side_effect=changing_lifecycle,
                    ),
                    mock.patch.object(
                        backend,
                        "pause_session_and_release_access",
                        side_effect=OSError("uncertain-secret"),
                    ),
                ):
                    self.assertEqual(controller.run_session_lobby(), 2)
                rendered = "".join(callbacks.records)
                self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
                self.assertNotIn("uncertain-secret", rendered)
                self.assertFalse(
                    workspace.path(_access_relative("session")).exists()
                )

    def test_quit_exit_and_eof_release_before_close_without_pausing(self) -> None:
        for terminal_exit in ("/quit", "/exit", EOFError()):
            with self.subTest(exit=repr(terminal_exit)):
                workspace, backend, controller, _callbacks = (
                    self.prepared_attached_controller(terminal_exit)
                )
                events: list[str] = []
                original_detach = backend.release_session_access
                original_close = backend.close

                def detach(session_id: str):
                    events.append(f"detach:{session_id}")
                    return original_detach(session_id)

                def close() -> None:
                    events.append("close")
                    original_close()

                with (
                    mock.patch.object(backend, "release_session_access", side_effect=detach),
                    mock.patch.object(backend, "close", side_effect=close),
                ):
                    self.assertEqual(controller.run_session_lobby(), 0)
                self.assertEqual(events, ["detach:session", "close"])
                self.assertEqual(
                    workspace.read_json(_manifest_relative("session"))[
                        "durable_state"
                    ],
                    "ready",
                )

    def test_cleanup_failure_overrides_normal_exit_and_not_primary_error(self) -> None:
        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller("/quit")
        )
        with mock.patch.object(
            backend,
            "release_session_access",
            side_effect=OSError("detach-secret"),
        ):
            self.assertEqual(controller.run_session_lobby(), 2)
        rendered = "".join(callbacks.records)
        self.assertEqual(rendered.count("[error cleanup_failed]"), 1)
        self.assertNotIn("detach-secret", rendered)
        self.assertFalse(workspace.path(_access_relative("session")).exists())

        workspace, backend, controller, callbacks = (
            self.prepared_attached_controller(RuntimeError("primary-secret"))
        )
        with mock.patch.object(
            backend,
            "release_session_access",
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
        self.assertFalse(workspace.path(_access_relative("session")).exists())


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


if __name__ == "__main__":
    unittest.main()
