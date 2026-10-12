"""Public import, durable inspection, and command-line behavior tests."""

from __future__ import annotations

from story_writing_agents import lead_exploration as exploration

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
from dataclasses import MISSING, FrozenInstanceError, fields, replace
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import cli as cli_module
from story_writing_agents.workspace import core as workspace_module
from story_writing_agents.workspace import (
    initialization as workspace_initialization_module,
)
from story_writing_agents.conversation_storage import (
    selection as storage_selection_module,
)
from story_writing_agents import (
    DebateContextMode,
    DebateContextRequest,
    DebateFailureKind,
    DebateMessage,
    DebateProgressEvent,
    DebateProgressPhase,
    InputKind,
    PLOT_ARCHITECT,
    ProgressObserverError,
    ReplyTrace,
    ReplyTraceOrigin,
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
    load_conversation_storage_activation_state,
)
from story_writing_agents.conversation_storage.selection import (
    ConversationStorageStartup,
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
from story_writing_agents.interactive_settings import (
    SettingsError,
    SettingsErrorKind,
)
from story_writing_agents.provider_auth import (
    ProviderAuthSnapshot,
    ProviderAuthState,
    ProviderAuthStatus,
)
from tests.helpers import (
    initialized_workspace,
    pristine_initialized_workspace,
    workspace_git_identity,
)
from tests.sqlite_conversation_storage_fixture import database_path, provision


from tests.test_smoke_cli import editorial_action, editorial_fixture

def _cli_new_patch(attribute, value):
    return mock.patch.object(cli_module, attribute, value)


def _cli_object_effect_patch(target, attribute, value):
    return mock.patch.object(target, attribute, side_effect=value)


def _fresh_construction_seam_patch(module, attribute):
    return mock.patch.object(
        module,
        attribute,
    )

def _resolved_workspace_patch(workspace):
    return mock.patch.object(cli_module, "resolve_workspace", return_value=workspace)


def _fresh_cli_value_patch(attribute, value):
    return mock.patch.object(
        cli_module,
        attribute,
        return_value=value,
    )

def _object_options_patch(target, attribute, **options):
    return mock.patch.object(target, attribute, **options)


def _fresh_cli_effect_patch(attribute, effect):
    return mock.patch.object(cli_module, attribute, side_effect=effect)

def _fresh_terminal_builder_patch(terminal):
    return mock.patch.object(
        cli_module,
        "build_stream_terminal",
        return_value=terminal,
    )


def _fresh_logger_failure_patch(control_type):
    return mock.patch.object(
        cli_module,
        "WorkspaceDebugLog",
        side_effect=control_type("logger acquisition"),
    )



def _fresh_auth_service_patch():
    return mock.patch.object(
        cli_module,
        "ProviderAuthService",
        return_value=object(),
    )

def _fresh_empty_terminal_patch():
    terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)
    return _fresh_terminal_builder_patch(terminal)

def _fresh_empty_cli_context_patch():
    context = CliContext()
    return _fresh_captured_cli_context_patch(context)



def _fresh_cli_context_failure_patch(failure):
    return mock.patch.object(
        cli_module,
        "load_cli_context",
        side_effect=failure,
    )



def _fresh_recording_cli_context_patch(events):
    return mock.patch.object(
        cli_module,
        "load_cli_context",
        side_effect=lambda current: (
            events.append(("context", None)) or CliContext()
        ),
    )

def _fresh_default_backend_attempt_patch(argument):
    return mock.patch.object(
        cli_module,
        'establish_default_backend',
        side_effect=argument,
    )

def _fresh_workspace_resolution_failure_patch(argument):
    return mock.patch.object(
        cli_module,
        'resolve_workspace',
        side_effect=argument,
    )

def _fresh_captured_cli_context_patch(argument):
    return mock.patch.object(
        cli_module,
        'load_cli_context',
        return_value=argument,
    )


def _fresh_workspace_mkdir_callback_patch(argument):
    return mock.patch.object(
        workspace_module.os,
        'mkdir',
        side_effect=argument,
    )

def _fresh_stream_terminal_failure_patch(argument):
    return mock.patch.object(
        cli_module,
        "build_stream_terminal",
        side_effect=argument,
    )

