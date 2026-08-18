"""Thin command-line interface over the public runtime API."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from typing import Sequence

from .agents import ROSTER
from .backend import AgentBackend
from .cli_context import (
    load_cli_context,
    resolve_session,
    resolve_workspace,
    save_default_session,
)
from .config import ConfigManager
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from .interactive_controller import InteractiveController
from .interactive_terminal import (
    InteractiveError,
    InteractiveErrorCode,
    InteractiveTerminal,
    build_stream_terminal,
)
from .models import DebateStatus
from .orchestrator import StoryRuntime
from .run_inputs import read_draft_file
from .session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)
from .workspace import initialize_workspace


_WORKSPACE_HELP = (
    "explicit initialized workspace; omitted discovers the nearest ancestor"
)
_SESSION_HELP = "explicit session; omitted uses the workspace default"


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
    initialize.add_argument(
        "--overwrite-prompts",
        action="store_true",
        help="replace only the four canonical agent AGENTS.md files",
    )

    directive = subcommands.add_parser(
        "directive",
        help="apply one exact standalone runtime directive",
    )
    directive.add_argument("--workspace", help=_WORKSPACE_HELP)
    directive.add_argument("--session", help=_SESSION_HELP)
    directive.add_argument("text")

    run = subcommands.add_parser(
        "run",
        help="run a prompt or draft through the configured agent backends",
    )
    run.add_argument("--workspace", help=_WORKSPACE_HELP)
    run.add_argument("--session", help=_SESSION_HELP)
    run.add_argument(
        "--backend",
        action="append",
        default=[],
        metavar="AGENT=BACKEND",
        help="override one canonical agent backend for this run (repeatable)",
    )
    run.add_argument("--codex-model", metavar="MODEL")
    run.add_argument(
        "--codex-timeout",
        metavar="SECONDS",
        type=float,
        default=300,
    )
    run.add_argument(
        "positional_prompt",
        nargs="?",
        metavar="PROMPT",
        help="story prompt; mutually exclusive with the other input options",
    )
    payload = run.add_mutually_exclusive_group()
    payload.add_argument("--prompt", help="inline story prompt")
    payload.add_argument("--draft", help="inline complete draft")
    payload.add_argument(
        "--draft-file",
        metavar="PATH",
        help="strict-UTF-8 draft file relative to the current directory",
    )
    run.add_argument("--output", metavar="MANUSCRIPT_PATH")
    run.add_argument("--debate-id")
    run.add_argument("--max-debate-rounds", type=int)

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
        help=(
            "use an explicit workspace path and retain diagnostic artifacts"
        ),
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
            raise ConfigurationError(
                f"duplicate backend override agent: {agent_id}"
            )
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
        raise ConfigurationError(
            "Codex timeout must be a positive number"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("Codex timeout must be a positive number")
    return timeout


def _resolve_run_input(arguments: argparse.Namespace) -> tuple[str, str]:
    """Resolve exactly one established prompt/draft source without mutation.

    Inline values are returned unchanged. Draft files are read as strict UTF-8;
    ambiguous, missing, unreadable, or invalid input raises
    ``ConfigurationError`` before backend construction.
    """

    sources = (
        ("prompt", arguments.positional_prompt),
        ("prompt", arguments.prompt),
        ("draft", arguments.draft),
        ("draft_file", arguments.draft_file),
    )
    supplied = [(kind, value) for kind, value in sources if value is not None]
    if len(supplied) != 1:
        raise ConfigurationError(
            "exactly one run input is required: positional prompt, --prompt, "
            "--draft, or --draft-file"
        )
    input_kind, value = supplied[0]
    if not isinstance(value, str):
        raise ConfigurationError("run input must be text")
    if input_kind != "draft_file":
        return input_kind, value

    return "draft", read_draft_file(value)


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


def _write_interactive_cli_error(error: InteractiveError) -> None:
    """Render one sanitized interactive startup error only on stderr.

    The helper borrows stderr for a single record and uses a reader that always
    fails if invoked. It performs no workspace, configuration, or provider
    access, and writer failures propagate to the process boundary.
    """

    terminal = InteractiveTerminal(
        _reject_unexpected_startup_input,
        sys.stderr.write,
    )
    terminal.write_error(error)


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


def _close_unstarted_interactive_backend(backend: AgentBackend) -> bool:
    """Close a backend not transferred to a controller; report safe failure.

    Returns ``True`` when cleanup completes. A close failure emits exactly one
    ``cleanup_failed`` record on stderr and never exposes exception details.
    """

    try:
        backend.close()
    except Exception:
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.CLEANUP_FAILED)
        )
        return False
    return True


def _run_interactive_cli(arguments: argparse.Namespace) -> int:
    """Construct and transfer one guarded interactive session-lobby lifecycle.

    Both streams are checked before input, option validation, workspace/context
    access, or backend construction. The adapter constructs one unified
    stream-backed terminal whose sole raw driver provides contextual main and
    composition live editing while retaining the existing ``input`` callback
    for unsupported fallback; subject and confirmation reads stay line-
    oriented. Setup failures are rendered safely on stderr. Once
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
        timeout_value = arguments.codex_timeout
        requested_session = arguments.session_name
    else:
        workspace_option = None
        backend_values = ()
        model = None
        timeout_value = 300
        requested_session = None

    try:
        backend_overrides = _parse_backend_overrides(backend_values)
        timeout_seconds = _parse_positive_timeout_seconds(timeout_value)
    except Exception as exc:
        _write_interactive_cli_error(
            _classify_interactive_configuration_failure(exc)
        )
        return 2

    try:
        workspace = resolve_workspace(workspace_option)
    except Exception as exc:
        _write_interactive_cli_error(_classify_workspace_entry_failure(exc))
        return 2

    try:
        cli_context = load_cli_context(workspace)
    except Exception as exc:
        _write_interactive_cli_error(
            _classify_interactive_configuration_failure(exc)
        )
        return 2

    try:
        terminal = build_stream_terminal(input, sys.stdin, sys.stdout)
    except Exception:
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        )
        return 2

    try:
        backend = AgentBackend(
            workspace,
            model=model,
            timeout_seconds=timeout_seconds,
            backend_overrides=backend_overrides,
        )
    except Exception as exc:
        _write_interactive_cli_error(
            _classify_interactive_configuration_failure(exc)
        )
        return 2

    try:
        controller = InteractiveController(
            workspace,
            cli_context,
            backend,
            terminal,
        )
    except Exception:
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        )
        _close_unstarted_interactive_backend(backend)
        return 2

    try:
        return controller.run_session_lobby(
            requested_session=requested_session
        )
    except Exception:
        _write_interactive_cli_error(
            InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
        )
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one CLI invocation through the canonical command contracts.

    Bare and ``chat`` invocations use the guarded interactive adapter. Argparse
    owns help and syntax exits; runtime failures use the stable status-2 path.
    """

    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None or arguments.command == "chat":
        return _run_interactive_cli(arguments)
    try:
        if arguments.command == "init":
            workspace = initialize_workspace(
                arguments.workspace,
                overwrite_prompts=arguments.overwrite_prompts,
            )
            print(json.dumps({"workspace": str(workspace.root)}, indent=2))
            return 0

        if arguments.command == "smoke":
            from .smoke import run_smoke

            report, exit_code = run_smoke(arguments.workspace)
            print(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return exit_code

        workspace = resolve_workspace(arguments.workspace)
        cli_context = load_cli_context(workspace)
        if arguments.command == "session":
            if arguments.session_name is not None:
                session_id = workspace.validate_identifier(
                    arguments.session_name
                )
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
            try:
                if arguments.session_action == "create":
                    if backend.validate_provider_configuration(session_id) is not True:
                        raise BackendProtocolError(
                            "provider configuration validation did not return true"
                        )
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
                        save_default_session(workspace, session_id)
                    finally:
                        state = backend.release_session_access(session_id)
                elif arguments.session_action == "reopen":
                    if backend.validate_provider_configuration(session_id) is not True:
                        raise BackendProtocolError(
                            "provider configuration validation did not return true"
                        )
                    acquired = _require_session_command_state(
                        backend.reopen_session_with_access(session_id),
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
                        save_default_session(workspace, session_id)
                    finally:
                        state = backend.release_session_access(session_id)
                else:
                    state = backend.pause_session_and_release_access(session_id)
            finally:
                backend.close()
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
            print(
                json.dumps(
                    {
                        "access_state": state.access_state.value,
                        "durable_state": state.durable_state.value,
                        "session_id": state.session_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
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
            return 0

        if arguments.command == "inspect":
            value = (
                runtime.inspect_debate(session_id, arguments.debate)
                if arguments.debate
                else runtime.inspect_session(session_id)
            )
            print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        input_kind, input_payload = _resolve_run_input(arguments)
        backend_overrides = _parse_backend_overrides(arguments.backend)
        backend = AgentBackend(
            workspace,
            model=arguments.codex_model,
            timeout_seconds=arguments.codex_timeout,
            backend_overrides=backend_overrides,
        )
        runtime = StoryRuntime(workspace, backend)
        options = {
            "manuscript_name": arguments.output,
            "max_debate_rounds": arguments.max_debate_rounds,
            "debate_id": arguments.debate_id,
        }
        try:
            result = asyncio.run(
                runtime.run_prompt(session_id, input_payload, **options)
                if input_kind == "prompt"
                else runtime.run_draft(session_id, input_payload, **options)
            )
        finally:
            backend.close()
        print(
            json.dumps(
                result.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        if result.status is DebateStatus.CONSENSUS:
            return 0
        if result.status is DebateStatus.DEADLOCK:
            return 3
        return 4
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
