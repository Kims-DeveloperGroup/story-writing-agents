"""Build the installed command-line grammar without starting the runtime."""

from __future__ import annotations

import argparse

from .provider_adapter import DEFAULT_PROVIDER_TIMEOUT_SECONDS


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
        "--antigravity-executable",
        metavar="EXECUTABLE",
        default="agy",
    )
    chat.add_argument("--antigravity-model", metavar="MODEL")
    chat.add_argument("--antigravity-effort", metavar="EFFORT")
    chat.add_argument(
        "--codex-timeout",
        metavar="SECONDS",
        default=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        help=(
            "positive provider response timeout in seconds "
            f"(default: {DEFAULT_PROVIDER_TIMEOUT_SECONDS:g})"
        ),
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