def _fresh_cli_output_streams():
    stdout = io.StringIO()
    stderr = io.StringIO()
    return stdout, stderr


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

    def select_default(
        self,
        current: ProviderAuthSnapshot,
        *,
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
        self.events.append("select_default")
        if current is not self.snapshot or selected_provider != "codex":
            raise AssertionError("unexpected default-provider recommendation")
        return provider_result(current, "codex")


class ScriptedCliCoordinator:
    """Supply exact startup snapshots/results while recording gate order."""

    def __init__(
        self,
        events: list[tuple[str, object | None]],
        *,
        snapshots: tuple[object, ...],
        results: tuple[object, ...] = (),
        default_results: tuple[object, ...] = (),
    ) -> None:
        self.events = events
        self.snapshots = list(snapshots)
        self.results = list(results)
        self.default_results = list(default_results)

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

    def select_default(
        self,
        current: ProviderAuthSnapshot,
        *,
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
        self.events.append(("select_default", selected_provider))
        if not self.default_results:
            raise AssertionError("unexpected CLI default-provider picker")
        value = self.default_results.pop(0)
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
    def test_all_declared_public_imports_exist_and_runtime_aliases_are_absent(
        self,
    ) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 106)
        for name in story_writing_agents.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(story_writing_agents, name))
        for removed_name in ("Runtime", "Session"):
            self.assertNotIn(removed_name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, removed_name))
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
        for facade in (story_writing_agents.StoryRuntime.lead_turn, story_writing_agents.RuntimeSession.lead_turn):
            parameters = inspect.signature(facade).parameters
            self.assertEqual(tuple((parameters[key].kind, parameters[key].default) for key in ("interaction_mode", "adoption")), ((inspect.Parameter.KEYWORD_ONLY, exploration.LeadInteractionMode.ORCHESTRATION), (inspect.Parameter.KEYWORD_ONLY, None)))

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
            "ReplyTrace",
            "ReplyTraceOrigin",
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
                "ReplyTrace",
                "ReplyTraceOrigin",
                "StoryWritingAgentsError",
            }
        ):
            with self.subTest(error=name):
                self.assertTrue(
                    issubclass(getattr(story_writing_agents, name), base_error)
                )
        from story_writing_agents.consensus import AuthoringIntent
        from story_writing_agents.specialist_routing import AuthoringIntent as intent_owner
        self.assertIs(AuthoringIntent, intent_owner)
        self.assertNotIn("AuthoringIntent", story_writing_agents.__all__)

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
        self.assertEqual(signature.parameters["timeout_seconds"].default, 900)
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

    def test_uninitialized_direct_constructors_reject_without_artifacts(
        self,
    ) -> None:
        """Require strict v2 readiness before direct runtime activation."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "uninitialized"
            for factory in (StoryRuntime, story_writing_agents.AgentBackend):
                with self.subTest(factory=factory.__name__):
                    with self.assertRaisesRegex(
                        WorkspaceFormatError,
                        "workspace is not ready",
                    ) as caught:
                        factory(target)
                    self.assertIsNone(caught.exception.__cause__)
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

        direct_only = {
            "COLLABORATION_READABLE_SOURCE_SCHEMA_ID",
            "CollaborationHistoryCitation",
            "CollaborationHistoryPage",
            "CollaborationHistoryPageCursor",
            "CollaborationHistorySearchHit",
            "CollaborationHistorySearchQuery",
            "CollaborationHistorySearchResult",
            "CollaborationHistorySearch",
            "CollaborationHistorySearchScope",
            "CollaborationHistoryService",
            "ReferenceCollaborationHistorySearch",
            "collaboration_readable_document_id",
            "continue_collaboration_history",
            "page_collaboration_history",
            "resolve_collaboration_citation",
            "search_collaboration_history",
        }
        required = set(conversation_search.__all__).difference(direct_only)
        self.assertTrue(required.issubset(story_writing_agents.__all__))
        self.assertTrue(direct_only.isdisjoint(story_writing_agents.__all__))
        for name in required:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(story_writing_agents, name),
                    getattr(conversation_search, name),
                )


class PersistenceValidationTests(unittest.TestCase):
    @staticmethod
    def _message(*, trace: ReplyTrace | None = None) -> DebateMessage:
        return DebateMessage(
            sender="plot_architect",
            phase="audit",
            round=1,
            revision="r0",
            kind="approval",
            content="Visible assessment.",
            timestamp="2026-01-01T00:00:00+00:00",
            trace=trace,
        )

    def test_persisted_debate_log_reopens_traced_and_untraced_messages_exactly(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        trace = ReplyTrace(
            role="plot_architect",
            action="audit",
            context_sources=("current_input", "read:writing_artifact"),
            recovered_fields=("structured_reply",),
            origin=ReplyTraceOrigin.RECOVERED,
        )
        untraced = self._message()
        traced = self._message(trace=trace)
        workspace.append_debate_message("session_a", "debate_a", untraced)
        workspace.append_debate_message("session_a", "debate_a", traced)

        restored = workspace.read_debate_messages("session_a", "debate_a")
        self.assertEqual(restored, (untraced, traced))
        self.assertIsNone(restored[0].trace)
        self.assertIs(type(restored[1].trace), ReplyTrace)
        self.assertEqual(restored[1].trace, trace)
        self.assertEqual(
            workspace.read_debate_messages("session_a", "debate_a"),
            restored,
        )

        raw = tuple(
            json.loads(line)
            for line in workspace.read_text(
                "discussion_logs/session_a/debate_a.jsonl"
            ).splitlines()
        )
        base_fields = {
            "sender",
            "phase",
            "round",
            "revision",
            "kind",
            "content",
            "timestamp",
        }
        self.assertEqual(set(raw[0]), base_fields)
        self.assertNotIn("trace", raw[0])
        self.assertEqual(set(raw[1]), {*base_fields, "trace"})
        self.assertEqual(raw[1]["trace"], trace.to_dict())

    def test_persisted_debate_log_reopens_validation_details_with_optional_trace(
        self,
    ) -> None:
        workspace = pristine_initialized_workspace(self)
        relative = "discussion_logs/session_a/debate_a.jsonl"
        detail = "Original response:\n$.action.result: expected object, got array"
        trace = ReplyTrace(
            role="plot_architect",
            action="audit",
            context_sources=("current_input",),
            recovered_fields=("structured_reply",),
            origin=ReplyTraceOrigin.RECOVERED,
        )
        legacy = self._message()
        untraced = replace(legacy, validation_details=(detail,))
        traced = replace(self._message(trace=trace), validation_details=(detail,))
        for message in (legacy, untraced, traced):
            workspace.append_debate_message("session_a", "debate_a", message)

        self.assertEqual(
            workspace.read_debate_messages("session_a", "debate_a"),
            (legacy, untraced, traced),
        )
        raw = tuple(json.loads(line) for line in workspace.read_text(relative).splitlines())
        self.assertNotIn("validation_details", raw[0])
        self.assertNotIn("trace", raw[1])
        self.assertEqual(raw[1]["validation_details"], [detail])
        self.assertEqual(raw[2]["validation_details"], [detail])
        self.assertEqual(raw[2]["trace"], trace.to_dict())

        workspace.write_text(
            relative,
            workspace.read_text(relative)
            + json.dumps({**raw[2], "unknown": "not a valid extension"}) + "\n",
        )
        with self.assertRaisesRegex(
            WorkspaceFormatError, r"^invalid debate log entry at line 4$",
        ):
            workspace.read_debate_messages("session_a", "debate_a")

    def test_persisted_debate_log_rejects_noncanonical_fields_values_and_rounds(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        relative = "discussion_logs/session_a/debate_a.jsonl"
        base = self._message().to_dict()
        valid_trace = ReplyTrace(
            role="plot_architect",
            action="audit",
            context_sources=("current_input",),
        ).to_dict()
        invalid_trace = dict(valid_trace)
        invalid_trace["origin"] = "unknown"
        missing_trace_field = dict(valid_trace)
        missing_trace_field.pop("action")
        extra_trace_field = {**valid_trace, "message": "must not be persisted"}

        invalid = [
            ("unknown base field", {**base, "unknown": True}),
            (
                "unknown traced field",
                {**base, "trace": valid_trace, "unknown": True},
            ),
            ("missing base field", {key: value for key, value in base.items() if key != "kind"}),
            ("null trace", {**base, "trace": None}),
            ("trace scalar", {**base, "trace": "invalid"}),
            ("trace list", {**base, "trace": []}),
            ("missing trace field", {**base, "trace": missing_trace_field}),
            ("extra trace field", {**base, "trace": extra_trace_field}),
            ("invalid trace origin", {**base, "trace": invalid_trace}),
            *(
                (
                    f"non-string {field}",
                    {**base, field: None},
                )
                for field in (
                    "sender",
                    "phase",
                    "revision",
                    "kind",
                    "content",
                    "timestamp",
                )
            ),
            ("boolean round", {**base, "round": True}),
            ("negative round", {**base, "round": -1}),
            ("string round", {**base, "round": "1"}),
            ("float round", {**base, "round": 1.0}),
        ]
        valid_prefix = json.dumps(base, sort_keys=True) + "\n"
        for label, value in invalid:
            with self.subTest(label=label):
                workspace.write_text(
                    relative,
                    valid_prefix + json.dumps(value, sort_keys=True) + "\n",
                )
                with self.assertRaisesRegex(
                    WorkspaceFormatError,
                    r"^invalid debate log entry at line 2$",
                ):
                    workspace.read_debate_messages("session_a", "debate_a")

        dataset, captures, packet_dir = editorial_fixture(self)
        raw = (captures / "captures.json").read_bytes()
        mutations = (
            lambda data: data.update(format_version=True),
            lambda data: data.update(seed=-1),
            lambda data: data.update(treatment="é" * 2049),
            lambda data: data.update(unknown_field=0),
            lambda data: data["arms"].append(data["arms"][0]),
            lambda data: data.update(dataset_sha256="0" * 64),
            lambda data: data["cases"][0]["runs"]["fixture-before"]["output"].update(path="../output-a.txt"),
            lambda data: data["cases"][0]["runs"]["fixture-before"]["output"].update(sha256="0" * 64),
            lambda data: data["cases"][0]["runs"]["fixture-before"]["costs"].update(submitted_calls=True),
        )
        for index, mutate in enumerate(mutations):
            data = json.loads(raw)
            mutate(data)
            changed = captures / "changed.json"
            changed.write_text(json.dumps(data), encoding="utf-8")
            output = packet_dir.parent / f"invalid-{index}"
            invalid = editorial_action('prepare', dataset, changed, output=output)
            self.assertEqual(invalid.returncode, 2, invalid.stderr)
            self.assertFalse(output.exists())
        binding_mutations = (
            lambda data: data.update(seed=data["seed"] + 1),
            lambda data: data["cases"][0]["runs"]["fixture-before"]["conditions"].update(provider="Hidden metadata mutation"),
            lambda data: None,
        )
        for index, mutate in enumerate(binding_mutations):
            data = json.loads(raw)
            mutate(data)
            changed.write_text(json.dumps(data, indent=2), encoding="utf-8")
            stale = editorial_action('compare', dataset, changed, packet=packet_dir / 'review-packet.json', reviews=captures / 'reviews.json', output=packet_dir.parent / f'stale-{index}')
            self.assertEqual(stale.returncode, 2, stale.stderr)
        reviews = json.loads((captures / "reviews.json").read_bytes())
        reviews["reviews"][0]["evidence_kind"] = "human_blinded"
        changed_reviews = captures / "human-without-context.json"
        changed_reviews.write_text(json.dumps(reviews), encoding="utf-8")
        unsupported = editorial_action('compare', dataset, captures / 'captures.json', packet=packet_dir / 'review-packet.json', reviews=changed_reviews, output=packet_dir.parent / 'unbacked-human')
        self.assertEqual(unsupported.returncode, 2, unsupported.stderr)


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
        self.enterContext(workspace_git_identity())
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
            "inspect",
            "rag",
            "session",
            "storage",
            "smoke",
        ):
            with self.subTest(command=command):
                self.assertIn(command, completed.stdout)
        self.assertNotIn("{run,", completed.stdout)
        self.assertNotIn("\n    run ", completed.stdout)

    def test_removed_run_exits_two_before_workspace_provider_storage_or_artifacts(
        self,
    ) -> None:
        parser = build_parser()
        choices = next(
            action.choices
            for action in parser._actions
            if getattr(action, "dest", None) == "command"
        )
        self.assertNotIn("run", choices)
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            mock.patch.object(cli_module, "resolve_workspace") as workspace,
            mock.patch.object(cli_module, "AgentBackend") as backend,
            _fresh_construction_seam_patch(cli_module, "activate_configured_conversation_storage") as storage,
            mock.patch.object(cli_module, "InteractiveController") as controller,
            self.assertRaises(SystemExit) as raised,
        ):
            main(
                [
                    "run",
                    "--workspace",
                    "/private/workspace",
                    "--prompt",
                    "private prompt",
                    "--output",
                    "part-01/private.md",
                ]
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice: 'run'", stderr.getvalue())
        for collaborator in (workspace, backend, storage, controller):
            collaborator.assert_not_called()
        self.provider_auth_factory.assert_not_called()
        self.auth_coordinator_factory.assert_not_called()

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
        for arguments in (["chat", "--codex-reasoning-effort", "high"],):
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
            ["storage", "transfer", "--help"],
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
                if arguments[:1] == ["chat"]:
                    for option in (
                        "--antigravity-executable",
                        "--antigravity-model",
                        "--antigravity-effort",
                    ):
                        self.assertIn(option, completed.stdout)

        storage_help = subprocess.run(
            [sys.executable, "-m", "story_writing_agents", "storage", "--help"],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(storage_help.returncode, 0, storage_help.stderr)
        self.assertIn("transfer", storage_help.stdout)
        self.assertNotIn("migrate", storage_help.stdout)

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
                    _fresh_cli_effect_patch("_parse_backend_overrides", AssertionError("parsed overrides before TTY")) as parse_overrides,
                    _fresh_workspace_resolution_failure_patch(AssertionError("resolved workspace before TTY")) as resolve_workspace,
                    _fresh_cli_context_failure_patch(AssertionError("loaded context before TTY")) as load_context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    _fresh_construction_seam_patch(cli_module, "build_stream_terminal") as terminal_factory,
                    _fresh_construction_seam_patch(cli_module, 'InteractiveController') as controller,
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
            self.assertEqual(exit_code, 2)
            self.assertEqual(
                stderr.getvalue(),
                "WorkspaceFormatError: workspace is not ready\n",
            )
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            self.assertFalse(target.exists())

            stderr = io.StringIO()
            failure = WorkspaceFormatError("initialization failed")
            with (
                _fresh_cli_effect_patch('initialize_workspace', failure) as initialize,
                mock.patch.object(cli_module, "_run_interactive_cli") as shell,
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(["init", str(target)]), 2)
            shell.assert_not_called()
            self.assertEqual(
                stderr.getvalue(), "WorkspaceFormatError: initialization failed\n"
            )
            self.assertNotIn("debug_logs/runtime.jsonl", stderr.getvalue())
            initialize.assert_called_once_with(str(target))
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

        if os.rename not in getattr(
            os, "supports_dir_fd", ()
        ) or os.symlink not in getattr(os, "supports_dir_fd", ()):
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

            stdout, stderr = _fresh_cli_output_streams()
            with (
                _fresh_construction_seam_patch(workspace_initialization_module, '_require_initialization_operations'),
                _fresh_workspace_mkdir_callback_patch(substitute_final_root),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(["init", str(root)])

            self.assertEqual(exit_code, 2)
            self.assertTrue(substituted)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(
                stderr.getvalue().startswith(("NotADirectoryError: ", "OSError: "))
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

                stdout, stderr = _fresh_cli_output_streams()
                with (
                    _fresh_construction_seam_patch(workspace_initialization_module, '_require_initialization_operations'),
                    _fresh_workspace_mkdir_callback_patch(fail_final_creation),
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
                            self.fail(f"{type(failure).__name__} did not propagate")
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
                        expected_interactive_error(InteractiveErrorCode.TTY_REQUIRED),
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
                900.0,
                {"plot_architect": "antigravity"},
                "private-agy",
                "agy-model",
                "medium",
            ),
            ([], None, None, 900.0, {}, "agy", None, None),
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
                    _resolved_workspace_patch(workspace) as resolve_workspace,
                    _fresh_captured_cli_context_patch(cli_context) as load_context,
                    _fresh_cli_value_patch('AgentBackend', backend_value) as backend_factory,
                    _fresh_cli_value_patch('ProviderCatalogService', catalog_value) as catalog_factory,
                    _fresh_terminal_builder_patch(terminal_value) as terminal_factory,
                    _fresh_cli_value_patch('InteractiveController', controller_value) as controller_factory,
                ):
                    self.assertEqual(main(arguments), 7)

                expected_workspace_option = str(workspace.root) if arguments else None
                resolve_workspace.assert_called_once_with(expected_workspace_option)
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
                main_reader.assert_called_once_with("story[lobby]> ")
                self.assertEqual(stdin.readline_calls, 0)
                controller_value.run_session_lobby.assert_called_once_with(
                    requested_session=requested
                )
                backend_value.close.assert_not_called()
                catalog_value.discover.assert_not_called()
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(
                    workspace.read_json("config/system_defaults.json")[
                        "default_backend"
                    ],
                    "codex",
                )
                self.assertTrue(
                    all(
                        agent["backend"] is None
                        for agent in workspace.read_json(
                            "config/agent_configs.json"
                        ).values()
                    )
                )

    def test_interactive_startup_orders_every_gate_before_runtime_artifacts(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        events: list[tuple[str, object | None]] = []
        both = authenticated_provider_snapshot()
        coordinator = ScriptedCliCoordinator(
            events,
            snapshots=(both, both),
            default_results=(provider_result(both, "codex"),),
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
        original_select_default = cli_module.select_default_provider
        original_load_snapshot = cli_module.ConfigManager.load_configuration_snapshot
        original_routes = cli_module._effective_interactive_providers
        original_establish_default = cli_module.establish_default_backend
        original_activate = cli_module.activate_configured_conversation_storage
        storage_startups: list[ConversationStorageStartup] = []

        def validate_executable(value: object) -> str:
            events.append(("validation", value))
            return original_validate_executable(value)

        def select_default(*args: object, **kwargs: object) -> str | None:
            selected = original_select_default(*args, **kwargs)  # type: ignore[arg-type]
            events.append(("default", selected))
            return selected

        def load_snapshot(manager: object) -> object:
            events.append(("snapshot", None))
            return original_load_snapshot(manager)  # type: ignore[arg-type]

        def routes(*args: object, **kwargs: object) -> tuple[str, ...]:
            events.append(("routes", None))
            return original_routes(*args, **kwargs)  # type: ignore[arg-type]

        def establish_default(*args: object, **kwargs: object) -> bool:
            events.append(("persist_default", args[1]))
            return original_establish_default(*args, **kwargs)  # type: ignore[arg-type]

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
            _fresh_cli_effect_patch('_validate_antigravity_executable', validate_executable),
            _fresh_workspace_resolution_failure_patch(lambda option: (
                    events.append(("workspace", option)) or workspace
                )),
            _fresh_cli_effect_patch('activate_configured_conversation_storage', activate),
            _fresh_stream_terminal_failure_patch(lambda *args: (
                    events.append(("terminal", None)) or terminal
                )),
            _fresh_cli_effect_patch(
                'ProviderAuthService',
                lambda *args, **kwargs: events.append(('auth_service', args[0])) or auth_service,
            ),
            _fresh_cli_effect_patch('ProviderCatalogService', lambda *args, **kwargs: (
                self.assertEqual(args, (workspace.root,)) or self.assertEqual(kwargs, {'antigravity_executable': 'agy'})
                or events.append(('catalog_service', args[0])) or catalog_service
            )),
            _fresh_cli_effect_patch('InteractiveAuthCoordinator', lambda service, current_terminal: (
                self.assertIs(service, auth_service) or self.assertIs(current_terminal, terminal)
                or events.append(('auth_coordinator', None)) or coordinator
            )),
            _fresh_cli_effect_patch('select_default_provider', select_default),
            _object_options_patch(cli_module.ConfigManager, 'load_configuration_snapshot',
                                  autospec=True, side_effect=load_snapshot),
            _fresh_default_backend_attempt_patch(establish_default),
            _fresh_cli_effect_patch('_effective_interactive_providers', routes),
            _fresh_recording_cli_context_patch(events),
            _fresh_cli_effect_patch(
                'AgentBackend',
                lambda *args, **kwargs: events.append(('backend', None)) or backend,
            ),
            _fresh_cli_effect_patch('InteractiveController', controller_factory),
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
                "snapshot",
                "progress_end",
                "auth_service",
                "catalog_service",
                "auth_coordinator",
                "welcome",
                "probe_all",
                "default",
                "snapshot",
                "select_default",
                "persist_default",
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
        self.assertEqual(events[8][1], workspace.root)
        self.assertEqual(events[9][1], workspace.root)
        self.assertEqual(
            workspace.read_json("config/system_defaults.json")["default_backend"],
            "codex",
        )
        backend.close.assert_not_called()
        self.assertEqual(stderr.getvalue(), "")

    def test_desired_sqlite_startup_failures_are_provider_free_and_preserving(
        self,
    ) -> None:
        custom_summary = "Conversation storage activation could not be completed."
        custom_hint = (
            "Run story storage use, transfer, or verify as appropriate, then "
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
                    _object_options_patch(storage_selection_module, 'probe_sqlite_conversation_storage_runtime',
                                          return_value=unavailable_preflight)
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
                    _resolved_workspace_patch(workspace),
                    runtime_probe_context as runtime_probe,
                    _fresh_terminal_builder_patch(terminal) as terminal_factory,
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    _fresh_construction_seam_patch(cli_module, 'InteractiveController') as controller,
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
                    _resolved_workspace_patch(workspace),
                    _fresh_cli_effect_patch('_run_interactive_startup_progress', run_progress),
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    _fresh_construction_seam_patch(cli_module, 'InteractiveController') as controller,
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
                agent_before = workspace.path("config/agent_configs.json").read_bytes()
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
                    _resolved_workspace_patch(workspace),
                    _fresh_terminal_builder_patch(terminal),
                    _fresh_auth_service_patch(),
                    _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    _fresh_construction_seam_patch(cli_module, 'InteractiveController') as controller,
                ):
                    self.assertEqual((main(["chat"]), stderr.getvalue()), (0, ""))
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

    def test_missing_default_retries_and_persists_only_authenticated_choice(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        codex_only = provider_snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        both = authenticated_provider_snapshot()
        events: list[tuple[str, object | None]] = []
        coordinator = ScriptedCliCoordinator(
            events,
            snapshots=(codex_only, both),
            default_results=(
                provider_result(codex_only, "antigravity"),
                provider_result(both, "antigravity"),
            ),
        )
        terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)
        backend = mock.Mock()
        controller = mock.Mock()
        controller.run_session_lobby.return_value = 0
        stdin = TtyStringIO(is_terminal=True)
        stdout = TtyStringIO(is_terminal=True)
        stderr = io.StringIO()

        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(cli_module, "resolve_workspace", return_value=workspace),
            _fresh_terminal_builder_patch(terminal),
            mock.patch.object(cli_module, "ProviderAuthService", return_value=object()),
            _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
            _object_options_patch(cli_module, 'establish_default_backend',
                                  wraps=cli_module.establish_default_backend) as establish,
            _fresh_cli_value_patch('AgentBackend', backend) as backend_factory,
            _fresh_cli_value_patch('InteractiveController', controller),
        ):
            self.assertEqual(main(["chat", "--workspace", str(workspace.root)]), 0)

        self.assertEqual(
            [event for event in events if event[0] in {"select_default", "probe_all"}],
            [
                ("probe_all", None),
                ("select_default", "codex"),
                ("select_default", "antigravity"),
                ("probe_all", None),
            ],
        )
        establish.assert_called_once_with(workspace, "antigravity")
        self.assertEqual(
            workspace.read_json("config/system_defaults.json")["default_backend"],
            "antigravity",
        )
        self.assertEqual(backend_factory.call_args.kwargs["backend_overrides"], {})
        self.assertEqual(stderr.getvalue(), "")

    def test_concurrent_default_winner_is_reloaded_and_remains_authoritative(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        both = authenticated_provider_snapshot()
        coordinator = ScriptedCliCoordinator(
            [],
            snapshots=(both, both),
            default_results=(provider_result(both, "codex"),),
        )
        terminal = InteractiveTerminal(lambda _prompt: "", lambda _text: None)
        controller = mock.Mock()
        controller.run_session_lobby.return_value = 0

        def concurrent_winner(current: Workspace, proposed: str) -> None:
            self.assertIs(current, workspace)
            self.assertEqual(proposed, "codex")
            system = current.read_json("config/system_defaults.json")
            system["default_backend"] = "antigravity"
            current.write_json("config/system_defaults.json", system)
            raise SettingsError(SettingsErrorKind.CONFLICT)

        with (
            mock.patch.object(sys, "stdin", TtyStringIO(is_terminal=True)),
            mock.patch.object(sys, "stdout", TtyStringIO(is_terminal=True)),
            mock.patch.object(sys, "stderr", io.StringIO()),
            mock.patch.object(cli_module, "resolve_workspace", return_value=workspace),
            _fresh_terminal_builder_patch(terminal),
            mock.patch.object(cli_module, "ProviderAuthService", return_value=object()),
            _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
            _fresh_default_backend_attempt_patch(concurrent_winner) as establish,
            _object_options_patch(cli_module, '_effective_interactive_providers',
                                  wraps=cli_module._effective_interactive_providers) as routes,
            mock.patch.object(cli_module, "AgentBackend", return_value=mock.Mock()) as backend,
            _fresh_cli_value_patch('InteractiveController', controller),
        ):
            self.assertEqual(main(["chat", "--workspace", str(workspace.root)]), 0)

        establish.assert_called_once_with(workspace, "codex")
        self.assertEqual(
            routes.call_args.args[1].system_defaults.default_backend,
            "antigravity",
        )
        self.assertEqual(
            workspace.read_json("config/system_defaults.json")["default_backend"],
            "antigravity",
        )
        self.assertEqual(backend.call_args.kwargs["backend_overrides"], {})

    def test_missing_default_controls_and_write_failure_precede_runtime_setup(
        self,
    ) -> None:
        both = authenticated_provider_snapshot()
        for kind in (
            InteractiveAuthResultKind.CANCEL,
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        ):
            with self.subTest(kind=kind):
                workspace = initialized_workspace(self)
                before = workspace.path("config/system_defaults.json").read_bytes()
                coordinator = ScriptedCliCoordinator(
                    [],
                    snapshots=(both,),
                    default_results=(InteractiveAuthResult(kind, both),),
                )
                with (
                    mock.patch.object(sys, "stdin", TtyStringIO(is_terminal=True)),
                    mock.patch.object(sys, "stdout", TtyStringIO(is_terminal=True)),
                    mock.patch.object(sys, "stderr", io.StringIO()),
                    _resolved_workspace_patch(workspace),
                    _fresh_empty_terminal_patch(),
                    _fresh_auth_service_patch(),
                    _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
                    mock.patch.object(cli_module, "establish_default_backend") as establish,
                    mock.patch.object(cli_module, "load_cli_context") as context,
                    mock.patch.object(cli_module, "AgentBackend") as backend,
                    mock.patch.object(cli_module, "InteractiveController") as controller,
                ):
                    self.assertEqual(main(["chat"]), 0)
                establish.assert_not_called()
                context.assert_not_called()
                backend.assert_not_called()
                controller.assert_not_called()
                self.assertEqual(
                    workspace.path("config/system_defaults.json").read_bytes(),
                    before,
                )

        workspace = initialized_workspace(self)
        before = workspace.path("config/system_defaults.json").read_bytes()
        coordinator = ScriptedCliCoordinator(
            [],
            snapshots=(both,),
            default_results=(provider_result(both, "codex"),),
        )
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", TtyStringIO(is_terminal=True)),
            mock.patch.object(sys, "stdout", TtyStringIO(is_terminal=True)),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(cli_module, "resolve_workspace", return_value=workspace),
            _fresh_empty_terminal_patch(),
            mock.patch.object(cli_module, "ProviderAuthService", return_value=object()),
            _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
            _fresh_default_backend_attempt_patch(OSError("persistence-secret")),
            mock.patch.object(cli_module, "load_cli_context") as context,
            mock.patch.object(cli_module, "AgentBackend") as backend,
            mock.patch.object(cli_module, "InteractiveController") as controller,
        ):
            self.assertEqual(main(["chat"]), 2)
        self.assertEqual(
            stderr.getvalue(),
            expected_interactive_error(
                InteractiveErrorCode.WORKSPACE_INVALID,
                debug_log_available=True,
            ),
        )
        self.assertNotIn("persistence-secret", stderr.getvalue())
        context.assert_not_called()
        backend.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(
            workspace.path("config/system_defaults.json").read_bytes(),
            before,
        )

        workspace = initialized_workspace(self)
        system_before = workspace.path("config/system_defaults.json").read_bytes()
        agents_before = workspace.path("config/agent_configs.json").read_bytes()
        coordinator = ScriptedCliCoordinator(
            [],
            snapshots=(both,),
            default_results=(provider_result(both, "codex"),),
        )
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", TtyStringIO(is_terminal=True)),
            mock.patch.object(sys, "stdout", TtyStringIO(is_terminal=True)),
            mock.patch.object(sys, "stderr", stderr),
            mock.patch.object(cli_module, "resolve_workspace", return_value=workspace),
            _fresh_empty_terminal_patch(),
            mock.patch.object(cli_module, "ProviderAuthService", return_value=object()),
            _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
            _fresh_default_backend_attempt_patch(SettingsError(SettingsErrorKind.CONFLICT)),
            mock.patch.object(cli_module, "load_cli_context") as context,
            mock.patch.object(cli_module, "AgentBackend") as backend,
            mock.patch.object(cli_module, "InteractiveController") as controller,
        ):
            self.assertEqual(main(["chat"]), 2)
        self.assertEqual(
            stderr.getvalue(),
            expected_interactive_error(
                InteractiveErrorCode.CONFIGURATION_INVALID,
                debug_log_available=True,
            ),
        )
        context.assert_not_called()
        backend.assert_not_called()
        controller.assert_not_called()
        self.assertEqual(
            workspace.path("config/system_defaults.json").read_bytes(),
            system_before,
        )
        self.assertEqual(
            workspace.path("config/agent_configs.json").read_bytes(),
            agents_before,
        )
        self.assertFalse(
            tuple(
                workspace.path(
                    "agent_states/orchestrator_state/provider_sessions"
                ).glob("*.json")
            )
        )

    def test_storage_activation_cas_remains_after_later_provider_interrupt(
        self,
    ) -> None:
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
            _resolved_workspace_patch(workspace),
            _fresh_terminal_builder_patch(terminal),
            _fresh_auth_service_patch(),
            _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
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
            _fresh_terminal_builder_patch(terminal),
            mock.patch.object(cli_module, "ProviderAuthService", return_value=object()),
            _fresh_cli_value_patch('InteractiveAuthCoordinator', coordinator),
            mock.patch.object(cli_module, "establish_default_backend") as establish,
            _fresh_recording_cli_context_patch(events),
            _fresh_cli_effect_patch(
                'AgentBackend',
                lambda *args, **kwargs: events.append(('backend', None)) or mock.Mock(),
            ),
            _fresh_cli_value_patch('InteractiveController', controller),
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
        establish.assert_not_called()
        self.assertNotIn("select_default", [name for name, _value in events])

    def test_noninteractive_and_non_tty_forms_never_construct_auth(self) -> None:
        provider_catalog_factory = mock.Mock()
        provider_catalog_patcher = _cli_new_patch("ProviderCatalogService", provider_catalog_factory)
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

            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main(["run", "--workspace", str(workspace_path)])
            self.assertEqual(raised.exception.code, 2)
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
                _resolved_workspace_patch(workspace),
                _fresh_captured_cli_context_patch(context),
                _fresh_cli_value_patch('AgentBackend', backend_value),
                _cli_new_patch("InteractiveController", controller_factory),
            ):
                return main(["chat"]), stderr.getvalue()

        unstarted_backend = mock.Mock()
        constructor_failure = mock.Mock(side_effect=RuntimeError("constructor-secret"))
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
                    _fresh_workspace_resolution_failure_patch(failure),
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
                    _resolved_workspace_patch(workspace),
                    _fresh_cli_context_failure_patch(failure_type("context-secret")),
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
            _resolved_workspace_patch(workspace),
            _fresh_empty_cli_context_patch(),
            _fresh_stream_terminal_failure_patch(RuntimeError("terminal-constructor-secret")),
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
                    _resolved_workspace_patch(workspace),
                    _fresh_empty_cli_context_patch(),
                    _fresh_cli_effect_patch("AgentBackend", failure_type("backend-secret")),
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
                    _fresh_construction_seam_patch(cli_module, "resolve_workspace") as resolve_workspace,
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
                    _resolved_workspace_patch(workspace),
                    _fresh_logger_failure_patch(control_type) as logger,
                    _fresh_empty_cli_context_patch() as load_context,
                    _fresh_terminal_builder_patch(terminal) as build_terminal,
                    _fresh_cli_value_patch('AgentBackend', backend) as backend_factory,
                    _fresh_cli_value_patch('InteractiveController', controller) as controller_factory,
                ):
                    exit_code = main(["chat"])

                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                logger.assert_called_once_with(
                    workspace,
                    defer_until_session=True,
                )
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
                    _resolved_workspace_patch(workspace),
                    _fresh_logger_failure_patch(control_type) as logger,
                    _fresh_cli_context_failure_patch(failure) as load_context,
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
                logger.assert_called_once_with(
                    workspace,
                    defer_until_session=True,
                )
                load_context.assert_called_once_with(workspace)
                backend.assert_not_called()

    def test_init_never_constructs_a_post_baseline_logger(self) -> None:
        """Initialization leaves no diagnostic residue after the baseline."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            stdout, stderr = _fresh_cli_output_streams()
            with (
                mock.patch.object(cli_module, "WorkspaceDebugLog") as logger,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main(["init", str(root)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(
                stdout.getvalue(),
                json.dumps({"workspace": str(root.resolve())}, indent=2) + "\n",
            )
            logger.assert_not_called()
            self.assertFalse(root.joinpath("debug_logs/runtime.jsonl").exists())

    def test_ordinary_logger_acquisition_controls_preserve_output_and_hint(
        self,
    ) -> None:
        """Keep ordinary success and fixed later-failure output without a logger."""

        for control_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(control=control_type.__name__, outcome="success"):
                workspace = initialized_workspace(self)
                stdout, stderr = _fresh_cli_output_streams()
                expected = {
                    "debates": [],
                    "overrides": {"agents": {}, "language": None},
                    "session_id": "logger_optional",
                }
                with (
                    _resolved_workspace_patch(workspace),
                    _fresh_logger_failure_patch(control_type) as logger,
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
                logger.assert_called_once_with(
                    workspace,
                    defer_until_session=False,
                )

            with self.subTest(control=control_type.__name__, outcome="failure"):
                workspace = initialized_workspace(self)
                stdout, stderr = _fresh_cli_output_streams()
                failure = ConfigurationError("PRIVATE_POST_WORKSPACE_FAILURE")
                with (
                    _resolved_workspace_patch(workspace),
                    _fresh_logger_failure_patch(control_type) as logger,
                    _fresh_cli_context_failure_patch(failure) as load_context,
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
                logger.assert_called_once_with(
                    workspace,
                    defer_until_session=False,
                )
                load_context.assert_called_once_with(workspace)
                runtime.assert_not_called()

    def test_diagnostic_failure_preserves_noninteractive_exit_and_output(
        self,
    ) -> None:
        workspace = initialized_workspace(self)
        stderr = io.StringIO()
        with (
            _resolved_workspace_patch(workspace),
            _cli_object_effect_patch(cli_module.WorkspaceDebugLog, "error", SystemExit("diagnostic failure")) as debug_error,
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
        stdout, stderr = _fresh_cli_output_streams()
        with (
            _resolved_workspace_patch(workspace),
            _cli_object_effect_patch(WorkspaceDebugLog, "trace", SystemExit("diagnostic trace failure")) as trace,
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
        stdout, stderr = _fresh_cli_output_streams()
        with (
            _resolved_workspace_patch(workspace),
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
            for line in workspace.path("debug_logs/runtime.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
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
        workspace_path = Path(temporary.name) / "workspace with spaces"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["init", str(workspace_path)])
        self.assertEqual(exit_code, 0)
        initialized = json.loads(stdout.getvalue())
        self.assertEqual(Path(initialized["workspace"]), workspace_path.resolve())
        self.assertTrue((workspace_path / "config" / "system_defaults.json").is_file())

        workspace = Workspace(workspace_path)
        def snapshot():
            return {path: (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in workspace_path.rglob("*") if path.is_file()}
        before = snapshot()
        with (
            mock.patch.object(cli_module, "_interactive_streams_are_terminals", return_value=True),
            mock.patch.object(cli_module, "_run_interactive_cli", return_value=7) as shell,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["init", str(workspace_path)]), 7)
        shell.assert_called_once()
        self.assertEqual(vars(shell.call_args.args[0]),
                         vars(cli_module.build_parser().parse_args(
                             ["chat", "--workspace", str(workspace_path.resolve())])))
        self.assertEqual(snapshot(), before)
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as retired,
        ):
            main(["init", str(workspace_path), "--overwrite-prompts"])
        self.assertEqual(retired.exception.code, 2)
        self.assertIn("unrecognized arguments: --overwrite-prompts", stderr.getvalue())
        self.assertEqual(snapshot(), before)

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
        with (
            _resolved_workspace_patch(workspace),
            contextlib.redirect_stderr(stderr),
        ):
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


if __name__ == "__main__":
    unittest.main()
