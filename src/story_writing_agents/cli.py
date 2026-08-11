"""Thin command-line interface over the public runtime API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from .agents import ROSTER
from .backend import AgentBackend
from .cli_context import (
    load_cli_context,
    resolve_session,
    resolve_workspace,
    select_session,
)
from .config import ConfigManager
from .errors import ConfigurationError
from .models import DebateStatus
from .orchestrator import StoryRuntime
from .workspace import initialize_workspace


_WORKSPACE_HELP = (
    "explicit initialized workspace; omitted discovers the nearest ancestor"
)
_SESSION_HELP = "explicit session; omitted uses the workspace selection"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="story-writing-agents")
    subcommands = parser.add_subparsers(dest="command", required=True)

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
        help="start or end a persistent provider session",
    )
    session_actions = session.add_subparsers(dest="session_action", required=True)
    for action in ("start", "end"):
        action_parser = session_actions.add_parser(action)
        action_parser.add_argument(
            "session_name",
            nargs="?",
            metavar="SESSION",
            help="session ID; omitted uses the workspace selection",
        )
        action_parser.add_argument("--workspace", help=_WORKSPACE_HELP)
        action_parser.add_argument("--session", help=_SESSION_HELP)

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


def _resolve_run_input(arguments: argparse.Namespace) -> tuple[str, str]:
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

    draft_path = Path(value).expanduser()
    try:
        content = draft_path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"draft file is not valid UTF-8: {draft_path}"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"cannot read draft file {draft_path}: {exc}"
        ) from exc
    return "draft", content


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
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
            session_id = resolve_session(
                workspace=workspace,
                context=cli_context,
                option_session=arguments.session,
                positional_session=arguments.session_name,
            )
            backend = AgentBackend(workspace)
            try:
                manifest = (
                    backend.start_session(session_id)
                    if arguments.session_action == "start"
                    else backend.end_session(session_id)
                )
            finally:
                backend.close()
            if arguments.session_action == "start":
                select_session(workspace, session_id)
            print(
                json.dumps(
                    {
                        "session_id": manifest["session_id"],
                        "active": manifest["active"],
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
