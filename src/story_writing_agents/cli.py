"""Thin command-line interface over the public runtime API."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections.abc import Callable
from typing import Sequence, TypeVar, cast

from .agents import ROSTER
from .backend import AgentBackend
from .cli_context import (
    CliContext,
    load_cli_context,
    load_default_conversation,
    resolve_session,
    resolve_workspace,
    save_default_session,
)
from .config import ConfigurationSnapshot, ConfigManager
from .conversation_rag_cli import (
    ConversationRagCliFailure,
    ConversationRagCliOperation,
    ConversationRagCliReport,
    ConversationRagCliRequest,
    dispatch_conversation_rag,
)
from .conversation_storage.contracts import (
    ConversationStorageCoordinationError,
    ConversationStorageServices,
)
from .conversation_storage.selection import (
    ConversationStorageSelectionFailure,
    ConversationStorageSelectionReport,
    activate_configured_conversation_storage,
    select_conversation_storage,
)
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationNotFoundError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from .interactive_auth import (
    InteractiveAuthCoordinator,
    InteractiveAuthResult,
    InteractiveAuthResultKind,
    select_default_provider,
)
from .interactive_controller import InteractiveController
from .interactive_settings import (
    SettingsError,
    SettingsErrorKind,
)
from .interactive_terminal import (
    AdvisoryProgressError,
    InteractiveAdvisoryProgress,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveTerminal,
    build_stream_terminal,
)
from .orchestrator import StoryRuntime
from .provider_auth import ProviderAuthService, ProviderAuthSnapshot
from .provider_catalog import ProviderCatalogService
from .session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)
from .session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionOperation,
)
from .workspace import Workspace, initialize_workspace


_WORKSPACE_HELP = (
    "explicit initialized workspace; omitted discovers the nearest ancestor"
)
_SESSION_HELP = "explicit session; omitted uses the workspace default"
_INTERACTIVE_DEBUG_LOG_NOTICE = (
    "If available, workspace-relative: debug_logs/runtime.jsonl"
)
_NONINTERACTIVE_DEBUG_LOG_HINT = (
    "Debug log (if available, workspace-relative): debug_logs/runtime.jsonl"
)
_StartupT = TypeVar("_StartupT")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI grammar without reading files or starting runtime work.

    The returned parser includes optional bare interactive entry plus
    ``chat [SESSION]``. Parsing help has no TTY or
    resource requirement; interactive value validation remains after the TTY
    guard so non-terminal entry cannot touch configuration.
    """

    parser = argparse.ArgumentParser(prog="story-writing-agents")
    subcommands = parser.add_subparsers(dest="command")

    chat = subcommands.add_parser(
        "chat",
        help="enter the interactive story-session lobby",
    )
    chat.add_argument("session_name", nargs="?", metavar="SESSION")
    chat.add_argument("--workspace", help=_WORKSPACE_HELP)
    chat.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="AGENT=BACKEND",
        help="override one canonical agent backend (repeatable)",
    )
    chat.add_argument("--codex-model", metavar="MODEL")
    chat.add_argument(
        "--antigravity-executable",
        metavar="EXECUTABLE",
        default="agy",
    )
    chat.add_argument("--antigravity-model", metavar="MODEL")
    chat.add_argument("--antigravity-effort", metavar="EFFORT")
    chat.add_argument(
        "--codex-timeout",
        metavar="SECONDS",
        default=300,
        help="positive provider timeout in seconds (default: 300)",
    )

    initialize = subcommands.add_parser(
        "init",
        help="idempotently initialize a local workspace",
    )
    initialize.add_argument("workspace", nargs="?", default=".")

    directive = subcommands.add_parser(
        "directive",
        help="apply one exact standalone runtime directive",
    )
    directive.add_argument("--workspace", help=_WORKSPACE_HELP)
    directive.add_argument("--session", help=_SESSION_HELP)
    directive.add_argument("text")

    inspect = subcommands.add_parser(
        "inspect",
        help="inspect persisted session or debate state",
    )
    inspect.add_argument("--workspace", help=_WORKSPACE_HELP)
    inspect.add_argument("--session", help=_SESSION_HELP)
    inspect.add_argument("--debate")

    session = subcommands.add_parser(
        "session",
        help="create, reopen, or pause a persistent provider session",
    )
    session_actions = session.add_subparsers(dest="session_action", required=True)
    for action in ("create", "reopen", "pause"):
        action_parser = session_actions.add_parser(action)
        if action == "create":
            action_parser.add_argument(
                "session_name",
                metavar="SESSION",
                help="new session ID",
            )
        else:
            action_parser.add_argument(
                "session_name",
                nargs="?",
                metavar="SESSION",
                help="session ID; omitted uses the workspace default",
            )
        action_parser.add_argument("--workspace", help=_WORKSPACE_HELP)

    storage = subcommands.add_parser(
        "storage",
        help="select, transfer, or verify authoritative conversation storage",
    )
    storage_actions = storage.add_subparsers(
        dest="storage_action",
        required=True,
    )
    for action in ("transfer", "verify"):
        action_parser = storage_actions.add_parser(action)
        action_parser.add_argument(
            "--from",
            dest="source_backend",
            choices=("filesystem", "sqlite"),
            required=True,
        )
        action_parser.add_argument(
            "--to",
            dest="destination_backend",
            choices=("filesystem", "sqlite"),
            required=True,
        )
        action_parser.add_argument("--workspace", help=_WORKSPACE_HELP)
        if action == "transfer":
            action_parser.add_argument("--dry-run", action="store_true")
    use = storage_actions.add_parser("use")
    use.add_argument("backend", metavar="BACKEND")
    use.add_argument("--install-missing", action="store_true")
    use.add_argument("--yes", action="store_true")
    use.add_argument("--workspace", help=_WORKSPACE_HELP)

    rag = subcommands.add_parser(
        "rag",
        help="inspect and operate the local conversation RAG index",
    )
    rag_actions = rag.add_subparsers(dest="rag_action", required=True)
    rag_status = rag_actions.add_parser("status")
    rag_status.add_argument("--workspace", help=_WORKSPACE_HELP)
    rag_status.add_argument("--session", help=_SESSION_HELP)
    rag_enable = rag_actions.add_parser("enable")
    rag_enable.add_argument("--provider", required=True, metavar="PROVIDER")
    rag_enable.add_argument("--workspace", help=_WORKSPACE_HELP)
    rag_rebuild = rag_actions.add_parser("rebuild")
    rag_rebuild.add_argument("--yes", action="store_true")
    rag_rebuild.add_argument("--workspace", help=_WORKSPACE_HELP)
    rag_rebuild.add_argument("--session", help=_SESSION_HELP)
    rag_search = rag_actions.add_parser("search")
    rag_search.add_argument("query", metavar="QUERY")
    rag_search.add_argument("--workspace", help=_WORKSPACE_HELP)
    rag_search.add_argument("--session", help=_SESSION_HELP)
    rag_disable = rag_actions.add_parser("disable")
    rag_disable.add_argument("--workspace", help=_WORKSPACE_HELP)
    rag_purge = rag_actions.add_parser("purge")
    rag_purge.add_argument("--yes", action="store_true")
    rag_purge.add_argument("--workspace", help=_WORKSPACE_HELP)
    rag_purge.add_argument("--session", help=_SESSION_HELP)

    smoke = subcommands.add_parser(
        "smoke",
        help="run a provider-free deterministic runtime smoke check",
        description=(
            "Run a deterministic provider-free runtime check. When --workspace "
            "is omitted, a temporary workspace is created and removed."
        ),
    )
    smoke.add_argument(
        "--workspace",
        help="verify an existing workspace without modifying it",
    )
    return parser


