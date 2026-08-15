# CLI reference

> **Availability:** bare `story` and `story chat` open the installed TTY lobby.
> It manages story sessions and, once one is attached, subject conversations.
> It does not send chat turns or expose history, search, or transcripts.

Key terms: **bare entry**—`story` with no subcommand; **TTY**—an interactive
terminal stream; **session selection**—the saved default session ID; **exit
code**—the process outcome number.

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

`argparse` supplies `-h`/`--help` for the root and subcommands. Help and syntax
errors finish before interactive setup and therefore do not require a TTY.
Bare entry accepts no options; use `story chat` when passing a session or
interactive configuration.

## Interactive streams

After argument parsing, bare and `chat` entry require both standard input and
standard output to report that they are TTYs. This check runs before input,
interactive option validation, workspace or CLI-context reads, and backend
construction. A missing or failing TTY check writes `tty_required` to standard
error and exits `2` without touching the workspace.

Prompts, orientation, status, and command-loop errors use standard output.
Startup and adapter errors use standard error. Interactive errors are typed and
sanitized; see [Terminal display safety](../operations/terminal-safety.md).

The unattached lobby supports help, story-session navigation, and quit.
Conversation creation, listing, selection, rename, and archive become available
only after attachment; these operations call no provider and write no
transcript or story artifact. Other parsed interaction remains unavailable.
See the [story-session scenario](../guides/story-sessions.md) and the canonical
[interactive grammar](interactive-command-parser.md).

## Workspace and session resolution

Except for `init` and `smoke`, an explicit workspace must already be
initialized. Without one, commands search upward and choose the nearest fully
initialized ancestor; they never initialize during resolution. Bare entry
always uses discovery, while `chat` can take `--workspace`.

Session precedence is `--session`, the `session start|end` positional ID, then
saved selection; both explicit forms are an error. Only successful start or an
acquired lobby destination reselects. Failure and end preserve selection.

Each story session has an independent, lazy conversation selection. It is not
stored in `cli_context.json`; see [Session storage](configuration/session-storage.md).

## Run options

Exactly one input is required. Positional input and `--prompt` are prompts;
`--draft` is an inline candidate; `--draft-file` reads strict UTF-8 relative to
the caller's directory. Resolution precedes provider construction.

`--backend` is repeatable, requires a canonical agent ID and valid backend, and
lasts one invocation. Duplicate agents are rejected. `--codex-model` selects
the Codex model for that invocation. `--codex-timeout` is positive and defaults
to 300 seconds. `--max-debate-rounds` is a nonnegative replacement count.

Omitted debate IDs are generated. Omitted output becomes
`<session_id>_<debate_id>.md` under `manuscript/`. Run prints complete
`DebateResult.to_dict()` JSON, including blackboard messages.

## Other outputs

Initialization, directives, inspection, and legacy lifecycle print indented
JSON. Legacy failures print `ExceptionType: message` to standard error;
interactive errors use the safe boundary above.

Smoke uses a temporary workspace unless given a retained path. It calls no
provider. Pass JSON includes `status: passed` and `provider_tested: false`;
invariant failure includes `failed_check`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | A normal command succeeded, the lobby ended normally, run reached consensus, or smoke passed. |
| `1` | Smoke completed but an invariant failed. |
| `2` | Usage, missing TTY, setup, typed fatal lobby or cleanup, or another caught failure. |
| `3` | Run returned `deadlock`. |
| `4` | Run returned `failed`. |

See [Identifiers and errors](configuration/identifiers-and-errors.md) for
validation and [Consensus runs](../guides/consensus-runs.md) for run guidance.
