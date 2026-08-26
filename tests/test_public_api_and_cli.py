"""Public import, durable inspection, and command-line behavior tests."""

from __future__ import annotations

import contextlib
import errno
import io
import inspect
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from collections.abc import Awaitable, Callable
from dataclasses import MISSING, FrozenInstanceError, fields
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import story_writing_agents
from story_writing_agents import cli as cli_module
from story_writing_agents import workspace as workspace_module
from story_writing_agents.conversation_storage import (
    selection as storage_selection_module,
)
from story_writing_agents import (
    AGENT_SPECS,
    ConversationEventStore,
    ConversationMemoryService,
    ConversationRetrievalProgressSink,
    ConversationRetrievalResult,
    ConversationResult,
    DebateContextMode,
    DebateContextRequest,
    DebateFailureKind,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateResult,
    DebateStatus,
    InputKind,
    PLOT_ARCHITECT,
    ProgressObserverError,
    Runtime,
    RuntimeSession,
    Session,
    StoryRuntime,
    Workspace,
)
from story_writing_agents.cli import build_parser, main
from story_writing_agents.cli_context import CliContext
from story_writing_agents.debug_log import WorkspaceDebugLog
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStoragePreflight,
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
    load_conversation_storage_activation_state,
)
from story_writing_agents.conversation_storage.selection import (
    ConversationStorageStartup,
)
from story_writing_agents.conversation_rag_runtime import (
    ConversationRagRuntimeService,
)
from story_writing_agents.errors import (
    BackendProtocolError,
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from story_writing_agents.interactive_terminal import (
    INTERACTIVE_ERROR_SPECS,
    InteractiveErrorCode,
    InteractiveTerminal,
)
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.interactive_terminal._render import render_plain
from story_writing_agents.interactive_auth import (
    InteractiveAuthResult,
    InteractiveAuthResultKind,
)
from story_writing_agents.provider_auth import (
    ProviderAuthSnapshot,
    ProviderAuthState,
    ProviderAuthStatus,
)
from story_writing_agents.workspace import DEFAULT_AGENT_PROMPTS

from tests.helpers import initialized_workspace
from tests.sqlite_conversation_storage_fixture import database_path, provision


class TtyStringIO(io.StringIO):
    """In-memory stream with observable terminal capability and input reads."""

    def __init__(self, *, is_terminal: bool, value: str = "") -> None:
        super().__init__(value)
        self.is_terminal = is_terminal
        self.readline_calls = 0

    def isatty(self) -> bool:
        """Return the configured terminal capability without other effects."""

        return self.is_terminal

    def readline(self, *args, **kwargs) -> str:
        """Record any attempted input read before delegating to StringIO."""

        self.readline_calls += 1
        return super().readline(*args, **kwargs)


def authenticated_provider_snapshot() -> ProviderAuthSnapshot:
    """Return a deterministic two-provider authorization fixture."""

    return ProviderAuthSnapshot(
        (
            ProviderAuthStatus("codex", ProviderAuthState.AUTHENTICATED),
            ProviderAuthStatus(
                "antigravity",
                ProviderAuthState.AUTHENTICATED,
            ),
        )
    )


def provider_snapshot(
    codex: ProviderAuthState,
    antigravity: ProviderAuthState,
) -> ProviderAuthSnapshot:
    """Return one ordered snapshot for CLI gate matrices."""

    return ProviderAuthSnapshot(
        (
            ProviderAuthStatus("codex", codex),
            ProviderAuthStatus("antigravity", antigravity),
        )
    )


def provider_result(
    current: ProviderAuthSnapshot,
    provider: str,
) -> InteractiveAuthResult:
    """Return a correlated provider result from one typed snapshot."""

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


class FailClosedCliAuthService:
    """Prevent every unit CLI test from reaching an installed executable."""

    def __init__(self, workspace_root: Path, **kwargs: object) -> None:
        self.workspace_root = workspace_root
        self.kwargs = kwargs

    def probe(self, provider: str) -> ProviderAuthStatus:
        raise AssertionError(f"CLI fixture made an unexpected native probe: {provider}")

    def probe_all(self) -> ProviderAuthSnapshot:
        raise AssertionError("CLI fixture made an unexpected native aggregate probe")

    def login(self, provider: str) -> object:
        raise AssertionError(f"CLI fixture made an unexpected native login: {provider}")


class AuthenticatedCliCoordinator:
    """Authorize startup deterministically without I/O, sleeps, or subprocesses."""

    def __init__(self, auth_service: object, terminal: object) -> None:
        if not isinstance(auth_service, FailClosedCliAuthService):
            raise AssertionError("CLI auth fixture received an unknown service")
        self.auth_service = auth_service
        self.terminal = terminal
        self.snapshot = authenticated_provider_snapshot()
        self.events: list[str] = []

    def show_welcome(self) -> None:
        self.events.append("welcome")

    def probe_all(self) -> ProviderAuthSnapshot:
        self.events.append("probe_all")
        return self.snapshot

    def run(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("fully authenticated CLI fixture opened the auth picker")


class ScriptedCliCoordinator:
    """Supply exact startup snapshots/results while recording gate order."""

    def __init__(
        self,
        events: list[tuple[str, object | None]],
        *,
        snapshots: tuple[object, ...],
        results: tuple[object, ...] = (),
    ) -> None:
        self.events = events
        self.snapshots = list(snapshots)
        self.results = list(results)

    def show_welcome(self) -> None:
        self.events.append(("welcome", None))

    def probe_all(self) -> ProviderAuthSnapshot:
        self.events.append(("probe_all", None))
        if not self.snapshots:
            raise AssertionError("unexpected CLI auth probe")
        value = self.snapshots.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]

    def run(
        self,
        current: ProviderAuthSnapshot,
        *,
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
        self.events.append(("run", selected_provider))
        if not self.results:
            raise AssertionError("unexpected CLI auth picker")
        value = self.results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


def expected_interactive_error(
    code: InteractiveErrorCode,
    *,
    debug_log_available: bool = False,
    summary: str | None = None,
    hint: str | None = None,
) -> str:
    """Return the exact callback-profile semantic startup error record."""

    spec = INTERACTIVE_ERROR_SPECS[code]
    notices = [
        terminal_module.Notice(
            spec.summary if summary is None else summary,
            label=f"error {code.value}",
            role=terminal_module.SemanticRole.ERROR,
            state=terminal_module.ViewState.ERROR,
        ),
        terminal_module.Notice(spec.hint if hint is None else hint, label="hint"),
    ]
    if debug_log_available:
        notices.append(
            terminal_module.Notice(
                "If available, workspace-relative: debug_logs/runtime.jsonl",
                label="debug log",
            )
        )
    component = terminal_module.Panel(
        "Interactive error",
        tuple(notices),
        state=terminal_module.ViewState.ERROR,
    )
    return f"{render_plain(component, width=80).text}\n"


class PackagingTests(unittest.TestCase):
    def test_console_script_aliases_map_to_the_same_exact_main_function(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with (repository_root / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        scripts = project["scripts"]
        expected_target = "story_writing_agents.cli:main"
        self.assertIn("story-writing-agents", scripts)
        self.assertIn("story", scripts)
        self.assertEqual(scripts["story-writing-agents"], expected_target)
        self.assertEqual(scripts["story"], expected_target)
        self.assertEqual(
            scripts["story"],
            scripts["story-writing-agents"],
        )


class PublicApiTests(unittest.TestCase):
    def test_all_declared_public_imports_exist_and_runtime_aliases_are_stable(
        self,
    ) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 120)
        for name in story_writing_agents.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(story_writing_agents, name))
        self.assertIs(Runtime, StoryRuntime)
        self.assertIs(Session, RuntimeSession)
        for removed in (
            "Session" + "LeaseState",
            "Session" + "Summary",
            "Session" + "InUseError",
        ):
            self.assertNotIn(removed, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, removed))

        for lifecycle_name in (
            "SessionDurableState",
            "SessionAccessState",
            "SessionLifecycleState",
            "provider_work_is_ready",
            "SessionExistsError",
            "SessionNotFoundError",
            "SessionPausedError",
            "SessionAccessBlockedError",
            "SessionOwnershipUnknownError",
            "WorkspaceVersionUnsupportedError",
        ):
            self.assertIn(lifecycle_name, story_writing_agents.__all__)
            self.assertTrue(hasattr(story_writing_agents, lifecycle_name))

    def test_top_level_exports_include_models_directives_and_public_errors(
        self,
    ) -> None:
        required = {
            "AgentBackend",
            "AgentDefinition",
            "AgentReply",
            "AgentRequest",
            "AgentStateEntry",
            "BoundAgent",
            "ConversationReply",
            "ConversationRequest",
            "ConversationResult",
            "DirectiveKind",
            "PromptFileReference",
            "StoryWritingAgentsError",
            "WorkspaceSecurityError",
            "WorkspaceFormatError",
            "ConfigurationError",
            "DirectiveError",
            "SessionIdError",
            "ManuscriptExistsError",
            "BackendProtocolError",
            "DebateExistsError",
        }
        self.assertTrue(required.issubset(set(story_writing_agents.__all__)))
        for name in required:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(story_writing_agents, name))
        from story_writing_agents import models

        for name in (
            "ConversationRequest",
            "ConversationReply",
            "ConversationResult",
        ):
            self.assertIs(
                getattr(story_writing_agents, name),
                getattr(models, name),
            )
        base_error = story_writing_agents.StoryWritingAgentsError
        for name in required.difference(
            {
                "AgentBackend",
                "AgentDefinition",
                "AgentReply",
                "AgentRequest",
                "AgentStateEntry",
                "BoundAgent",
                "ConversationReply",
                "ConversationRequest",
                "ConversationResult",
                "DirectiveKind",
                "PromptFileReference",
                "StoryWritingAgentsError",
            }
        ):
            with self.subTest(error=name):
                self.assertTrue(
                    issubclass(getattr(story_writing_agents, name), base_error)
                )

    def test_agent_backend_signature_and_configuration_exports_are_exact(
        self,
    ) -> None:
        signature = inspect.signature(story_writing_agents.AgentBackend)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "workspace",
                "executable",
                "model",
                "timeout_seconds",
                "backend_overrides",
                "antigravity_executable",
                "antigravity_model",
                "antigravity_effort",
            ),
        )
        self.assertIs(
            signature.parameters["workspace"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for name in (
            "executable",
            "model",
            "timeout_seconds",
            "backend_overrides",
            "antigravity_executable",
            "antigravity_model",
            "antigravity_effort",
        ):
            with self.subTest(parameter=name):
                self.assertIs(
                    signature.parameters[name].kind,
                    inspect.Parameter.KEYWORD_ONLY,
                )
        self.assertEqual(signature.parameters["executable"].default, "codex")
        self.assertIsNone(signature.parameters["model"].default)
        self.assertEqual(signature.parameters["timeout_seconds"].default, 300)
        self.assertIsNone(signature.parameters["backend_overrides"].default)
        self.assertEqual(
            signature.parameters["antigravity_executable"].default,
            "agy",
        )
        self.assertIsNone(signature.parameters["antigravity_model"].default)
        self.assertIsNone(signature.parameters["antigravity_effort"].default)
        for internal in ("ConfigManager", "SystemDefaults"):
            with self.subTest(internal=internal):
                self.assertNotIn(internal, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, internal))

    def test_uninitialized_direct_constructors_preserve_results_and_artifacts(
        self,
    ) -> None:
        """Keep diagnostics lazy before a direct API has a usable workspace."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "uninitialized"
            runtime = StoryRuntime(target)
            self.assertIsInstance(runtime, StoryRuntime)
            self.assertEqual(runtime.workspace.root, target.resolve())
            self.assertFalse(target.exists())

            with self.assertRaises(WorkspaceFormatError) as caught:
                story_writing_agents.AgentBackend(target)

            self.assertEqual(
                str(caught.exception),
                "cannot read file: config/system_defaults.json",
            )
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
            self.assertFalse(target.exists())

    def test_debate_context_and_progress_public_models_are_exact(self) -> None:
        required = {
            "DebateContextMode",
            "DebateContextRequest",
            "DebateFailureKind",
            "DebateProgressEvent",
            "DebateProgressPhase",
            "DebateProgressSink",
            "ProgressObserverError",
        }
        self.assertTrue(required.issubset(story_writing_agents.__all__))
        self.assertEqual(
            tuple((member.name, member.value) for member in DebateContextMode),
            (("INCLUDED", "included"), ("FRESH", "fresh")),
        )
        self.assertEqual(
            tuple((member.name, member.value) for member in DebateProgressPhase),
            (
                ("STARTED", "started"),
                ("DRAFTED", "drafted"),
                ("AUDIT_STARTED", "audit_started"),
                ("AUDIT_COMPLETED", "audit_completed"),
                ("SYNTHESIZED", "synthesized"),
                ("ARTIFACTS_COMMITTED", "artifacts_committed"),
                ("CONSENSUS", "consensus"),
                ("DEADLOCK", "deadlock"),
                ("FAILED", "failed"),
                ("INTERRUPTED", "interrupted"),
            ),
        )
        self.assertEqual(
            tuple((member.name, member.value) for member in DebateFailureKind),
            (
                ("PREFLIGHT", "preflight"),
                ("PROTOCOL", "protocol"),
                ("CONTEXT", "context"),
                ("WORKSPACE", "workspace"),
                ("ARTIFACT_CONFLICT", "artifact_conflict"),
                ("ARTIFACT_FAILURE", "artifact_failure"),
                ("INTERNAL", "internal"),
            ),
        )

        context_fields = fields(DebateContextRequest)
        self.assertEqual(
            tuple(field.name for field in context_fields),
            ("conversation_id", "mode"),
        )
        self.assertTrue(all(field.default is MISSING for field in context_fields))
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in inspect.signature(
                    DebateContextRequest
                ).parameters.values()
            )
        )
        request = DebateContextRequest(
            conversation_id="conversation_a",
            mode=DebateContextMode.INCLUDED,
        )
        self.assertFalse(hasattr(request, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            request.mode = DebateContextMode.FRESH  # type: ignore[misc]
        with self.assertRaises(TypeError):
            DebateContextRequest(  # type: ignore[misc]
                "conversation_a",
                DebateContextMode.INCLUDED,
            )

        progress_fields = fields(DebateProgressEvent)
        self.assertEqual(
            tuple(field.name for field in progress_fields),
            (
                "session_id",
                "debate_id",
                "input_kind",
                "phase",
                "revision_id",
                "round_number",
                "context_mode",
                "context_event_count",
                "context_omitted_turns",
                "conflicting_agents",
                "manuscript_path",
                "core_updates",
                "failure_kind",
            ),
        )
        self.assertEqual(
            tuple(field.default for field in progress_fields),
            (
                MISSING,
                MISSING,
                MISSING,
                MISSING,
                None,
                0,
                None,
                0,
                0,
                (),
                None,
                (),
                None,
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in inspect.signature(
                    DebateProgressEvent
                ).parameters.values()
            )
        )
        event = DebateProgressEvent(
            session_id="session_a",
            debate_id="debate_a",
            input_kind=InputKind.PROMPT,
            phase=DebateProgressPhase.STARTED,
        )
        self.assertFalse(hasattr(event, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            event.phase = DebateProgressPhase.DRAFTED  # type: ignore[misc]
        self.assertEqual(tuple(inspect.signature(ProgressObserverError).parameters), ())
        observer_error = ProgressObserverError()
        self.assertEqual(str(observer_error), "debate progress observer failed")
        self.assertFalse(hasattr(observer_error, "cause"))
        self.assertIsNone(observer_error.__cause__)
        self.assertNotIn("private observer prose", str(observer_error))
        with self.assertRaises(TypeError):
            ProgressObserverError(RuntimeError("private observer prose"))

    def test_debate_progress_phase_invariants_are_exhaustive(self) -> None:
        common = {
            "session_id": "session_a",
            "debate_id": "debate_a",
            "input_kind": InputKind.PROMPT,
        }
        valid = (
            {"phase": DebateProgressPhase.STARTED},
            {
                "phase": DebateProgressPhase.STARTED,
                "context_mode": DebateContextMode.INCLUDED,
                "context_event_count": 2,
                "context_omitted_turns": 3,
            },
            {
                "phase": DebateProgressPhase.DRAFTED,
                "revision_id": "r0",
            },
            {
                "phase": DebateProgressPhase.AUDIT_STARTED,
                "revision_id": "r0",
            },
            {
                "phase": DebateProgressPhase.AUDIT_COMPLETED,
                "revision_id": "r0",
                "conflicting_agents": ("plot_architect",),
            },
            {
                "phase": DebateProgressPhase.SYNTHESIZED,
                "revision_id": "r1",
                "round_number": 1,
            },
            {
                "phase": DebateProgressPhase.ARTIFACTS_COMMITTED,
                "revision_id": "r1",
                "round_number": 1,
                "manuscript_path": "manuscript/part-01/chapter.md",
                "core_updates": ("global_outline.md",),
            },
            {
                "phase": DebateProgressPhase.CONSENSUS,
                "revision_id": "r1",
                "round_number": 1,
            },
            {
                "phase": DebateProgressPhase.DEADLOCK,
                "revision_id": "r1",
                "round_number": 1,
            },
            {
                "phase": DebateProgressPhase.FAILED,
                "revision_id": None,
                "round_number": 2,
                "failure_kind": DebateFailureKind.INTERNAL,
            },
            {"phase": DebateProgressPhase.INTERRUPTED},
        )
        for values in valid:
            with self.subTest(valid=values["phase"]):
                self.assertIsInstance(
                    DebateProgressEvent(**common, **values),  # type: ignore[arg-type]
                    DebateProgressEvent,
                )

        invalid = (
            {"phase": DebateProgressPhase.STARTED, "revision_id": "r0"},
            {"phase": DebateProgressPhase.STARTED, "context_event_count": 1},
            {
                "phase": DebateProgressPhase.STARTED,
                "context_mode": DebateContextMode.FRESH,
                "context_omitted_turns": 1,
            },
            {"phase": DebateProgressPhase.DRAFTED, "revision_id": "r1"},
            {
                "phase": DebateProgressPhase.AUDIT_STARTED,
                "revision_id": "r0",
                "conflicting_agents": ("plot_architect",),
            },
            {
                "phase": DebateProgressPhase.AUDIT_COMPLETED,
                "revision_id": "r0",
                "conflicting_agents": ("execution_specialist", "plot_architect"),
            },
            {
                "phase": DebateProgressPhase.SYNTHESIZED,
                "revision_id": "r1",
            },
            {
                "phase": DebateProgressPhase.ARTIFACTS_COMMITTED,
                "revision_id": "r0",
            },
            {
                "phase": DebateProgressPhase.CONSENSUS,
                "revision_id": None,
            },
            {
                "phase": DebateProgressPhase.DEADLOCK,
                "revision_id": "r0",
                "context_mode": DebateContextMode.FRESH,
            },
            {"phase": DebateProgressPhase.FAILED},
            {
                "phase": DebateProgressPhase.INTERRUPTED,
                "failure_kind": DebateFailureKind.INTERNAL,
            },
        )
        for values in invalid:
            with self.subTest(invalid=values):
                with self.assertRaises((TypeError, ValueError)):
                    DebateProgressEvent(**common, **values)  # type: ignore[arg-type]

    def test_debate_run_signatures_and_additive_result_shape_are_exact(
        self,
    ) -> None:
        method_cases = (
            (
                StoryRuntime.run_prompt,
                (
                    "self",
                    "session_id",
                    "prompt",
                    "output_path",
                    "max_debate_rounds",
                    "debate_id",
                    "conversation_context",
                    "progress_sink",
                ),
                3,
            ),
            (
                StoryRuntime.run_draft,
                (
                    "self",
                    "session_id",
                    "draft",
                    "output_path",
                    "max_debate_rounds",
                    "debate_id",
                    "conversation_context",
                    "progress_sink",
                ),
                3,
            ),
            (
                RuntimeSession.run_prompt,
                (
                    "self",
                    "prompt",
                    "output_path",
                    "max_debate_rounds",
                    "debate_id",
                    "conversation_context",
                    "progress_sink",
                ),
                2,
            ),
            (
                RuntimeSession.run_draft,
                (
                    "self",
                    "draft",
                    "output_path",
                    "max_debate_rounds",
                    "debate_id",
                    "conversation_context",
                    "progress_sink",
                ),
                2,
            ),
        )
        for method, names, positional_count in method_cases:
            with self.subTest(method=method.__qualname__):
                signature = inspect.signature(method)
                self.assertEqual(tuple(signature.parameters), names)
                parameters = tuple(signature.parameters.values())
                self.assertTrue(
                    all(
                        parameter.kind
                        is inspect.Parameter.POSITIONAL_OR_KEYWORD
                        for parameter in parameters[:positional_count]
                    )
                )
                keyword_only = parameters[positional_count:]
                self.assertTrue(
                    all(
                        parameter.kind is inspect.Parameter.KEYWORD_ONLY
                        for parameter in keyword_only
                    )
                )
                self.assertIs(
                    keyword_only[0].default,
                    inspect.Parameter.empty,
                )
                self.assertTrue(
                    all(parameter.default is None for parameter in keyword_only[1:])
                )
        for method, names in (
            (
                StoryRuntime.inspect_debate_context,
                ("self", "session_id", "debate_id"),
            ),
            (RuntimeSession.inspect_debate_context, ("self", "debate_id")),
        ):
            signature = inspect.signature(method)
            self.assertEqual(tuple(signature.parameters), names)

        self.assertEqual(
            tuple(field.name for field in fields(DebateResult)),
            (
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "revision_id",
                "rounds_completed",
                "final_candidate",
                "latest_candidate",
                "manuscript_path",
                "core_updates",
                "messages",
                "error",
                "action_required",
                "reference_operation_id",
            ),
        )
        legacy = DebateResult(
            "session_a",
            "debate_a",
            InputKind.DRAFT,
            DebateStatus.DEADLOCK,
            "r1",
            1,
            None,
            "candidate",
            None,
        )
        self.assertEqual(
            set(legacy.to_dict()),
            {
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "revision_id",
                "rounds_completed",
                "final_candidate",
                "latest_candidate",
                "manuscript_path",
                "core_updates",
                "messages",
                "error",
                "action_required",
            },
        )

    def test_conversation_runtime_signatures_are_exact_and_compatible(self) -> None:
        constructor = inspect.signature(StoryRuntime)
        self.assertEqual(
            tuple(constructor.parameters),
            (
                "workspace",
                "backend",
                "conversation_event_store",
                "conversation_storage_services",
                "conversation_rag_service",
            ),
        )
        self.assertIs(
            constructor.parameters["workspace"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        self.assertIs(
            constructor.parameters["backend"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        self.assertIsNone(constructor.parameters["backend"].default)
        collaborator = constructor.parameters["conversation_event_store"]
        self.assertIs(collaborator.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(collaborator.default)
        self.assertEqual(
            get_type_hints(StoryRuntime.__init__)["conversation_event_store"],
            ConversationEventStore | None,
        )
        services = constructor.parameters["conversation_storage_services"]
        self.assertIs(services.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(services.default)
        self.assertEqual(
            get_type_hints(StoryRuntime.__init__)[
                "conversation_storage_services"
            ],
            ConversationStorageServices | None,
        )
        rag_service = constructor.parameters["conversation_rag_service"]
        self.assertIs(rag_service.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(rag_service.default)
        self.assertEqual(
            get_type_hints(StoryRuntime.__init__)["conversation_rag_service"],
            ConversationRagRuntimeService | None,
        )

        workspace = initialized_workspace(self)
        bundle = compose_filesystem_conversation_storage(workspace)
        runtime = StoryRuntime(
            workspace,
            conversation_storage_services=bundle,
        )
        self.assertIs(runtime.conversation_storage_services, bundle)
        self.assertIs(runtime.conversation_event_store, bundle.event_store)
        self.assertIsNone(runtime.conversation_rag_service)
        legacy = StoryRuntime(workspace)
        self.assertIsInstance(
            legacy.conversation_storage_services,
            ConversationStorageServices,
        )
        with self.assertRaises(TypeError):
            StoryRuntime(
                workspace,
                conversation_event_store=bundle.event_store,
                conversation_storage_services=bundle,
            )

        cases = (
            (
                StoryRuntime.converse,
                ("self", "session_id", "conversation_id", "agent_id", "text"),
            ),
            (
                RuntimeSession.converse,
                ("self", "conversation_id", "agent_id", "text"),
            ),
        )
        for method, names in cases:
            with self.subTest(method=method.__qualname__):
                signature = inspect.signature(method)
                self.assertEqual(tuple(signature.parameters), names)
                self.assertTrue(
                    all(
                        parameter.kind
                        is inspect.Parameter.POSITIONAL_OR_KEYWORD
                        and parameter.default is inspect.Parameter.empty
                        for parameter in signature.parameters.values()
                    )
                )
                self.assertIs(
                    get_type_hints(method)["return"],
                    ConversationResult,
                )

    def test_retrieval_runtime_signatures_exports_and_legacy_compatibility_are_exact(
        self,
    ) -> None:
        cases = (
            (
                StoryRuntime.converse_with_retrieval,
                (
                    "self",
                    "session_id",
                    "conversation_id",
                    "agent_id",
                    "text",
                    "memory_service",
                    "progress_sink",
                    "rag_query",
                ),
                5,
            ),
            (
                RuntimeSession.converse_with_retrieval,
                (
                    "self",
                    "conversation_id",
                    "agent_id",
                    "text",
                    "memory_service",
                    "progress_sink",
                    "rag_query",
                ),
                4,
            ),
        )
        for method, names, positional_count in cases:
            with self.subTest(method=method.__qualname__):
                signature = inspect.signature(method)
                self.assertEqual(tuple(signature.parameters), names)
                parameters = tuple(signature.parameters.values())
                self.assertTrue(
                    all(
                        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                        and parameter.default is inspect.Parameter.empty
                        for parameter in parameters[:positional_count]
                    )
                )
                memory = signature.parameters["memory_service"]
                self.assertIs(memory.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(memory.default, inspect.Parameter.empty)
                progress = signature.parameters["progress_sink"]
                self.assertIs(progress.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIsNone(progress.default)
                rag_query = signature.parameters["rag_query"]
                self.assertIs(rag_query.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIsNone(rag_query.default)
                hints = get_type_hints(method)
                self.assertIs(hints["memory_service"], ConversationMemoryService)
                self.assertEqual(
                    hints["progress_sink"],
                    ConversationRetrievalProgressSink | None,
                )
                self.assertEqual(hints["rag_query"], str | None)
                self.assertIs(hints["return"], ConversationRetrievalResult)

        required = {
            "CONVERSATION_RETRIEVAL_POLICY",
            "ConversationRetrievalAction",
            "ConversationRetrievalActionHandler",
            "ConversationRetrievalActionKind",
            "ConversationRetrievalAnswerAction",
            "ConversationRetrievalObservation",
            "ConversationRetrievalPolicy",
            "ConversationRetrievalProgressEvent",
            "ConversationRetrievalProgressPhase",
            "ConversationRetrievalProgressSink",
            "ConversationRetrievalReadAction",
            "ConversationRetrievalReadObservation",
            "ConversationRetrievalReply",
            "ConversationRetrievalRequest",
            "ConversationRetrievalResult",
            "ConversationRetrievalSearchAction",
            "ConversationRetrievalSearchItem",
            "ConversationRetrievalSearchObservation",
            "ConversationRetrievalSource",
            "ConversationRetrievalTranscriptEvent",
            "ConversationRetrievalError",
            "ConversationRetrievalSemanticError",
            "ConversationRetrievalBudgetError",
            "ConversationRetrievalObserverError",
        }
        self.assertTrue(required.issubset(story_writing_agents.__all__))
        self.assertFalse(
            hasattr(
                story_writing_agents,
                "serialize_conversation_retrieval_observation",
            )
        )
        self.assertNotIn(
            "serialize_conversation_retrieval_observation",
            story_writing_agents.__all__,
        )
        for direct_only in (
            "ConversationRagAttemptOutcome",
            "ConversationRagContext",
            "ConversationRagExcerpt",
            "ConversationRagRuntimeService",
        ):
            self.assertNotIn(direct_only, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, direct_only))

    def test_interactive_parser_exports_use_the_supported_package_facade(
        self,
    ) -> None:
        from story_writing_agents import interactive_commands

        required = {
            "ActionKind",
            "InteractiveAction",
            "CommandSpec",
            "COMMANDS",
            "parse_interactive_line",
        }
        self.assertTrue(required.issubset(story_writing_agents.__all__))
        for name in required:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(story_writing_agents, name),
                    getattr(interactive_commands, name),
                )

    def test_conversation_search_exports_use_the_supported_package_facade(
        self,
    ) -> None:
        from story_writing_agents import conversation_search

        required = set(conversation_search.__all__)
        self.assertTrue(required.issubset(story_writing_agents.__all__))
        for name in required:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(story_writing_agents, name),
                    getattr(conversation_search, name),
                )


class PersistenceValidationTests(unittest.TestCase):
    def test_persisted_debate_log_rejects_wrong_field_types(self) -> None:
        workspace = initialized_workspace(self)
        workspace.write_text(
            "discussion_logs/session_a/debate_a.jsonl",
            json.dumps(
                {
                    "sender": "plot_architect",
                    "phase": "audit",
                    "round": "not-an-integer",
                    "revision": "r0",
                    "kind": "approval",
                    "content": "neutral",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }
            )
            + "\n",
        )
        with self.assertRaisesRegex(WorkspaceFormatError, "line 1"):
            workspace.read_debate_messages("session_a", "debate_a")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        """Install fail-closed native-auth doubles before every CLI invocation."""

        self.provider_auth_factory = mock.Mock(
            side_effect=FailClosedCliAuthService,
        )
        self.auth_coordinator_factory = mock.Mock(
            side_effect=AuthenticatedCliCoordinator,
        )
        self.auth_service_patcher = mock.patch.object(
            cli_module,
            "ProviderAuthService",
            self.provider_auth_factory,
        )
        self.auth_coordinator_patcher = mock.patch.object(
            cli_module,
            "InteractiveAuthCoordinator",
            self.auth_coordinator_factory,
        )
        self.auth_service_patcher.start()
        self.auth_coordinator_patcher.start()
        self.addCleanup(self.auth_coordinator_patcher.stop)
        self.addCleanup(self.auth_service_patcher.stop)

    def test_module_help_lists_public_commands(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        source_path = str(repository_root / "src")
        environment["PYTHONPATH"] = (
            source_path
            if not existing_pythonpath
            else os.pathsep.join((source_path, existing_pythonpath))
        )
        completed = subprocess.run(
            [sys.executable, "-m", "story_writing_agents", "--help"],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("story-writing-agents", completed.stdout)
        for command in (
            "chat",
            "init",
            "directive",
            "run",
            "inspect",
            "rag",
            "session",
            "storage",
            "smoke",
        ):
            with self.subTest(command=command):
                self.assertIn(command, completed.stdout)

    def test_bare_chat_and_lifecycle_parser_shapes_have_help(self) -> None:
        parser = build_parser()
        self.assertEqual(vars(parser.parse_args([])), {"command": None})
        parsed = parser.parse_args(
            [
                "chat",
                "chapter_one",
                "--workspace",
                "/stories/example",
                "--backend",
                "lead_orchestrator=codex",
                "--codex-model",
                "model-name",
                "--antigravity-executable",
                "agy-private",
                "--antigravity-model",
                "agy-model",
                "--antigravity-effort",
                "high",
                "--codex-timeout",
                "12.5",
            ]
        )
        self.assertEqual(
            vars(parsed),
            {
                "command": "chat",
                "session_name": "chapter_one",
                "workspace": "/stories/example",
                "backend": ["lead_orchestrator=codex"],
                "codex_model": "model-name",
                "antigravity_executable": "agy-private",
                "antigravity_model": "agy-model",
                "antigravity_effort": "high",
                "codex_timeout": "12.5",
            },
        )
        run_parsed = parser.parse_args(
            [
                "run",
                "--draft",
                "draft",
                "--output",
                "part-01/draft.md",
            ]
        )
        self.assertEqual(run_parsed.antigravity_executable, "agy")
        self.assertIsNone(run_parsed.antigravity_model)
        self.assertIsNone(run_parsed.antigravity_effort)
        self.assertIsNone(run_parsed.codex_model)
        self.assertEqual(run_parsed.codex_timeout, 300)

        for arguments in (
            ["chat", "--codex-reasoning-effort", "high"],
            [
                "run",
                "--draft",
                "draft",
                "--output",
                "part-01/reasoning.md",
                "--codex-reasoning-effort",
                "high",
            ],
        ):
            stderr = io.StringIO()
            with (
                self.subTest(arguments=arguments),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                parser.parse_args(arguments)
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("unrecognized arguments", stderr.getvalue())

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(["--workspace", "/not-a-bare-option"])
        self.assertEqual(raised.exception.code, 2)

        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        source_path = str(repository_root / "src")
        environment["PYTHONPATH"] = source_path
        for arguments in (
            ["--help"],
            ["chat", "--help"],
            ["run", "--help"],
            ["rag", "--help"],
            ["rag", "status", "--help"],
            ["rag", "enable", "--help"],
            ["rag", "rebuild", "--help"],
            ["rag", "search", "--help"],
            ["rag", "disable", "--help"],
            ["rag", "purge", "--help"],
            ["session", "create", "--help"],
            ["session", "reopen", "--help"],
            ["session", "pause", "--help"],
            ["storage", "--help"],
            ["storage", "migrate", "--help"],
            ["storage", "verify", "--help"],
            ["storage", "use", "--help"],
        ):
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    [sys.executable, "-m", "story_writing_agents", *arguments],
                    cwd=repository_root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("usage:", completed.stdout)
                self.assertNotIn(
                    "--codex-reasoning-effort",
                    completed.stdout,
                )
                if arguments[:1] in (["chat"], ["run"]):
                    for option in (
                        "--antigravity-executable",
                        "--antigravity-model",
                        "--antigravity-effort",
                    ):
                        self.assertIn(option, completed.stdout)

    def test_non_tty_guard_precedes_input_options_workspace_and_backend(self) -> None:
        cases = (
            ([], False, True),
            (["chat", "--codex-timeout", "not-a-number"], True, False),
            (
                [
                    "chat",
                    "target",
                    "--workspace",
                    "/missing",
                    "--backend",
                    "unsafe override",
                ],
                False,
                False,
            ),
        )
        for arguments, stdin_tty, stdout_tty in cases:
            with self.subTest(arguments=arguments):
                stdin = TtyStringIO(is_terminal=stdin_tty, value="/quit\n")
                stdout = TtyStringIO(is_terminal=stdout_tty)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "_parse_backend_overrides",
                        side_effect=AssertionError("parsed overrides before TTY"),
                    ) as parse_overrides,
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        side_effect=AssertionError("resolved workspace before TTY"),
                    ) as resolve_workspace,
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        side_effect=AssertionError("loaded context before TTY"),
                    ) as load_context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    mock.patch.object(
                        cli_module,
                        "build_stream_terminal",
                    ) as terminal_factory,
                    mock.patch.object(
                        cli_module,
                        "InteractiveController",
                    ) as controller,
                ):
                    exit_code = main(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(InteractiveErrorCode.TTY_REQUIRED),
                )
                self.assertEqual(stdin.readline_calls, 0)
                parse_overrides.assert_not_called()
                resolve_workspace.assert_not_called()
                load_context.assert_not_called()
                backend.assert_not_called()
                terminal_factory.assert_not_called()
                controller.assert_not_called()

    def test_noninteractive_pre_workspace_failures_are_exact_and_log_free(
        self,
    ) -> None:
        """Keep discovery, initialization, and argparse failures pre-log."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "missing"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "inspect",
                        "--workspace",
                        str(target),
                        "--session",
                        "pre_workspace",
                    ]
                )
            resolved = target.resolve()
            self.assertEqual(exit_code, 2)
            self.assertEqual(
                stderr.getvalue(),
                "ConfigurationError: workspace is not initialized: "
                f"{resolved}; run 'story init {resolved}'\n",
            )
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            self.assertFalse(target.exists())

            stderr = io.StringIO()
            failure = WorkspaceFormatError("initialization failed")
            with (
                mock.patch.object(
                    cli_module,
                    "initialize_workspace",
                    side_effect=failure,
                ) as initialize,
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(["init", str(target)]), 2)
            self.assertEqual(stderr.getvalue(), "WorkspaceFormatError: initialization failed\n")
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            initialize.assert_called_once_with(
                str(target),
                overwrite_prompts=False,
            )
            self.assertFalse(target.exists())

            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                main(["inspect", "--unknown-option"])
            self.assertEqual(raised.exception.code, 2)
            self.assertTrue(
                stderr.getvalue().endswith(
                    "story-writing-agents: error: unrecognized arguments: "
                    "--unknown-option\n"
                )
            )
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            self.assertFalse(target.exists())

    def test_real_init_symlink_race_is_hint_free_and_never_touches_external(
        self,
    ) -> None:
        """Exercise the real CLI/workspace boundary through a no-follow race."""

        if (
            os.rename not in getattr(os, "supports_dir_fd", ())
            or os.symlink not in getattr(os, "supports_dir_fd", ())
        ):
            self.skipTest("descriptor-relative rename/symlink is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "pending_cli_workspace"
            parked = parent / ".parked-pending-cli-workspace"
            external = parent / "external"
            external.mkdir()
            (external / "sentinel.bin").write_bytes(b"external bytes\x00")
            external_before = {
                path.relative_to(external).as_posix(): (
                    None if path.is_dir() else path.read_bytes()
                )
                for path in sorted(external.rglob("*"))
            }
            real_mkdir = os.mkdir
            substituted = False

            def substitute_final_root(
                path: object,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> None:
                nonlocal substituted
                real_mkdir(path, mode, dir_fd=dir_fd)
                if path != root.name or substituted:
                    return
                substituted = True
                os.rename(
                    root.name,
                    parked.name,
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                )
                os.symlink(
                    external,
                    root.name,
                    target_is_directory=True,
                    dir_fd=dir_fd,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    workspace_module.os,
                    "mkdir",
                    side_effect=substitute_final_root,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(["init", str(root)])

            self.assertEqual(exit_code, 2)
            self.assertTrue(substituted)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(
                stderr.getvalue().startswith(
                    ("NotADirectoryError: ", "OSError: ")
                )
            )
            self.assertNotIn('"workspace"', stdout.getvalue())
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            self.assertTrue(root.is_symlink())
            self.assertTrue(parked.is_dir())
            self.assertEqual(
                {
                    path.relative_to(external).as_posix(): (
                        None if path.is_dir() else path.read_bytes()
                    )
                    for path in sorted(external.rglob("*"))
                },
                external_before,
            )
            self.assertFalse(external.joinpath("debug_logs").exists())

    def test_post_bind_init_replacement_is_hint_free_and_contained(self) -> None:
        """Keep CLI failure exact when the canonical root changes during writes."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "pending_cli_workspace"
            parked = parent / ".parked-pending-cli-workspace"
            external = parent / "external"
            external.mkdir()
            (external / "sentinel.bin").write_bytes(b"external bytes\x00")
            external_before = {
                path.relative_to(external).as_posix(): (
                    None if path.is_dir() else path.read_bytes()
                )
                for path in sorted(external.rglob("*"))
            }
            real_entries = workspace_module._initialize_pending_workspace_entries
            substituted = False

            def substitute_after_bind(
                target_workspace: Workspace,
                root_descriptor: int,
                *,
                overwrite_prompts: bool,
            ) -> None:
                nonlocal substituted
                root.rename(parked)
                try:
                    root.symlink_to(external, target_is_directory=True)
                except (NotImplementedError, OSError) as exc:
                    self.skipTest(
                        f"directory symlink replacement unavailable: {exc}"
                    )
                substituted = True
                real_entries(
                    target_workspace,
                    root_descriptor,
                    overwrite_prompts=overwrite_prompts,
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(
                    workspace_module,
                    "_initialize_pending_workspace_entries",
                    side_effect=substitute_after_bind,
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(["init", str(root)])

            self.assertEqual(exit_code, 2)
            self.assertTrue(substituted)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(
                stderr.getvalue().startswith(
                    ("NotADirectoryError: ", "OSError: ")
                )
            )
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            self.assertTrue(root.is_symlink())
            self.assertTrue(parked.is_dir())
            self.assertEqual(
                {
                    path.relative_to(external).as_posix(): (
                        None if path.is_dir() else path.read_bytes()
                    )
                    for path in sorted(external.rglob("*"))
                },
                external_before,
            )
            self.assertFalse(external.joinpath("debug_logs").exists())
            self.assertFalse(parked.joinpath("debug_logs/runtime.jsonl").exists())

    def test_init_root_failures_keep_codes_and_process_control_semantics(
        self,
    ) -> None:
        """Keep ordinary status two while propagating fatal controls unchanged."""

        failures: tuple[BaseException, ...] = (
            OSError(errno.EIO, "pending root creation failed"),
            KeyboardInterrupt("pending root creation interrupted"),
            SystemExit("pending root creation terminated"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                parent = Path(temporary.name)
                root = parent / "pending_cli_workspace"
                real_mkdir = os.mkdir

                def fail_final_creation(
                    path: object,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> None:
                    if path == root.name:
                        raise failure
                    real_mkdir(path, mode, dir_fd=dir_fd)

                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        workspace_module.os,
                        "mkdir",
                        side_effect=fail_final_creation,
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    if isinstance(failure, Exception):
                        exit_code = main(["init", str(root)])
                    else:
                        try:
                            main(["init", str(root)])
                        except type(failure) as caught:
                            self.assertIs(caught, failure)
                        else:
                            self.fail(
                                f"{type(failure).__name__} did not propagate"
                            )
                        exit_code = None

                if isinstance(failure, Exception):
                    self.assertEqual(exit_code, 2)
                    self.assertEqual(
                        stderr.getvalue(),
                        "OSError: [Errno 5] pending root creation failed\n",
                    )
                else:
                    self.assertIsNone(exit_code)
                    self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
                self.assertFalse(root.exists())

    def test_module_non_tty_entry_is_exact_and_does_not_touch_cwd(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        invocations = (
            [],
            [
                "chat",
                "target",
                "--workspace",
                "missing",
                "--backend",
                "invalid",
                "--codex-timeout",
                "invalid",
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for arguments in invocations:
                with self.subTest(arguments=arguments):
                    before = tuple(root.iterdir())
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "story_writing_agents",
                            *arguments,
                        ],
                        cwd=root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(
                        completed.stderr,
                        expected_interactive_error(
                            InteractiveErrorCode.TTY_REQUIRED
                        ),
                    )
                    self.assertEqual(tuple(root.iterdir()), before)

    def test_interactive_adapter_passes_exact_chat_and_bare_configuration(self) -> None:
        workspace = initialized_workspace(self)
        contexts = (
            (
                [
                    "chat",
                    "chapter_one",
                    "--workspace",
                    str(workspace.root),
                    "--backend",
                    "lead_orchestrator=codex",
                    "--codex-model",
                    "model-name",
                    "--codex-timeout",
                    "12.5",
                ],
                "chapter_one",
                "model-name",
                12.5,
                {"lead_orchestrator": "codex"},
                "agy",
                None,
                None,
            ),
            (
                [
                    "chat",
                    "antigravity-session",
                    "--workspace",
                    str(workspace.root),
                    "--backend",
                    "plot_architect=antigravity",
                    "--antigravity-executable",
                    "private-agy",
                    "--antigravity-model",
                    "agy-model",
                    "--antigravity-effort",
                    "medium",
                ],
                "antigravity-session",
                None,
                300.0,
                {"plot_architect": "antigravity"},
                "private-agy",
                "agy-model",
                "medium",
            ),
            ([], None, None, 300.0, {}, "agy", None, None),
        )
        for (
            arguments,
            requested,
            model,
            timeout,
            overrides,
            antigravity_executable,
            antigravity_model,
            antigravity_effort,
        ) in contexts:
            with self.subTest(arguments=arguments):
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                cli_context = CliContext(default_session_id="saved")
                backend_value = mock.Mock()
                catalog_value = mock.Mock()
                controller_value = mock.Mock()
                controller_value.run_session_lobby.return_value = 7
                main_reader = mock.Mock(return_value="/status")
                terminal_value = InteractiveTerminal(
                    lambda prompt: "unused",
                    lambda text: None,
                    main_read_line=main_reader,
                )
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ) as resolve_workspace,
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        return_value=cli_context,
                    ) as load_context,
                    mock.patch.object(
                        cli_module,
                        "AgentBackend",
                        return_value=backend_value,
                    ) as backend_factory,
                    mock.patch.object(
                        cli_module,
                        "ProviderCatalogService",
                        return_value=catalog_value,
                    ) as catalog_factory,
                    mock.patch.object(
                        cli_module,
                        "build_stream_terminal",
                        return_value=terminal_value,
                    ) as terminal_factory,
                    mock.patch.object(
                        cli_module,
                        "InteractiveController",
                        return_value=controller_value,
                    ) as controller_factory,
                ):
                    self.assertEqual(main(arguments), 7)

                expected_workspace_option = (
                    str(workspace.root) if arguments else None
                )
                resolve_workspace.assert_called_once_with(
                    expected_workspace_option
                )
                load_context.assert_called_once_with(workspace)
                backend_factory.assert_called_once_with(
                    workspace,
                    model=model,
                    timeout_seconds=timeout,
                    backend_overrides=overrides,
                    antigravity_executable=antigravity_executable,
                    antigravity_model=antigravity_model,
                    antigravity_effort=antigravity_effort,
                )
                catalog_factory.assert_called_once_with(
                    workspace.root,
                    antigravity_executable=antigravity_executable,
                )
                terminal_factory.assert_called_once_with(input, stdin, stdout)
                controller_args = controller_factory.call_args.args
                self.assertEqual(
                    controller_args[:3],
                    (workspace, cli_context, backend_value),
                )
                self.assertIsInstance(
                    controller_factory.call_args.kwargs[
                        "conversation_storage_services"
                    ],
                    ConversationStorageServices,
                )
                terminal = controller_args[3]
                self.assertIsInstance(terminal, InteractiveTerminal)
                self.assertIs(
                    controller_factory.call_args.kwargs["provider_catalog"],
                    catalog_value,
                )
                action = terminal.read_main()
                self.assertEqual((action.kind.value, action.text), ("line", "/status"))
                main_reader.assert_called_once_with(
                    "story[lobby]> "
                )
                self.assertEqual(stdin.readline_calls, 0)
                controller_value.run_session_lobby.assert_called_once_with(
                    requested_session=requested
                )
                backend_value.close.assert_not_called()
                catalog_value.discover.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")

    def test_interactive_startup_orders_every_gate_before_runtime_artifacts(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        events: list[tuple[str, object | None]] = []
        both = authenticated_provider_snapshot()
        coordinator = ScriptedCliCoordinator(
            events,
            snapshots=(both, both),
        )
        auth_service = object()
        catalog_service = object()
        terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)

        async def observe_startup_progress(
            operation: Awaitable[object],
        ) -> object:
            """Record both startup progress spans around their real work."""

            events.append(("progress_start", None))
            try:
                return await operation
            finally:
                events.append(("progress_end", None))

        terminal._advisory_progress = observe_startup_progress
        backend = mock.Mock()
        controller = mock.Mock()
        controller.run_session_lobby.return_value = 7
        stdin = TtyStringIO(is_terminal=True)
        stdout = TtyStringIO(is_terminal=True)
        stderr = io.StringIO()

        original_validate_executable = cli_module._validate_antigravity_executable
        original_establish = cli_module.establish_default_backend
        original_load_snapshot = (
            cli_module.ConfigManager.load_configuration_snapshot
        )
        original_routes = cli_module._effective_interactive_providers
        original_activate = cli_module.activate_configured_conversation_storage
        storage_startups: list[ConversationStorageStartup] = []

        def validate_executable(value: object) -> str:
            events.append(("validation", value))
            return original_validate_executable(value)

        def establish(current_workspace: Workspace, provider: str) -> bool:
            events.append(("default", provider))
            return original_establish(current_workspace, provider)

        def load_snapshot(manager: object) -> object:
            events.append(("snapshot", None))
            return original_load_snapshot(manager)  # type: ignore[arg-type]

        def routes(*args: object, **kwargs: object) -> tuple[str, ...]:
            events.append(("routes", None))
            return original_routes(*args, **kwargs)  # type: ignore[arg-type]

        def activate(current_workspace: Workspace) -> ConversationStorageStartup:
            events.append(("storage", None))
            startup = original_activate(current_workspace)
            storage_startups.append(startup)
            return startup

        def controller_factory(*args: object, **kwargs: object) -> object:
            events.append(("controller", None))
            self.assertIs(kwargs["auth_coordinator"], coordinator)
            self.assertIs(
                kwargs["conversation_storage_services"],
                storage_startups[0].services,
            )
            self.assertIs(kwargs["provider_catalog"], catalog_service)
            return controller

        def run_lobby(*, requested_session: str | None = None) -> int:
            events.append(("lobby", requested_session))
            return 7

        controller.run_session_lobby.side_effect = run_lobby

        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(
                cli_module,
                "_validate_antigravity_executable",
                side_effect=validate_executable,
            ),
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                side_effect=lambda option: (
                    events.append(("workspace", option)) or workspace
                ),
            ),
            mock.patch.object(
                cli_module,
                "activate_configured_conversation_storage",
                side_effect=activate,
            ),
            mock.patch.object(
                cli_module,
                "build_stream_terminal",
                side_effect=lambda *args: (
                    events.append(("terminal", None)) or terminal
                ),
            ),
            mock.patch.object(
                cli_module,
                "ProviderAuthService",
                side_effect=lambda *args, **kwargs: (
                    events.append(("auth_service", args[0])) or auth_service
                ),
            ),
            mock.patch.object(
                cli_module,
                "ProviderCatalogService",
                side_effect=lambda *args, **kwargs: (
                    self.assertEqual(args, (workspace.root,))
                    or self.assertEqual(
                        kwargs,
                        {"antigravity_executable": "agy"},
                    )
                    or events.append(("catalog_service", args[0]))
                    or catalog_service
                ),
            ),
            mock.patch.object(
                cli_module,
                "InteractiveAuthCoordinator",
                side_effect=lambda service, current_terminal: (
                    self.assertIs(service, auth_service)
                    or self.assertIs(current_terminal, terminal)
                    or events.append(("auth_coordinator", None))
                    or coordinator
                ),
            ),
            mock.patch.object(
                cli_module,
                "establish_default_backend",
                side_effect=establish,
            ),
            mock.patch.object(
                cli_module.ConfigManager,
                "load_configuration_snapshot",
                autospec=True,
                side_effect=load_snapshot,
            ),
            mock.patch.object(
                cli_module,
                "_effective_interactive_providers",
                side_effect=routes,
            ),
            mock.patch.object(
                cli_module,
                "load_cli_context",
                side_effect=lambda current: (
                    events.append(("context", None)) or CliContext()
                ),
            ),
            mock.patch.object(
                cli_module,
                "AgentBackend",
                side_effect=lambda *args, **kwargs: (
                    events.append(("backend", None)) or backend
                ),
            ),
            mock.patch.object(
                cli_module,
                "InteractiveController",
                side_effect=controller_factory,
            ),
        ):
            self.assertEqual(
                main(
                    [
                        "chat",
                        "chapter_one",
                        "--workspace",
                        str(workspace.root),
                    ]
                ),
                7,
            )

        self.assertEqual(
            [name for name, _value in events],
            [
                "validation",
                "workspace",
                "terminal",
                "progress_start",
                "storage",
                "snapshot",
                "progress_end",
                "auth_service",
                "catalog_service",
                "auth_coordinator",
                "welcome",
                "probe_all",
                "default",
                "snapshot",
                "routes",
                "probe_all",
                "progress_start",
                "context",
                "backend",
                "controller",
                "progress_end",
                "lobby",
            ],
        )
        self.assertEqual(events[7][1], workspace.root)
        self.assertEqual(events[8][1], workspace.root)
        backend.close.assert_not_called()
        self.assertEqual(stderr.getvalue(), "")

    def test_desired_sqlite_startup_failures_are_provider_free_and_preserving(
        self,
    ) -> None:
        custom_summary = "Conversation storage activation could not be completed."
        custom_hint = (
            "Run story storage use, migrate, or verify as appropriate, then "
            "retry after concurrent workspace storage work finishes."
        )
        cases = (
            (
                "missing_database",
                False,
                False,
                InteractiveErrorCode.CONFIGURATION_INVALID,
                True,
            ),
            (
                "runtime_unavailable",
                False,
                True,
                InteractiveErrorCode.CONFIGURATION_INVALID,
                True,
            ),
            (
                "invalid_database",
                True,
                False,
                InteractiveErrorCode.WORKSPACE_INVALID,
                False,
            ),
        )
        for (
            name,
            invalid_database,
            runtime_unavailable,
            expected_code,
            custom_copy,
        ) in cases:
            with self.subTest(case=name):
                workspace = initialized_workspace(self)
                database = database_path(workspace)
                if invalid_database:
                    database.write_bytes(b"invalid-database-private-secret")
                system = workspace.read_json("config/system_defaults.json")
                system["conversation_storage_backend"] = "sqlite"
                workspace.write_json("config/system_defaults.json", system)

                configuration_paths = (
                    workspace.path("config/system_defaults.json"),
                    workspace.path("config/agent_configs.json"),
                )
                configuration_before = tuple(
                    path.read_bytes() for path in configuration_paths
                )
                database_before = database.read_bytes() if database.exists() else None
                activation_path = workspace.path(
                    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH
                )
                activation_before = (
                    activation_path.read_bytes() if activation_path.exists() else None
                )

                unavailable_preflight = ConversationStoragePreflight(
                    ConversationStorageBackend.SQLITE,
                    ConversationStorageCapability.UNAVAILABLE,
                )
                runtime_probe_context = (
                    mock.patch.object(
                        storage_selection_module,
                        "probe_sqlite_conversation_storage_runtime",
                        return_value=unavailable_preflight,
                    )
                    if runtime_unavailable
                    else contextlib.nullcontext(None)
                )
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                terminal = InteractiveTerminal(
                    lambda _prompt: "",
                    lambda _text: None,
                )
                self.provider_auth_factory.reset_mock()
                self.auth_coordinator_factory.reset_mock()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    runtime_probe_context as runtime_probe,
                    mock.patch.object(
                        cli_module,
                        "build_stream_terminal",
                        return_value=terminal,
                    ) as terminal_factory,
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    mock.patch.object(
                        cli_module,
                        "InteractiveController",
                    ) as controller,
                ):
                    self.assertEqual(
                        main(["chat", "--workspace", str(workspace.root)]),
                        2,
                    )

                if runtime_probe is not None:
                    runtime_probe.assert_called_once_with()
                terminal_factory.assert_called_once_with(input, stdin, stdout)
                self.provider_auth_factory.assert_not_called()
                self.auth_coordinator_factory.assert_not_called()
                context.assert_not_called()
                backend.assert_not_called()
                controller.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(
                        expected_code,
                        debug_log_available=True,
                        summary=custom_summary if custom_copy else None,
                        hint=custom_hint if custom_copy else None,
                    ),
                )
                self.assertNotIn("private-secret", stderr.getvalue())
                self.assertEqual(
                    tuple(path.read_bytes() for path in configuration_paths),
                    configuration_before,
                )
                self.assertEqual(
                    database.read_bytes() if database.exists() else None,
                    database_before,
                )
                self.assertEqual(
                    activation_path.read_bytes() if activation_path.exists() else None,
                    activation_before,
                )
                self.assertEqual(
                    workspace.read_json("config/system_defaults.json")[
                        "conversation_storage_backend"
                    ],
                    "sqlite",
                )

    def test_startup_indicator_failures_use_the_internal_error_path(self) -> None:
        """Keep terminal-only startup failures distinct from domain failures."""

        workspace = initialized_workspace(self)
        for failing_span in (1, 2):
            with self.subTest(failing_span=failing_span):
                calls = 0

                def run_progress(
                    terminal: InteractiveTerminal,
                    operation: Callable[[], object],
                ) -> object:
                    nonlocal calls
                    self.assertIsInstance(terminal, InteractiveTerminal)
                    calls += 1
                    if calls == failing_span:
                        raise cli_module.AdvisoryProgressError("progress-secret")
                    return operation()

                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "_run_interactive_startup_progress",
                        side_effect=run_progress,
                    ),
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    mock.patch.object(
                        cli_module,
                        "InteractiveController",
                    ) as controller,
                ):
                    self.assertEqual(main(["chat"]), 2)

                self.assertEqual(calls, failing_span)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(
                        InteractiveErrorCode.INTERNAL_ERROR,
                        debug_log_available=True,
                    ),
                )
                self.assertNotIn("progress-secret", stderr.getvalue())
                context.assert_not_called()
                backend.assert_not_called()
                controller.assert_not_called()

    def test_every_interactive_argument_failure_precedes_workspace_and_auth(
        self,
    ) -> None:
        cases = (
            ["chat", "--backend", "invalid-secret"],
            ["chat", "--codex-timeout", "0"],
            ["chat", "bad/name"],
            ["chat", "--codex-model", "bad\x00model"],
            ["chat", "--antigravity-executable", ""],
            ["chat", "--antigravity-model", "bad\x00model"],
            ["chat", "--antigravity-effort", "xhigh"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(cli_module, "resolve_workspace") as resolve,
                    mock.patch.object(cli_module, "ProviderAuthService") as auth,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                ):
                    self.assertEqual(main(arguments), 2)
                resolve.assert_not_called()
                auth.assert_not_called()
                backend.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("secret", stderr.getvalue())

    def test_auth_controls_exit_without_context_backend_or_session_artifacts(
        self,
    ) -> None:
        for kind in (
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        ):
            with self.subTest(kind=kind):
                workspace = initialized_workspace(self)
                system_before = workspace.path(
                    "config/system_defaults.json"
                ).read_bytes()
                agent_before = workspace.path(
                    "config/agent_configs.json"
                ).read_bytes()
                none = provider_snapshot(
                    ProviderAuthState.UNAUTHENTICATED,
                    ProviderAuthState.UNAVAILABLE,
                )
                events: list[tuple[str, object | None]] = []
                coordinator = ScriptedCliCoordinator(
                    events,
                    snapshots=(none,),
                    results=(InteractiveAuthResult(kind, none),),
                )
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "build_stream_terminal",
                        return_value=terminal,
                    ),
                    mock.patch.object(
                        cli_module,
                        "ProviderAuthService",
                        return_value=object(),
                    ),
                    mock.patch.object(
                        cli_module,
                        "InteractiveAuthCoordinator",
                        return_value=coordinator,
                    ),
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    mock.patch.object(
                        cli_module,
                        "InteractiveController",
                    ) as controller,
                ):
                    self.assertEqual(main(["chat"]), 0)
                context.assert_not_called()
                backend.assert_not_called()
                controller.assert_not_called()
                self.assertEqual(
                    workspace.path("config/system_defaults.json").read_bytes(),
                    system_before,
                )
                self.assertEqual(
                    workspace.path("config/agent_configs.json").read_bytes(),
                    agent_before,
                )
                self.assertFalse(
                    tuple(
                        workspace.path(
                            "agent_states/orchestrator_state/provider_sessions"
                        ).glob("*.json")
                    )
                )
                self.assertEqual(
                    [name for name, _value in events],
                    ["welcome", "probe_all", "run"],
                )

    def test_storage_activation_cas_remains_after_later_provider_interrupt(self) -> None:
        workspace = initialized_workspace(self)
        provision(workspace)
        system = workspace.read_json("config/system_defaults.json")
        system["conversation_storage_backend"] = "sqlite"
        workspace.write_json("config/system_defaults.json", system)
        marker = workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        self.assertFalse(marker.exists())
        none = provider_snapshot(
            ProviderAuthState.UNAUTHENTICATED,
            ProviderAuthState.UNAVAILABLE,
        )
        coordinator = ScriptedCliCoordinator(
            [],
            snapshots=(none,),
            results=(
                InteractiveAuthResult(
                    InteractiveAuthResultKind.INTERRUPT,
                    none,
                ),
            ),
        )
        stdin = TtyStringIO(is_terminal=True)
        stdout = TtyStringIO(is_terminal=True)
        stderr = io.StringIO()
        terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                return_value=workspace,
            ),
            mock.patch.object(
                cli_module,
                "build_stream_terminal",
                return_value=terminal,
            ),
            mock.patch.object(
                cli_module,
                "ProviderAuthService",
                return_value=object(),
            ),
            mock.patch.object(
                cli_module,
                "InteractiveAuthCoordinator",
                return_value=coordinator,
            ),
            mock.patch.object(cli_module, "AgentBackend") as backend,
            mock.patch.object(cli_module, "InteractiveController") as controller,
        ):
            self.assertEqual(
                main(["chat", "--workspace", str(workspace.root)]),
                0,
            )
        backend.assert_not_called()
        controller.assert_not_called()
        activation = load_conversation_storage_activation_state(workspace)
        self.assertIs(activation.backend, ConversationStorageBackend.SQLITE)
        self.assertEqual(activation.generation, 1)
        self.assertTrue(marker.is_file())

    def test_mixed_effective_routes_are_freshly_gated_once_before_backend(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        system = workspace.read_json("config/system_defaults.json")
        system["default_backend"] = "codex"
        workspace.write_json("config/system_defaults.json", system)
        agents = workspace.read_json("config/agent_configs.json")
        agents[PLOT_ARCHITECT]["backend"] = "antigravity"
        workspace.write_json("config/agent_configs.json", agents)
        both = authenticated_provider_snapshot()
        codex_only = provider_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        events: list[tuple[str, object | None]] = []
        coordinator = ScriptedCliCoordinator(
            events,
            snapshots=(both, codex_only, both),
            results=(provider_result(both, "antigravity"),),
        )
        controller = mock.Mock()
        controller.run_session_lobby.return_value = 0
        terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)
        stdin = TtyStringIO(is_terminal=True)
        stdout = TtyStringIO(is_terminal=True)
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(cli_module, "resolve_workspace", return_value=workspace),
            mock.patch.object(
                cli_module,
                "build_stream_terminal",
                return_value=terminal,
            ),
            mock.patch.object(cli_module, "ProviderAuthService", return_value=object()),
            mock.patch.object(
                cli_module,
                "InteractiveAuthCoordinator",
                return_value=coordinator,
            ),
            mock.patch.object(
                cli_module,
                "load_cli_context",
                side_effect=lambda current: (
                    events.append(("context", None)) or CliContext()
                ),
            ),
            mock.patch.object(
                cli_module,
                "AgentBackend",
                side_effect=lambda *args, **kwargs: (
                    events.append(("backend", None)) or mock.Mock()
                ),
            ),
            mock.patch.object(
                cli_module,
                "InteractiveController",
                return_value=controller,
            ),
        ):
            self.assertEqual(main(["chat", "--workspace", str(workspace.root)]), 0)
        self.assertEqual(
            events,
            [
                ("welcome", None),
                ("probe_all", None),
                ("probe_all", None),
                ("run", "antigravity"),
                ("probe_all", None),
                ("context", None),
                ("backend", None),
            ],
        )

    def test_noninteractive_and_non_tty_forms_never_construct_auth(self) -> None:
        provider_catalog_factory = mock.Mock()
        provider_catalog_patcher = mock.patch.object(
            cli_module,
            "ProviderCatalogService",
            provider_catalog_factory,
        )
        provider_catalog_patcher.start()
        self.addCleanup(provider_catalog_patcher.stop)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_path = root / "workspace"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["init", str(workspace_path)]), 0)
            self.provider_auth_factory.assert_not_called()
            self.auth_coordinator_factory.assert_not_called()
            provider_catalog_factory.assert_not_called()

            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "run",
                            "--workspace",
                            str(workspace_path),
                            "--output",
                            "part-01/no-auth.md",
                        ]
                    ),
                    2,
                )
            self.provider_auth_factory.assert_not_called()
            self.auth_coordinator_factory.assert_not_called()
            provider_catalog_factory.assert_not_called()

        stdin = TtyStringIO(is_terminal=False)
        stdout = TtyStringIO(is_terminal=True)
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
        ):
            self.assertEqual(main([]), 2)
        self.provider_auth_factory.assert_not_called()
        provider_catalog_factory.assert_not_called()
        self.auth_coordinator_factory.assert_not_called()

    def test_run_passes_separate_antigravity_options_without_provider_work(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        context = CliContext(default_session_id="run-session")
        backend = mock.Mock()
        initial_runtime = mock.Mock()
        provider_runtime = mock.Mock()
        result = mock.Mock(status=DebateStatus.CONSENSUS)
        result.to_dict.return_value = {"status": "consensus"}
        provider_runtime.run_draft = mock.AsyncMock(return_value=result)
        stdout = io.StringIO()

        with (
            contextlib.redirect_stdout(stdout),
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                return_value=workspace,
            ),
            mock.patch.object(
                cli_module,
                "load_cli_context",
                return_value=context,
            ),
            mock.patch.object(
                cli_module,
                "resolve_session",
                return_value="run-session",
            ),
            mock.patch.object(
                cli_module,
                "AgentBackend",
                return_value=backend,
            ) as backend_factory,
            mock.patch.object(
                cli_module,
                "StoryRuntime",
                side_effect=(initial_runtime, provider_runtime),
            ) as runtime_factory,
        ):
            exit_code = main(
                [
                    "run",
                    "--workspace",
                    str(workspace.root),
                    "--session",
                    "run-session",
                    "--backend",
                    "plot_architect=antigravity",
                    "--draft",
                    "draft",
                    "--output",
                    "part-01/antigravity.md",
                    "--antigravity-executable",
                    "private-agy",
                    "--antigravity-model",
                    "agy-model",
                    "--antigravity-effort",
                    "low",
                ]
            )

        self.assertEqual(exit_code, 0)
        backend_factory.assert_called_once_with(
            workspace,
            model=None,
            timeout_seconds=300,
            backend_overrides={"plot_architect": "antigravity"},
            antigravity_executable="private-agy",
            antigravity_model="agy-model",
            antigravity_effort="low",
        )
        self.assertEqual(
            runtime_factory.call_args_list,
            [mock.call(workspace), mock.call(workspace, backend)],
        )
        provider_runtime.run_draft.assert_awaited_once_with(
            "run-session",
            "draft",
            output_path="part-01/antigravity.md",
            max_debate_rounds=None,
            debate_id=None,
        )
        backend.close.assert_called_once_with()

    def test_backend_cleanup_ownership_transfers_only_when_run_begins(self) -> None:
        workspace = initialized_workspace(self)
        context = CliContext()

        def invoke_with(
            backend_value: mock.Mock,
            controller_factory: mock.Mock,
        ) -> tuple[int, str]:
            """Invoke a TTY chat with supplied backend/controller doubles."""

            stdin = TtyStringIO(is_terminal=True)
            stdout = TtyStringIO(is_terminal=True)
            stderr = io.StringIO()
            with (
                mock.patch.object(sys, "stdin", stdin),
                mock.patch.object(sys, "stdout", stdout),
                mock.patch.object(sys, "stderr", stderr),
                mock.patch.object(
                    cli_module,
                    "resolve_workspace",
                    return_value=workspace,
                ),
                mock.patch.object(
                    cli_module,
                    "load_cli_context",
                    return_value=context,
                ),
                mock.patch.object(
                    cli_module,
                    "AgentBackend",
                    return_value=backend_value,
                ),
                mock.patch.object(
                    cli_module,
                    "InteractiveController",
                    controller_factory,
                ),
            ):
                return main(["chat"]), stderr.getvalue()

        unstarted_backend = mock.Mock()
        constructor_failure = mock.Mock(
            side_effect=RuntimeError("constructor-secret")
        )
        exit_code, stderr = invoke_with(
            unstarted_backend,
            constructor_failure,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr,
            expected_interactive_error(
                InteractiveErrorCode.INTERNAL_ERROR,
                debug_log_available=True,
            ),
        )
        self.assertNotIn("constructor-secret", stderr)
        unstarted_backend.close.assert_called_once_with()

        transferred_backend = mock.Mock()
        running_controller = mock.Mock()
        running_controller.run_session_lobby.side_effect = RuntimeError(
            "controller-secret"
        )
        exit_code, stderr = invoke_with(
            transferred_backend,
            mock.Mock(return_value=running_controller),
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr,
            expected_interactive_error(
                InteractiveErrorCode.INTERNAL_ERROR,
                debug_log_available=True,
            ),
        )
        self.assertNotIn("controller-secret", stderr)
        transferred_backend.close.assert_not_called()

    def test_setup_failures_are_phase_typed_and_never_expose_prose(self) -> None:
        workspace = initialized_workspace(self)
        workspace_cases = (
            (
                ConfigurationError("workspace-secret"),
                InteractiveErrorCode.WORKSPACE_NOT_INITIALIZED,
            ),
            (
                WorkspaceSecurityError("workspace-secret"),
                InteractiveErrorCode.WORKSPACE_UNSAFE,
            ),
            (
                WorkspaceFormatError("workspace-secret"),
                InteractiveErrorCode.WORKSPACE_INVALID,
            ),
            (OSError("workspace-secret"), InteractiveErrorCode.WORKSPACE_INVALID),
            (RuntimeError("workspace-secret"), InteractiveErrorCode.INTERNAL_ERROR),
        )
        for failure, code in workspace_cases:
            with self.subTest(phase="workspace", failure=type(failure).__name__):
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        side_effect=failure,
                    ),
                ):
                    self.assertEqual(main(["chat"]), 2)
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(code),
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("workspace-secret", stderr.getvalue())

        context_cases = (
            (ConfigurationError, InteractiveErrorCode.CONFIGURATION_INVALID),
            (SessionIdError, InteractiveErrorCode.CONFIGURATION_INVALID),
            (BackendProtocolError, InteractiveErrorCode.CONFIGURATION_INVALID),
            (WorkspaceSecurityError, InteractiveErrorCode.WORKSPACE_UNSAFE),
            (WorkspaceFormatError, InteractiveErrorCode.WORKSPACE_INVALID),
            (OSError, InteractiveErrorCode.WORKSPACE_INVALID),
            (RuntimeError, InteractiveErrorCode.INTERNAL_ERROR),
        )
        for failure_type, code in context_cases:
            with self.subTest(phase="context", failure=failure_type.__name__):
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        side_effect=failure_type("context-secret"),
                    ),
                ):
                    self.assertEqual(main(["chat"]), 2)
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(code, debug_log_available=True),
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("context-secret", stderr.getvalue())

        stdin = TtyStringIO(is_terminal=True)
        stdout = TtyStringIO(is_terminal=True)
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                return_value=workspace,
            ),
            mock.patch.object(
                cli_module,
                "load_cli_context",
                return_value=CliContext(),
            ),
            mock.patch.object(
                cli_module,
                "build_stream_terminal",
                side_effect=RuntimeError("terminal-constructor-secret"),
            ),
            mock.patch.object(cli_module, "AgentBackend") as backend,
        ):
            self.assertEqual(main(["chat"]), 2)
        self.assertEqual(
            stderr.getvalue(),
            expected_interactive_error(
                InteractiveErrorCode.INTERNAL_ERROR,
                debug_log_available=True,
            ),
        )
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("terminal-constructor-secret", stderr.getvalue())
        backend.assert_not_called()

        backend_cases = (
            (ConfigurationError, InteractiveErrorCode.CONFIGURATION_INVALID),
            (WorkspaceSecurityError, InteractiveErrorCode.WORKSPACE_UNSAFE),
            (OSError, InteractiveErrorCode.WORKSPACE_INVALID),
            (RuntimeError, InteractiveErrorCode.INTERNAL_ERROR),
        )
        for failure_type, code in backend_cases:
            with self.subTest(phase="backend", failure=failure_type.__name__):
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        return_value=CliContext(),
                    ),
                    mock.patch.object(
                        cli_module,
                        "AgentBackend",
                        side_effect=failure_type("backend-secret"),
                    ),
                ):
                    self.assertEqual(main(["chat"]), 2)
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(code, debug_log_available=True),
                )
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn("backend-secret", stderr.getvalue())

        for arguments in (
            ["chat", "--backend", "invalid-secret"],
            ["chat", "--codex-timeout", "nan-secret"],
        ):
            with self.subTest(phase="options", arguments=arguments):
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                    ) as resolve_workspace,
                ):
                    self.assertEqual(main(arguments), 2)
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(
                        InteractiveErrorCode.CONFIGURATION_INVALID
                    ),
                )
                self.assertEqual(stdout.getvalue(), "")
                resolve_workspace.assert_not_called()

    def test_changed_cli_callables_have_specification_docstrings(self) -> None:
        names = (
            "build_parser",
            "_parse_backend_overrides",
            "_parse_positive_timeout_seconds",
            "_resolve_run_input",
            "_interactive_streams_are_terminals",
            "_reject_unexpected_startup_input",
            "_write_interactive_cli_error",
            "_classify_workspace_entry_failure",
            "_classify_interactive_configuration_failure",
            "_close_unstarted_interactive_backend",
            "_run_interactive_startup_progress",
            "_run_interactive_cli",
            "main",
        )
        for name in names:
            with self.subTest(callable=name):
                value = getattr(cli_module, name)
                self.assertTrue(inspect.getdoc(value))

    def test_interactive_logger_acquisition_controls_are_optional(self) -> None:
        """Continue TTY setup and retain post-workspace hints without a logger."""

        for control_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(control=control_type.__name__, outcome="success"):
                workspace = initialized_workspace(self)
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                backend = mock.Mock()
                controller = mock.Mock()
                controller.run_session_lobby.return_value = 0
                terminal = InteractiveTerminal(
                    lambda _prompt: "",
                    lambda _text: None,
                )
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "WorkspaceDebugLog",
                        side_effect=control_type("logger acquisition"),
                    ) as logger,
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        return_value=CliContext(),
                    ) as load_context,
                    mock.patch.object(
                        cli_module,
                        "build_stream_terminal",
                        return_value=terminal,
                    ) as build_terminal,
                    mock.patch.object(
                        cli_module,
                        "AgentBackend",
                        return_value=backend,
                    ) as backend_factory,
                    mock.patch.object(
                        cli_module,
                        "InteractiveController",
                        return_value=controller,
                    ) as controller_factory,
                ):
                    exit_code = main(["chat"])

                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                logger.assert_called_once_with(workspace)
                load_context.assert_called_once_with(workspace)
                build_terminal.assert_called_once_with(input, stdin, stdout)
                backend_factory.assert_called_once()
                controller_factory.assert_called_once()
                controller.run_session_lobby.assert_called_once_with(
                    requested_session=None
                )
                backend.close.assert_not_called()

            with self.subTest(control=control_type.__name__, outcome="failure"):
                workspace = initialized_workspace(self)
                stdin = TtyStringIO(is_terminal=True)
                stdout = TtyStringIO(is_terminal=True)
                stderr = io.StringIO()
                failure = ConfigurationError("PRIVATE_POST_WORKSPACE_FAILURE")
                with (
                    mock.patch.object(sys, "stdin", stdin),
                    mock.patch.object(sys, "stdout", stdout),
                    mock.patch.object(sys, "stderr", stderr),
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "WorkspaceDebugLog",
                        side_effect=control_type("logger acquisition"),
                    ) as logger,
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        side_effect=failure,
                    ) as load_context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                ):
                    exit_code = main(["chat"])

                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    expected_interactive_error(
                        InteractiveErrorCode.CONFIGURATION_INVALID,
                        debug_log_available=True,
                    ),
                )
                self.assertNotIn(str(failure), stderr.getvalue())
                logger.assert_called_once_with(workspace)
                load_context.assert_called_once_with(workspace)
                backend.assert_not_called()

    def test_init_logger_acquisition_controls_preserve_exact_success(self) -> None:
        """Keep initialized state and output when its optional logger is absent."""

        with tempfile.TemporaryDirectory() as temporary:
            for control_type in (KeyboardInterrupt, SystemExit):
                with self.subTest(control=control_type.__name__):
                    root = Path(temporary) / control_type.__name__
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            cli_module,
                            "WorkspaceDebugLog",
                            side_effect=control_type("logger acquisition"),
                        ) as logger,
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        exit_code = main(["init", str(root)])

                    self.assertEqual(exit_code, 0)
                    self.assertEqual(stderr.getvalue(), "")
                    self.assertEqual(
                        stdout.getvalue(),
                        json.dumps({"workspace": str(root.resolve())}, indent=2)
                        + "\n",
                    )
                    logger.assert_called_once()
                    logged_workspace = logger.call_args.args[0]
                    self.assertIs(type(logged_workspace), Workspace)
                    self.assertEqual(logged_workspace.root, root.resolve())
                    self.assertTrue(
                        root.joinpath("config/system_defaults.json").is_file()
                    )

    def test_ordinary_logger_acquisition_controls_preserve_output_and_hint(
        self,
    ) -> None:
        """Keep ordinary success and fixed later-failure output without a logger."""

        for control_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(control=control_type.__name__, outcome="success"):
                workspace = initialized_workspace(self)
                stdout = io.StringIO()
                stderr = io.StringIO()
                expected = {
                    "debates": [],
                    "overrides": {"agents": {}, "language": None},
                    "session_id": "logger_optional",
                }
                with (
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "WorkspaceDebugLog",
                        side_effect=control_type("logger acquisition"),
                    ) as logger,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    exit_code = main(
                        [
                            "inspect",
                            "--workspace",
                            str(workspace.root),
                            "--session",
                            "logger_optional",
                        ]
                    )

                self.assertEqual(exit_code, 0)
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(
                    stdout.getvalue(),
                    json.dumps(
                        expected,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
                logger.assert_called_once_with(workspace)

            with self.subTest(control=control_type.__name__, outcome="failure"):
                workspace = initialized_workspace(self)
                stdout = io.StringIO()
                stderr = io.StringIO()
                failure = ConfigurationError("PRIVATE_POST_WORKSPACE_FAILURE")
                with (
                    mock.patch.object(
                        cli_module,
                        "resolve_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(
                        cli_module,
                        "WorkspaceDebugLog",
                        side_effect=control_type("logger acquisition"),
                    ) as logger,
                    mock.patch.object(
                        cli_module,
                        "load_cli_context",
                        side_effect=failure,
                    ) as load_context,
                    mock.patch.object(cli_module, "StoryRuntime") as runtime,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    exit_code = main(
                        [
                            "inspect",
                            "--workspace",
                            str(workspace.root),
                            "--session",
                            "logger_optional",
                        ]
                    )

                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(
                    stderr.getvalue(),
                    "ConfigurationError: PRIVATE_POST_WORKSPACE_FAILURE\n"
                    "Debug log (if available, workspace-relative): "
                    "debug_logs/runtime.jsonl\n",
                )
                logger.assert_called_once_with(workspace)
                load_context.assert_called_once_with(workspace)
                runtime.assert_not_called()

    def test_diagnostic_failure_preserves_noninteractive_exit_and_output(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        stderr = io.StringIO()
        with (
            mock.patch.object(
                cli_module.WorkspaceDebugLog,
                "error",
                side_effect=SystemExit("diagnostic failure"),
            ) as debug_error,
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "directive",
                    "--workspace",
                    str(workspace.root),
                    "--session",
                    "cli_session",
                    "not a directive",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr.getvalue(),
            "DirectiveError: message is not an exact standalone directive\n"
            "Debug log (if available, workspace-relative): "
            "debug_logs/runtime.jsonl\n",
        )
        self.assertTrue(debug_error.called)

    def test_trace_failure_preserves_noninteractive_success_output_and_exit(
        self,
    ) -> None:
        """Keep a valid read-only command independent of its trace sink."""

        workspace = initialized_workspace(self)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                WorkspaceDebugLog,
                "trace",
                side_effect=SystemExit("diagnostic trace failure"),
            ) as trace,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "inspect",
                    "--workspace",
                    str(workspace.root),
                    "--session",
                    "trace_success",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "debates": [],
                "overrides": {"agents": {}, "language": None},
                "session_id": "trace_success",
            },
        )
        self.assertGreater(trace.call_count, 0)

    def test_noninteractive_success_records_fixed_cli_and_runtime_operations(
        self,
    ) -> None:
        """Persist the exact post-workspace CLI and inspection correlation flow."""

        workspace = initialized_workspace(self)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "inspect",
                    "--workspace",
                    str(workspace.root),
                    "--session",
                    "record_success",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["session_id"], "record_success")
        records = [
            json.loads(line)
            for line in workspace.path(
                "debug_logs/runtime.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        projected = [
            (
                record["component"],
                record["operation"],
                record["phase"],
                record.get("outcome"),
                record.get("session_id"),
            )
            for record in records
        ]
        self.assertEqual(
            projected,
            [
                ("cli", "workspace_discovery", "succeeded", "ready", None),
                ("cli", "cli_command", "started", None, None),
                ("cli", "cli_context", "succeeded", "completed", None),
                (
                    "runtime",
                    "inspection",
                    "observed",
                    "completed",
                    "record_success",
                ),
                (
                    "cli",
                    "cli_command",
                    "succeeded",
                    "completed",
                    "record_success",
                ),
            ],
        )

    def test_init_inspect_and_expected_error_exit_codes(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace_path = Path(temporary.name) / "workspace"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["init", str(workspace_path)])
        self.assertEqual(exit_code, 0)
        initialized = json.loads(stdout.getvalue())
        self.assertEqual(Path(initialized["workspace"]), workspace_path.resolve())
        self.assertTrue((workspace_path / "config" / "system_defaults.json").is_file())

        workspace = Workspace(workspace_path)
        prompt_path = AGENT_SPECS[PLOT_ARCHITECT].system_prompt_file
        workspace.write_text(prompt_path, "custom prompt\n")
        workspace.path("core_documents/global_outline.md").write_text(
            "preserved outline\n",
            encoding="utf-8",
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "init",
                    str(workspace_path),
                    "--overwrite-prompts",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            workspace.read_text(prompt_path),
            DEFAULT_AGENT_PROMPTS[PLOT_ARCHITECT],
        )
        self.assertEqual(
            workspace.read_text("core_documents/global_outline.md"),
            "preserved outline\n",
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "inspect",
                    "--workspace",
                    str(workspace_path),
                    "--session",
                    "cli_session",
                ]
            )
        self.assertEqual(exit_code, 0)
        inspected = json.loads(stdout.getvalue())
        self.assertEqual(inspected["session_id"], "cli_session")
        self.assertEqual(inspected["debates"], [])

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "directive",
                    "--workspace",
                    str(workspace_path),
                    "--session",
                    "cli_session",
                    "embedded [SYS: LANG=EN]",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr.getvalue(),
            "DirectiveError: message is not an exact standalone directive\n"
            "Debug log (if available, workspace-relative): "
            "debug_logs/runtime.jsonl\n",
        )
        debug_log = workspace.path("debug_logs/runtime.jsonl")
        raw_debug_log = debug_log.read_text(encoding="utf-8")
        records = [json.loads(line) for line in raw_debug_log.splitlines()]
        cli_failure = next(
            record
            for record in reversed(records)
            if record["component"] == "cli"
            and record["operation"] == "cli_command"
            and record["level"] == "error"
        )
        self.assertEqual(
            {
                key: value
                for key, value in cli_failure.items()
                if key not in {"timestamp", "exception"}
            },
            {
                "schema_version": 1,
                "level": "error",
                "component": "cli",
                "operation": "cli_command",
                "phase": "failed",
                "session_id": "cli_session",
                "outcome": "failed",
            },
        )
        self.assertEqual(
            set(cli_failure["exception"]),
            {"type", "frames", "frames_truncated"},
        )
        for prohibited in (
            "embedded [SYS: LANG=EN]",
            "message is not an exact standalone directive",
            str(workspace.root),
        ):
            self.assertNotIn(prohibited, raw_debug_log)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "run",
                    "--workspace",
                    str(workspace_path),
                    "--session",
                    "cli_session",
                    "--backend",
                    "unsafe backend spec",
                    "--draft",
                    "neutral draft",
                    "--output",
                    "part-01/invalid-backend.md",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("ConfigurationError", stderr.getvalue())
        self.assertTrue(
            stderr.getvalue().endswith(
                "Debug log (if available, workspace-relative): "
                "debug_logs/runtime.jsonl\n"
            )
        )
        self.assertNotIn(
            "unsafe backend spec",
            debug_log.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
