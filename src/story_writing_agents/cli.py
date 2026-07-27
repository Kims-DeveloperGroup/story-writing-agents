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
from .config import ConfigManager
from .errors import ConfigurationError
from .models import DebateStatus
from .orchestrator import StoryRuntime
from .workspace import Workspace, initialize_workspace


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
    directive.add_argument("--workspace", default=".")
    directive.add_argument("--session", required=True)
    directive.add_argument("text")

    run = subcommands.add_parser(
        "run",
        help="run a prompt or draft through the configured agent backends",
    )
    run.add_argument("--workspace", default=".")
    run.add_argument("--session", required=True)
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
    payload = run.add_mutually_exclusive_group(required=True)
    payload.add_argument("--prompt")
    payload.add_argument("--draft")
    run.add_argument("--output", metavar="MANUSCRIPT_PATH")
    run.add_argument("--debate-id")
    run.add_argument("--max-debate-rounds", type=int)

    inspect = subcommands.add_parser(
        "inspect",
        help="inspect persisted session or debate state",
    )
    inspect.add_argument("--workspace", default=".")
    inspect.add_argument("--session", required=True)
    inspect.add_argument("--debate")

    session = subcommands.add_parser(
        "session",
        help="start or end a persistent provider session",
    )
    session_actions = session.add_subparsers(dest="session_action", required=True)
    for action in ("start", "end"):
        action_parser = session_actions.add_parser(action)
        action_parser.add_argument("--workspace", default=".")
        action_parser.add_argument("--session", required=True)

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

        workspace = Workspace(Path(arguments.workspace))
        if arguments.command == "session":
            backend = AgentBackend(workspace)
            try:
                manifest = (
                    backend.start_session(arguments.session)
                    if arguments.session_action == "start"
                    else backend.end_session(arguments.session)
                )
            finally:
                backend.close()
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
        if arguments.command == "directive":
            state = runtime.apply_directive(arguments.session, arguments.text)
            print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        if arguments.command == "inspect":
            value = (
                runtime.inspect_debate(arguments.session, arguments.debate)
                if arguments.debate
                else runtime.inspect_session(arguments.session)
            )
            print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

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
                runtime.run_prompt(arguments.session, arguments.prompt, **options)
                if arguments.prompt is not None
                else runtime.run_draft(arguments.session, arguments.draft, **options)
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
