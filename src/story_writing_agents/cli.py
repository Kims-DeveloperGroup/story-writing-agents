"""Thin command-line interface over the public runtime API."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from .backend import load_backend
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

    directive = subcommands.add_parser(
        "directive",
        help="apply one exact standalone runtime directive",
    )
    directive.add_argument("--workspace", default=".")
    directive.add_argument("--session", required=True)
    directive.add_argument("text")

    run = subcommands.add_parser(
        "run",
        help="run a prompt or draft through an AgentBackend adapter",
    )
    run.add_argument("--workspace", default=".")
    run.add_argument("--session", required=True)
    run.add_argument(
        "--backend",
        required=True,
        metavar="MODULE:ATTRIBUTE",
        help="trusted async AgentBackend plugin (required; no provider is bundled)",
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "init":
            workspace = initialize_workspace(arguments.workspace)
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

        backend = load_backend(arguments.backend)
        runtime = StoryRuntime(workspace, backend)
        options = {
            "manuscript_name": arguments.output,
            "max_debate_rounds": arguments.max_debate_rounds,
            "debate_id": arguments.debate_id,
        }
        result = asyncio.run(
            runtime.run_prompt(arguments.session, arguments.prompt, **options)
            if arguments.prompt is not None
            else runtime.run_draft(arguments.session, arguments.draft, **options)
        )
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