def _parse_backend_overrides(values: Sequence[str]) -> dict[str, str]:
    """Validate CLI route overrides and return one backend per agent.

    The function performs no filesystem or provider work. Invalid syntax,
    duplicate agents, or unsupported backend names raise ``ConfigurationError``
    before any caller-owned runtime resource is created.
    """

    result: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str) or value.count("=") != 1:
            raise ConfigurationError(
                "backend override must use CANONICAL_AGENT_ID=BACKEND"
            )
        agent_id, backend = value.split("=", 1)
        if agent_id not in ROSTER:
            raise ConfigurationError(f"unknown backend override agent: {agent_id}")
        if agent_id in result:
            raise ConfigurationError(f"duplicate backend override agent: {agent_id}")
        validated = ConfigManager.validate_backend(backend)
        assert validated is not None
        result[agent_id] = validated
    return result


def _parse_positive_timeout_seconds(value: object) -> float:
    """Return one finite positive chat timeout without mutating state.

    Parsing is deliberately deferred until after the TTY guard. Invalid
    numbers raise ``ConfigurationError`` before workspace or backend access.
    """

    if isinstance(value, bool):
        raise ConfigurationError("Codex timeout must be a positive number")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("Codex timeout must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("Codex timeout must be a positive number")
    return timeout


def _validate_antigravity_executable(value: object) -> str:
    """Return one nonempty NUL-free executable before workspace access."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigurationError(
            "Antigravity executable must be non-empty NUL-free text"
        )
    return value


def _require_session_command_state(
    value: object,
    *,
    session_id: str,
    durable_state: SessionDurableState,
    access_state: SessionAccessState,
) -> SessionLifecycleState:
    """Return exact correlated lifecycle facts for one automation transition."""

    if type(value) is not SessionLifecycleState:
        raise BackendProtocolError(
            "session command returned an invalid lifecycle state"
        )
    try:
        provider_work_is_ready(
            value,
            provider_configuration_ready=False,
        )
    except (TypeError, ValueError) as exc:
        raise BackendProtocolError(
            "session command returned malformed lifecycle facts"
        ) from exc
    if (
        value.session_id != session_id
        or value.durable_state is not durable_state
        or value.access_state is not access_state
    ):
        raise BackendProtocolError(
            "session command returned uncorrelated lifecycle facts"
        )
    return value


def _transition_assessment_payload(
    assessment: SessionTransitionAssessment,
) -> dict[str, object]:
    """Return only fixed content-safe transition fields for CLI JSON."""

    if type(assessment) is not SessionTransitionAssessment:
        raise BackendProtocolError(
            "session command returned an invalid transition assessment"
        )
    try:
        assessment.__post_init__()
    except Exception as exc:
        raise BackendProtocolError(
            "session command returned malformed transition assessment facts"
        ) from exc
    recovery = assessment.recovery_result
    recovery_payload: dict[str, object] | None = None
    if recovery is not None:
        recovery_payload = {
            "application_id": recovery.application_id,
            "checkpoint_operation_id": recovery.checkpoint_operation_id,
            "collaboration_tail_sequence": recovery.collaboration_tail_sequence,
            "commit_id": recovery.commit_id,
            "confirmation_request_id": recovery.confirmation_request_id,
            "conversation_id": recovery.conversation_id,
            "correlation_id": recovery.correlation_id,
            "failure_code": recovery.failure_code.value,
            "next_action": recovery.next_action.value,
            "owner": recovery.owner.value,
            "proposal_id": recovery.proposal_id,
            "reviewed_candidate_id": recovery.reviewed_candidate_id,
            "session_id": recovery.session_id,
            "state": recovery.state.value,
            "story_commit_candidate_id": recovery.story_commit_candidate_id,
            "story_commit_operation_id": recovery.story_commit_operation_id,
        }
    return {
        "allowed_operations": [
            operation.value for operation in assessment.allowed_operations
        ],
        "conversation_id": assessment.conversation_id,
        "failure_code": assessment.failure_code.value,
        "next_action": assessment.next_action.value,
        "owner": assessment.owner.value if assessment.owner is not None else None,
        "recovery_result": recovery_payload,
        "scope": assessment.scope.value,
        "session_id": assessment.session_id,
        "state": assessment.state.value,
    }


def _session_transition_conversation_storage(
    workspace: Workspace,
    session_id: str,
) -> tuple[str | None, ConversationStorageServices | None]:
    """Resolve one persisted active default and its same-provenance services."""

    conversation_id = load_default_conversation(workspace, session_id)
    if conversation_id is None:
        return None, None
    services = activate_configured_conversation_storage(workspace).services
    try:
        metadata = services.catalog.get(session_id, conversation_id)
    except ConversationNotFoundError:
        return None, None
    if (
        metadata.session_id != session_id
        or metadata.conversation_id != conversation_id
    ):
        raise BackendProtocolError(
            "conversation storage returned metadata for another scope"
        )
    if metadata.status.value == "archived":
        return None, None
    if metadata.status.value != "active":
        raise BackendProtocolError(
            "conversation storage returned an invalid conversation status"
        )
    return conversation_id, services


def _interactive_streams_are_terminals() -> bool:
    """Return whether stdin and stdout safely report terminal capability.

    The check performs no reads, writes, configuration access, or mutation.
    Missing or failing ``isatty`` implementations are treated as non-terminals
    so callers can emit the canonical automation-safe error.
    """

    try:
        return bool(sys.stdin.isatty()) and bool(sys.stdout.isatty())
    except Exception:
        return False


def _reject_unexpected_startup_input(_prompt: str) -> str:
    """Guard the stderr-only startup renderer from accidental input reads."""

    raise RuntimeError("startup error rendering must not read input")


def _write_interactive_cli_error(
    error: InteractiveError,
    *,
    debug_log_available: bool = False,
) -> None:
    """Render one sanitized interactive startup error only on stderr.

    The helper borrows stderr for a single record and uses a reader that always
    fails if invoked. It performs no workspace, configuration, or provider
    access, and writer failures propagate to the process boundary.
    """

    terminal = InteractiveTerminal(
        _reject_unexpected_startup_input,
        sys.stderr.write,
    )
    terminal.write_error(
        error,
        debug_log_path=(_INTERACTIVE_DEBUG_LOG_NOTICE if debug_log_available else None),
    )


def _record_cli_error(
    debug_log: WorkspaceDebugLog | None,
    operation: DebugOperation,
    failure: BaseException,
    *,
    session_id: str | None = None,
) -> None:
    """Record a CLI failure without allowing diagnostics to replace it."""

    if debug_log is None:
        return
    try:
        debug_log.error(
            DebugComponent.CLI,
            operation,
            failure,
            session_id=session_id,
            outcome=DebugOutcome.FAILED,
        )
    except BaseException:
        pass


def _create_cli_debug_log(workspace: Workspace) -> WorkspaceDebugLog | None:
    """Create one optional CLI logger without changing command behavior."""

    try:
        return WorkspaceDebugLog(workspace, defer_until_session=True)
    except BaseException:
        return None


def _trace_cli(
    debug_log: WorkspaceDebugLog | None,
    operation: DebugOperation,
    phase: DebugPhase,
    *,
    session_id: str | None = None,
    outcome: DebugOutcome | None = None,
) -> None:
    """Record a CLI trace without allowing diagnostics to alter behavior."""

    if debug_log is None:
        return
    try:
        debug_log.trace(
            component=DebugComponent.CLI,
            operation=operation,
            phase=phase,
            session_id=session_id,
            outcome=outcome,
        )
    except BaseException:
        pass


def _trace_cli_completion(
    debug_log: WorkspaceDebugLog | None,
    *,
    session_id: str | None = None,
    outcome: DebugOutcome = DebugOutcome.COMPLETED,
) -> None:
    """Record one successful post-workspace command completion."""

    _trace_cli(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.SUCCEEDED,
        session_id=session_id,
        outcome=outcome,
    )


def _classify_workspace_entry_failure(failure: Exception) -> InteractiveError:
    """Map workspace discovery types to safe startup codes without raw prose.

    Discovery ``ConfigurationError`` means no initialized workspace was found;
    security and persisted-format/I/O failures retain their specific codes.
    The function has no side effects.
    """

    if isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    elif isinstance(failure, ConfigurationError):
        code = InteractiveErrorCode.WORKSPACE_NOT_INITIALIZED
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


def _classify_interactive_configuration_failure(
    failure: Exception,
) -> InteractiveError:
    """Map context/override/backend setup types to safe startup codes.

    No exception message is inspected or displayed. Workspace security and I/O
    remain distinct, known value/configuration failures are configuration
    errors, and unexpected failures use ``internal_error`` without mutation.
    """

    if isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    elif isinstance(
        failure,
        (
            BackendProtocolError,
            ConfigurationError,
            SessionIdError,
            TypeError,
            ValueError,
        ),
    ):
        code = InteractiveErrorCode.CONFIGURATION_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


def _classify_interactive_gate_failure(
    failure: Exception,
) -> InteractiveError:
    """Map auth-gate configuration persistence to fixed startup codes."""

    if isinstance(failure, SettingsError):
        if failure.kind in {
            SettingsErrorKind.VALIDATION,
            SettingsErrorKind.CONFLICT,
        }:
            code = InteractiveErrorCode.CONFIGURATION_INVALID
        else:
            code = InteractiveErrorCode.WORKSPACE_INVALID
        return InteractiveError(code)
    return _classify_interactive_configuration_failure(failure)


def _classify_conversation_storage_startup_failure(
    failure: Exception,
) -> InteractiveError:
    """Map startup storage failures to sanitized, actionable recovery copy."""

    if isinstance(failure, WorkspaceSecurityError):
        return InteractiveError(InteractiveErrorCode.WORKSPACE_UNSAFE)
    if isinstance(failure, WorkspaceVersionUnsupportedError):
        return InteractiveError(InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED)
    if isinstance(failure, (WorkspaceFormatError, OSError)):
        return InteractiveError(InteractiveErrorCode.WORKSPACE_INVALID)
    if isinstance(failure, ConfigurationError):
        return InteractiveError(InteractiveErrorCode.CONFIGURATION_INVALID)
    return InteractiveError(
        InteractiveErrorCode.CONFIGURATION_INVALID,
        summary="Conversation storage activation could not be completed.",
        hint=(
            "Run story storage use, transfer, or verify as appropriate, then "
            "retry after concurrent workspace storage work finishes."
        ),
    )


def _newly_authenticated_provider(
    before: ProviderAuthSnapshot,
    result: InteractiveAuthResult,
    after: ProviderAuthSnapshot,
) -> str | None:
    """Return the exact provider whose explicit interaction gained auth."""

    provider = result.provider
    if (
        result.kind is InteractiveAuthResultKind.AUTHENTICATED
        and provider is not None
        and not before.is_authenticated(provider)
        and after.is_authenticated(provider)
    ):
        return provider
    return None


def _authenticate_initial_provider(
    coordinator: InteractiveAuthCoordinator,
) -> tuple[ProviderAuthSnapshot, str | None] | None:
    """Remain in the explicit auth picker until one provider authenticates.

    ``None`` represents only explicit interrupt or EOF. Picker cancellation and
    every nonauthenticated result receive a fresh full probe and stay gated.
    """

    snapshot = coordinator.probe_all()
    newly_authenticated: str | None = None
    while not snapshot.any_authenticated:
        before = snapshot
        result = coordinator.run(snapshot)
        if result.kind in {
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        }:
            return None
        snapshot = coordinator.probe_all()
        newly_authenticated = _newly_authenticated_provider(
            before,
            result,
            snapshot,
        )
    return snapshot, newly_authenticated


def _effective_interactive_providers(
    manager: ConfigManager,
    snapshot: ConfigurationSnapshot,
    backend_overrides: dict[str, str],
) -> tuple[str, ...]:
    """Resolve all roster routes and return distinct providers in roster order."""

    providers: list[str] = []
    for agent_id in ROSTER:
        provider = manager.resolve_from_snapshot(
            snapshot,
            agent_id,
            backend_overrides=backend_overrides,
        ).backend
        if provider not in providers:
            providers.append(provider)
    return tuple(providers)


def _authenticate_effective_providers(
    coordinator: InteractiveAuthCoordinator,
    providers: tuple[str, ...],
) -> bool:
    """Freshly hard-gate every effective provider until auth or explicit exit."""

    snapshot = coordinator.probe_all()
    while True:
        provider = next(
            (
                candidate
                for candidate in providers
                if not snapshot.is_authenticated(candidate)
            ),
            None,
        )
        if provider is None:
            return True
        result = coordinator.run(
            snapshot,
            selected_provider=provider,
        )
        if result.kind in {
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        }:
            return False
        snapshot = coordinator.probe_all()


def _close_unstarted_interactive_backend(backend: AgentBackend) -> bool:
    """Close a backend not transferred to a controller; report safe failure.

    Returns ``True`` when cleanup completes. A close failure emits exactly one
    ``cleanup_failed`` record on stderr and never exposes exception details.
    """

    try:
        backend.close()
    except Exception:
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.CLEANUP_FAILED),
            debug_log_available=True,
        )
        return False
    return True


def _run_interactive_startup_progress(
    terminal: InteractiveTerminal,
    operation: Callable[[], _StartupT],
) -> _StartupT:
    """Run one blocking startup operation under terminal-owned progress."""

    if not isinstance(terminal, InteractiveTerminal):
        raise TypeError("terminal must be an InteractiveTerminal")
    if not callable(operation):
        raise TypeError("startup operation must be callable")

    async def run() -> object:
        """Keep the progress loop responsive while startup work blocks."""

        progress = InteractiveAdvisoryProgress(terminal)
        return await progress.run(asyncio.to_thread(operation))

    with asyncio.Runner() as runner:
        return cast(_StartupT, runner.run(run()))


def _run_interactive_cli(arguments: argparse.Namespace) -> int:
    """Construct and transfer one guarded interactive session-lobby lifecycle.

    Both streams are checked before input, option validation, workspace access,
    or backend construction. All interactive values validate before workspace
    discovery. One workspace-bound authentication coordinator then owns the
    welcome and hard gates for initial and effective providers. Conversation
    storage activates under terminal-owned progress before provider auth;
    context, backend, and controller construction remain behind auth and share
    a second progress span. Once
    ``run_session_lobby`` is called, the controller owns backend cleanup;
    before that transfer this adapter closes any constructed backend.
    """

    if not _interactive_streams_are_terminals():
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.TTY_REQUIRED)
        )
        return 2

    if arguments.command == "chat":
        workspace_option = arguments.workspace
        backend_values = arguments.backend
        model = arguments.codex_model
        antigravity_executable = arguments.antigravity_executable
        antigravity_model = arguments.antigravity_model
        antigravity_effort = arguments.antigravity_effort
        timeout_value = arguments.codex_timeout
        requested_session = arguments.session_name
    else:
        workspace_option = None
        backend_values = ()
        model = None
        antigravity_executable = "agy"
        antigravity_model = None
        antigravity_effort = None
        timeout_value = 300
        requested_session = None

    try:
        backend_overrides = _parse_backend_overrides(backend_values)
        timeout_seconds = _parse_positive_timeout_seconds(timeout_value)
        if requested_session is not None:
            requested_session = Workspace.validate_identifier(requested_session)
        model = ConfigManager.validate_codex_model(model)
        antigravity_model = ConfigManager.validate_antigravity_model(antigravity_model)
        antigravity_effort = ConfigManager.validate_antigravity_effort(
            antigravity_effort
        )
        antigravity_executable = _validate_antigravity_executable(
            antigravity_executable
        )
    except Exception as exc:
        _write_interactive_cli_error(_classify_interactive_configuration_failure(exc))
        return 2

    try:
        workspace = resolve_workspace(workspace_option)
    except Exception as exc:
        _write_interactive_cli_error(_classify_workspace_entry_failure(exc))
        return 2
    debug_log = _create_cli_debug_log(workspace)
    _trace_cli(
        debug_log,
        DebugOperation.WORKSPACE_DISCOVERY,
        DebugPhase.SUCCEEDED,
        outcome=DebugOutcome.READY,
    )
    _trace_cli(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.STARTED,
    )

    try:
        terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
    except Exception as exc:
        _record_cli_error(debug_log, DebugOperation.CLI_COMMAND, exc)
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR),
            debug_log_available=True,
        )
        return 2

    try:
        storage_startup = _run_interactive_startup_progress(
            terminal,
            lambda: activate_configured_conversation_storage(workspace),
        )
    except KeyboardInterrupt:
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.CANCELLED,
            outcome=DebugOutcome.INTERRUPTED,
        )
        return 0
    except Exception as exc:
        if isinstance(exc, AdvisoryProgressError):
            _record_cli_error(debug_log, DebugOperation.CLI_COMMAND, exc)
            error = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        else:
            _record_cli_error(debug_log, DebugOperation.CONFIGURATION, exc)
            error = _classify_conversation_storage_startup_failure(exc)
        _write_interactive_cli_error(
            error,
            debug_log_available=True,
        )
        return 2

    try:
        auth_service = ProviderAuthService(
            workspace.root,
            antigravity_executable=antigravity_executable,
        )
        catalog_service = ProviderCatalogService(
            workspace.root,
            antigravity_executable=antigravity_executable,
        )
        auth_coordinator = InteractiveAuthCoordinator(
            auth_service,
            terminal,
        )
    except Exception as exc:
        _record_cli_error(debug_log, DebugOperation.CONFIGURATION, exc)
        _write_interactive_cli_error(
            _classify_interactive_configuration_failure(exc),
            debug_log_available=True,
        )
        return 2

    try:
        auth_coordinator.show_welcome()
        initial_auth = _authenticate_initial_provider(auth_coordinator)
    except KeyboardInterrupt:
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.CANCELLED,
            outcome=DebugOutcome.INTERRUPTED,
        )
        return 0
    except Exception as exc:
        _record_cli_error(debug_log, DebugOperation.CONFIGURATION, exc)
        _write_interactive_cli_error(
            _classify_interactive_configuration_failure(exc),
            debug_log_available=True,
        )
        return 2
    if initial_auth is None:
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.CANCELLED,
            outcome=DebugOutcome.INTERRUPTED,
        )
        return 0

    snapshot, newly_authenticated = initial_auth
    try:
        default_provider = select_default_provider(
            snapshot,
            newly_authenticated_provider=newly_authenticated,
        )
        if default_provider is None:
            raise RuntimeError("authenticated startup snapshot has no default provider")
        manager = ConfigManager(workspace)
        configuration = manager.load_configuration_snapshot()
        runtime_backend_overrides = dict(backend_overrides)
        if configuration.system_defaults.default_backend is None:
            for agent_id, agent in configuration.agent_configs.items():
                if agent.backend is None:
                    runtime_backend_overrides.setdefault(agent_id, default_provider)
        effective_providers = _effective_interactive_providers(
            manager,
            configuration,
            runtime_backend_overrides,
        )
    except KeyboardInterrupt:
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.CANCELLED,
            outcome=DebugOutcome.INTERRUPTED,
        )
        return 0
    except Exception as exc:
        _record_cli_error(debug_log, DebugOperation.CONFIGURATION, exc)
        _write_interactive_cli_error(
            _classify_interactive_gate_failure(exc),
            debug_log_available=True,
        )
        return 2

    try:
        gate_ready = _authenticate_effective_providers(
            auth_coordinator,
            effective_providers,
        )
    except KeyboardInterrupt:
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.CANCELLED,
            outcome=DebugOutcome.INTERRUPTED,
        )
        return 0
    except Exception as exc:
        _record_cli_error(debug_log, DebugOperation.CONFIGURATION, exc)
        _write_interactive_cli_error(
            _classify_interactive_configuration_failure(exc),
            debug_log_available=True,
        )
        return 2
    if not gate_ready:
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.CANCELLED,
            outcome=DebugOutcome.INTERRUPTED,
        )
        return 0

    startup_phase = "context"
    unstarted_backend: AgentBackend | None = None

    def prepare_interactive_runtime() -> tuple[
        CliContext,
        AgentBackend,
        InteractiveController,
    ]:
        """Construct authenticated runtime state inside one progress span."""

        nonlocal startup_phase, unstarted_backend
        cli_context = load_cli_context(workspace)
        _trace_cli(
            debug_log,
            DebugOperation.CLI_CONTEXT,
            DebugPhase.SUCCEEDED,
            outcome=DebugOutcome.COMPLETED,
        )
        startup_phase = "backend"
        backend = AgentBackend(
            workspace,
            model=model,
            timeout_seconds=timeout_seconds,
            backend_overrides=runtime_backend_overrides,
            antigravity_executable=antigravity_executable,
            antigravity_model=antigravity_model,
            antigravity_effort=antigravity_effort,
        )
        unstarted_backend = backend
        startup_phase = "controller"
        controller = InteractiveController(
            workspace,
            cli_context,
            backend,
            terminal,
            conversation_storage_services=storage_startup.services,
            auth_coordinator=auth_coordinator,
            provider_catalog=catalog_service,
        )
        startup_phase = "complete"
        return cli_context, backend, controller

    try:
        cli_context, backend, controller = _run_interactive_startup_progress(
            terminal,
            prepare_interactive_runtime,
        )
    except Exception as exc:
        if isinstance(exc, AdvisoryProgressError):
            _record_cli_error(debug_log, DebugOperation.CLI_COMMAND, exc)
            error = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        elif startup_phase == "context":
            _record_cli_error(debug_log, DebugOperation.CLI_CONTEXT, exc)
            error = _classify_interactive_configuration_failure(exc)
        elif startup_phase == "backend":
            _record_cli_error(debug_log, DebugOperation.CONFIGURATION, exc)
            error = _classify_interactive_configuration_failure(exc)
        else:
            _record_cli_error(debug_log, DebugOperation.CLI_COMMAND, exc)
            error = InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        _write_interactive_cli_error(
            error,
            debug_log_available=True,
        )
        if unstarted_backend is not None:
            _close_unstarted_interactive_backend(unstarted_backend)
        return 2

    try:
        result = controller.run_session_lobby(requested_session=requested_session)
    except Exception as exc:
        _record_cli_error(debug_log, DebugOperation.INTERACTIVE_LOBBY, exc)
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR),
            debug_log_available=True,
        )
        return 2
    _trace_cli(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.SUCCEEDED if result == 0 else DebugPhase.FAILED,
        outcome=(DebugOutcome.COMPLETED if result == 0 else DebugOutcome.FAILED),
    )
    return result


def _run_conversation_rag_cli(arguments: argparse.Namespace) -> int:
    """Resolve CLI scope and emit exactly one sanitized schema-v1 report."""

    operation = ConversationRagCliOperation(arguments.rag_action)
    try:
        workspace = resolve_workspace(arguments.workspace)
    except Exception:
        report = ConversationRagCliReport.failed(
            operation,
            ConversationRagCliFailure.WORKSPACE_INVALID,
        )
        print(report.to_json())
        return report.exit_code

    session_id: str | None = None
    if operation in {
        ConversationRagCliOperation.STATUS,
        ConversationRagCliOperation.REBUILD,
        ConversationRagCliOperation.SEARCH,
        ConversationRagCliOperation.PURGE,
    }:
        try:
            session_id = resolve_session(
                workspace=workspace,
                context=load_cli_context(workspace),
                option_session=arguments.session,
            )
        except Exception:
            report = ConversationRagCliReport.failed(
                operation,
                ConversationRagCliFailure.SESSION_INVALID,
            )
            print(report.to_json())
            return report.exit_code

    try:
        request = ConversationRagCliRequest(
            operation=operation,
            session_id=session_id,
            provider=getattr(arguments, "provider", None),
            query=getattr(arguments, "query", None),
            confirmed=bool(getattr(arguments, "yes", False)),
        )
    except Exception:
        report = ConversationRagCliReport.failed(
            operation,
            ConversationRagCliFailure.INVALID_REQUEST,
        )
        print(report.to_json())
        return report.exit_code

    services = None
    if operation is ConversationRagCliOperation.SEARCH or (
        operation is ConversationRagCliOperation.REBUILD and request.confirmed
    ):
        try:
            services = activate_configured_conversation_storage(workspace).services
        except (KeyboardInterrupt, SystemExit):
            raise
        except ConversationStorageCoordinationError:
            report = ConversationRagCliReport.failed(
                operation,
                ConversationRagCliFailure.COORDINATION_FAILED,
            )
            print(report.to_json())
            return report.exit_code
        except Exception:
            report = ConversationRagCliReport.failed(
                operation,
                ConversationRagCliFailure.STORAGE_UNAVAILABLE,
            )
            print(report.to_json())
            return report.exit_code

    report = dispatch_conversation_rag(
        workspace,
        request,
        conversation_storage_services=services,
    )
    print(report.to_json())
    return report.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one CLI invocation through the canonical command contracts.

    Bare and ``chat`` invocations use the guarded interactive adapter. Argparse
    owns help and syntax exits; runtime failures use the stable status-2 path.
    """

    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None or arguments.command == "chat":
        return _run_interactive_cli(arguments)
    if arguments.command == "rag":
        return _run_conversation_rag_cli(arguments)
    debug_log: WorkspaceDebugLog | None = None
    debug_log_hint_available = False
    session_id: str | None = None
    try:
        if arguments.command == "init":
            workspace = initialize_workspace(arguments.workspace)
            print(json.dumps({"workspace": str(workspace.root)}, indent=2))
            return 0

        if arguments.command == "smoke":
            from .smoke import run_smoke

            if arguments.workspace is None:
                report, exit_code = run_smoke()
            else:
                workspace = resolve_workspace(arguments.workspace)
                report, exit_code = run_smoke(workspace)
            print(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            if debug_log is not None:
                _trace_cli(
                    debug_log,
                    DebugOperation.CLI_COMMAND,
                    (DebugPhase.SUCCEEDED if exit_code == 0 else DebugPhase.FAILED),
                    outcome=(
                        DebugOutcome.COMPLETED
                        if exit_code == 0
                        else DebugOutcome.FAILED
                    ),
                )
            return exit_code

        if arguments.command == "storage":
            if arguments.storage_action == "use":
                try:
                    desired = ConfigManager.validate_conversation_storage_backend(
                        arguments.backend
                    )
                except Exception:
                    use_report = ConversationStorageSelectionReport.failed(
                        ConversationStorageSelectionFailure.INVALID_REQUEST
                    )
                else:
                    assert desired is not None
                    install_missing = arguments.install_missing
                    yes = arguments.yes
                    if install_missing != yes or (
                        desired != "sqlite" and (install_missing or yes)
                    ):
                        use_report = ConversationStorageSelectionReport.failed(
                            ConversationStorageSelectionFailure.INVALID_REQUEST,
                            desired_backend=desired,
                        )
                    else:
                        try:
                            workspace = resolve_workspace(arguments.workspace)
                        except Exception:
                            use_report = ConversationStorageSelectionReport.failed(
                                ConversationStorageSelectionFailure.WORKSPACE_INVALID,
                                desired_backend=desired,
                            )
                        else:
                            use_report = select_conversation_storage(
                                workspace,
                                desired,
                                install_missing=install_missing,
                                yes=yes,
                                diagnostic_stream=sys.stderr,
                            )
                print(use_report.to_json())
                return use_report.exit_code

            from .conversation_storage.migration import (
                ConversationStorageTransferOperation,
                ConversationStorageTransferReport,
                transfer_conversation_storage,
                verify_conversation_storage,
            )

            operation = ConversationStorageTransferOperation(arguments.storage_action)
            dry_run = bool(getattr(arguments, "dry_run", False))
            try:
                workspace = resolve_workspace(arguments.workspace)
            except Exception:
                storage_report = ConversationStorageTransferReport.failed(
                    operation=operation,
                    source_backend=arguments.source_backend,
                    destination_backend=arguments.destination_backend,
                    dry_run=dry_run,
                    failure="workspace_invalid",
                )
            else:
                storage_report = (
                    transfer_conversation_storage(
                        workspace,
                        source_backend=arguments.source_backend,
                        destination_backend=arguments.destination_backend,
                        dry_run=dry_run,
                    )
                    if operation is ConversationStorageTransferOperation.TRANSFER
                    else verify_conversation_storage(
                        workspace,
                        source_backend=arguments.source_backend,
                        destination_backend=arguments.destination_backend,
                    )
                )
            print(storage_report.to_json())
            return storage_report.exit_code

        workspace = resolve_workspace(arguments.workspace)
        debug_log_hint_available = True
        debug_log = _create_cli_debug_log(workspace)
        _trace_cli(
            debug_log,
            DebugOperation.WORKSPACE_DISCOVERY,
            DebugPhase.SUCCEEDED,
            outcome=DebugOutcome.READY,
        )
        _trace_cli(
            debug_log,
            DebugOperation.CLI_COMMAND,
            DebugPhase.STARTED,
        )
        cli_context = load_cli_context(workspace)
        _trace_cli(
            debug_log,
            DebugOperation.CLI_CONTEXT,
            DebugPhase.SUCCEEDED,
            outcome=DebugOutcome.COMPLETED,
        )
        if arguments.command == "session":
            if arguments.session_name is not None:
                session_id = workspace.validate_identifier(arguments.session_name)
            elif cli_context.default_session_id is not None:
                session_id = workspace.validate_identifier(
                    cli_context.default_session_id
                )
            else:
                raise ConfigurationError(
                    "no default session; provide SESSION or run "
                    "'story session create SESSION'"
                )
            backend = AgentBackend(workspace)
            assessment: SessionTransitionAssessment | None = None
            denied = False
            try:
                if arguments.session_action == "create":
                    _require_session_command_state(
                        backend.get_session_lifecycle(session_id),
                        session_id=session_id,
                        durable_state=SessionDurableState.ABSENT,
                        access_state=SessionAccessState.CLAIMABLE,
                    )
                    if backend.validate_provider_configuration(session_id) is not True:
                        raise BackendProtocolError(
                            "provider configuration validation did not return true"
                        )
                    backend.provision_session_resources(session_id)
                    acquired = _require_session_command_state(
                        backend.create_session_with_access(session_id),
                        session_id=session_id,
                        durable_state=SessionDurableState.READY,
                        access_state=SessionAccessState.HELD_HERE,
                    )
                    observed = backend.get_session_lifecycle(session_id)
                    if observed != acquired:
                        raise BackendProtocolError(
                            "created session lifecycle changed before release"
                        )
                    try:
                        assessment = backend.assess_session_transition(
                            lifecycle_state=acquired,
                            requested_operation=(
                                SessionTransitionOperation.ENTER_DESTINATION
                            ),
                            conversation_id=None,
                            collaboration_store=None,
                        )
                        denied = (
                            SessionTransitionOperation.ENTER_DESTINATION
                            not in assessment.allowed_operations
                        )
                        if not denied:
                            save_default_session(workspace, session_id)
                    finally:
                        state = backend.release_session_access(session_id)
                elif arguments.session_action == "reopen":
                    before = backend.get_session_lifecycle(session_id)
                    if type(before) is not SessionLifecycleState:
                        raise BackendProtocolError(
                            "session command returned an invalid lifecycle state"
                        )
                    provider_work_is_ready(
                        before,
                        provider_configuration_ready=False,
                    )
                    if (
                        before.session_id != session_id
                        or before.durable_state is SessionDurableState.ABSENT
                    ):
                        raise BackendProtocolError(
                            "session command cannot reopen an absent session"
                        )
                    if before.access_state in {
                        SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER,
                        SessionAccessState.OWNERSHIP_UNKNOWN,
                    }:
                        denied = True
                        state = before
                    else:
                        if backend.validate_provider_configuration(session_id) is not True:
                            raise BackendProtocolError(
                                "provider configuration validation did not return true"
                            )
                        transition = (
                            backend.reopen_session_with_access(session_id)
                            if before.durable_state is SessionDurableState.PAUSED
                            else backend.acquire_session_access(session_id)
                        )
                        acquired = _require_session_command_state(
                            transition,
                            session_id=session_id,
                            durable_state=SessionDurableState.READY,
                            access_state=SessionAccessState.HELD_HERE,
                        )
                        observed = backend.get_session_lifecycle(session_id)
                        if observed != acquired:
                            raise BackendProtocolError(
                                "reopened session lifecycle changed before release"
                            )
                        try:
                            conversation_id, storage_services = (
                                _session_transition_conversation_storage(
                                    workspace,
                                    session_id,
                                )
                            )
                            assessment = backend.assess_session_transition(
                                lifecycle_state=acquired,
                                requested_operation=(
                                    SessionTransitionOperation.ENTER_DESTINATION
                                ),
                                conversation_id=conversation_id,
                                collaboration_store=(
                                    storage_services.event_store
                                    if storage_services is not None
                                    else None
                                ),
                            )
                            denied = (
                                SessionTransitionOperation.ENTER_DESTINATION
                                not in assessment.allowed_operations
                            )
                            if not denied:
                                save_default_session(workspace, session_id)
                        finally:
                            state = backend.release_session_access(session_id)
                else:
                    before = backend.get_session_lifecycle(session_id)
                    if type(before) is not SessionLifecycleState:
                        raise BackendProtocolError(
                            "session command returned an invalid lifecycle state"
                        )
                    provider_work_is_ready(
                        before,
                        provider_configuration_ready=False,
                    )
                    if before.session_id != session_id:
                        raise BackendProtocolError(
                            "session command returned another lifecycle identity"
                        )
                    if (
                        before.durable_state is SessionDurableState.READY
                        and before.access_state is SessionAccessState.CLAIMABLE
                    ):
                        current = _require_session_command_state(
                            backend.acquire_session_access(session_id),
                            session_id=session_id,
                            durable_state=SessionDurableState.READY,
                            access_state=SessionAccessState.HELD_HERE,
                        )
                        newly_acquired = True
                    elif (
                        before.durable_state is SessionDurableState.READY
                        and before.access_state is SessionAccessState.HELD_HERE
                    ):
                        current = before
                        newly_acquired = False
                    else:
                        denied = True
                        state = before
                    if not denied:
                        conversation_id, storage_services = (
                            _session_transition_conversation_storage(
                                workspace,
                                session_id,
                            )
                        )
                        assessment = backend.assess_session_transition(
                            lifecycle_state=current,
                            requested_operation=(
                                SessionTransitionOperation.PAUSE_SESSION
                            ),
                            conversation_id=conversation_id,
                            collaboration_store=(
                                storage_services.event_store
                                if storage_services is not None
                                else None
                            ),
                        )
                        denied = (
                            SessionTransitionOperation.PAUSE_SESSION
                            not in assessment.allowed_operations
                        )
                        if denied:
                            state = (
                                backend.release_session_access(session_id)
                                if newly_acquired
                                else before
                            )
                        else:
                            state = backend.pause_session_and_release_access(session_id)
            finally:
                backend.close()
            if denied:
                print(
                    json.dumps(
                        {
                            "access_state": state.access_state.value,
                            "assessment": (
                                _transition_assessment_payload(assessment)
                                if assessment is not None
                                else None
                            ),
                            "durable_state": state.durable_state.value,
                            "session_id": state.session_id,
                            "transition_denial": (
                                None
                                if assessment is not None
                                else "lifecycle_not_ready_held_here"
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
                _trace_cli_completion(
                    debug_log,
                    session_id=session_id,
                    outcome=DebugOutcome.REJECTED,
                )
                return 2
            expected_durable_state = (
                SessionDurableState.PAUSED
                if arguments.session_action == "pause"
                else SessionDurableState.READY
            )
            state = _require_session_command_state(
                state,
                session_id=session_id,
                durable_state=expected_durable_state,
                access_state=SessionAccessState.CLAIMABLE,
            )
            assert assessment is not None
            print(
                json.dumps(
                    {
                        "access_state": state.access_state.value,
                        "assessment": _transition_assessment_payload(assessment),
                        "durable_state": state.durable_state.value,
                        "session_id": state.session_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            _trace_cli_completion(debug_log, session_id=session_id)
            return 0

        runtime = StoryRuntime(workspace)
        session_id = resolve_session(
            workspace=workspace,
            context=cli_context,
            option_session=arguments.session,
        )
        if arguments.command == "directive":
            state = runtime.apply_directive(session_id, arguments.text)
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            _trace_cli_completion(debug_log, session_id=session_id)
            return 0

        if arguments.command == "inspect":
            value = (
                runtime.inspect_debate(session_id, arguments.debate)
                if arguments.debate
                else runtime.inspect_session(session_id)
            )
            print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
            _trace_cli_completion(debug_log, session_id=session_id)
            return 0

        raise ConfigurationError(f"unsupported command: {arguments.command}")
    except Exception as exc:
        if debug_log is not None:
            _record_cli_error(
                debug_log,
                DebugOperation.CLI_COMMAND,
                exc,
                session_id=session_id,
            )
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if debug_log_hint_available:
            print(_NONINTERACTIVE_DEBUG_LOG_HINT, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
