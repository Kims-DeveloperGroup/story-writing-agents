# CLI reference

> **Availability:** bare `story` and `story chat` open the installed TTY lobby.
> The shell manages sessions and conversations, sends routed single-line turns,
> searches active conversations, and reads bounded active or archived
> transcripts. `/history`, consensus, and artifact actions are not exposed.

Key terms: **bare entry**—`story` alone; **TTY**—interactive terminal;
**attachment**—this process currently owns a story-session lease;
**exit code**—process outcome.

`story` and `story-writing-agents` are exact aliases for
`story_writing_agents.cli:main`.

## Command syntax

| Command | Exact syntax |
| --- | --- |
| Interactive lobby | `story` |
| Interactive lobby with options | `story chat [SESSION] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS] [PROMPT | --prompt TEXT | --draft TEXT | --draft-file PATH] [--output PATH] [--debate-id ID] [--max-debate-rounds N]` |
| Inspect | `story inspect [--workspace PATH] [--session ID] [--debate ID]` |
| Start/end session | `story session (start | end) [ID] [--workspace PATH] [--session ID]` |
| Smoke | `story smoke [--workspace PATH]` |

`argparse` handles `-h`/`--help` and syntax errors before the TTY check. Bare
entry accepts no options; use `story chat` for session or runtime options.

## Interactive streams

Bare and `chat` entry require TTY standard input and output. This check occurs
before input, option validation, workspace/context reads, and backend
construction. Failure writes a typed error to standard error and exits `2`
without workspace access. Prompts, command status, and command errors use
standard output; startup and adapter errors use standard error. All are
sanitized; see [Terminal safety](../operations/terminal-safety.md).

`/agents`, help, session navigation, and quit are global and provider-free.
`/to NAME`, `/agent NAME`, conversation management, `/search QUERY`, and
`/transcript ID [COUNT] [--from SEQUENCE]` require an attachment but no selected
conversation. Search is active-only. Transcript references may explicitly name
active or archived records. Normal reads are provider-free and leave selection,
sticky target, and durable story state unchanged.

An ordinary line, `@AGENT TEXT`, or `/say TEXT` requires a selected active
conversation. Ordinary and `/say` use the sticky target; `@AGENT` is one-shot.
Each line waits for one `StoryRuntime.converse()` call with bounded pre-turn
context. The reply is displayed once and never reparsed. Conversation changes
keep the target; session attach, switch, and end reset it to lead.

The loop is synchronous per command. `/compose`, active-turn cancellation, a
persistent event loop, `/history`, debate, and artifact actions remain
unavailable. Use the [story-session guide](../guides/story-sessions.md), exact
[interactive grammar](interactive-command-parser.md),
[search/read contract](conversation-search.md), and
[terminal rendering contract](interactive-terminal.md).

## Workspace and session resolution

Except for `init` and `smoke`, the workspace must be initialized. Without an
explicit path, commands choose the nearest initialized ancestor. Bare entry
always discovers; `chat` accepts `--workspace`.

Session precedence is `--session`, the `session start|end` positional ID, then
saved selection; both explicit forms conflict. Successful start or shell
acquisition reselects; failure and end preserve selection. Each story session
has an independent lazy conversation selection, stored outside
`cli_context.json`; see [Session storage](configuration/session-storage.md).

## Run options and outputs

Exactly one run input is required. Positional input and `--prompt` are prompts;
`--draft` is inline, and `--draft-file` reads strict UTF-8 relative to the
caller's directory. Resolution precedes backend construction.

`--backend` is repeatable, accepts canonical agent IDs and valid backend names,
and lasts one invocation; duplicates fail. `--codex-timeout` is positive and
defaults to 300 seconds. `--max-debate-rounds` is nonnegative.

Omitted debate IDs are generated. Omitted output becomes
`<session_id>_<debate_id>.md` under `manuscript/`. Run prints complete
`DebateResult.to_dict()` JSON. Initialization, directives, inspection, and
legacy lifecycle print indented JSON; legacy failures print
`ExceptionType: message` to standard error, unlike the safe interactive
boundary.

Smoke uses no provider. Pass JSON includes `status: passed` and
`provider_tested: false`; invariant failure includes `failed_check`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | A command succeeded, the lobby ended normally, run reached consensus, or smoke passed. |
| `1` | Smoke completed but an invariant failed. |
| `2` | Usage, missing TTY, setup, fatal lobby/cleanup, or another caught failure. |
| `3` | Run returned `deadlock`. |
| `4` | Run returned `failed`. |

See [Identifiers and errors](configuration/identifiers-and-errors.md) and
[Consensus runs](../guides/consensus-runs.md) for noninteractive details.
