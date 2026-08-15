# CLI reference

> **Availability:** bare `story` and `story chat` open the installed TTY lobby.
> It manages sessions and conversations and sends routed single-line advisory
> turns. History, transcript viewing, search, consensus, and artifacts are not exposed.

Key terms: **bare entry**—`story` alone; **TTY**—interactive terminal;
**session selection**—saved default; **exit code**—process outcome.

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

Bare and `chat` entry require TTY standard input and output. This check precedes
input, option validation, workspace/context reads, and backend construction.
Failure writes `tty_required` to standard error and exits `2` without workspace
access.

Interactive prompts, status, and errors use standard output; startup and adapter
errors use standard error. All are typed and sanitized; see
[Terminal safety](../operations/terminal-safety.md).

`/agents`, help, session navigation, and quit are global and provider-free.
`/to NAME` and `/agent NAME` need attachment, not conversation selection, and
change only the process-local sticky target. Conversation management also needs
attachment and remains provider-free.

An ordinary line, `@AGENT TEXT`, or `/say TEXT` also needs a selected active
conversation. Ordinary and `/say` use the sticky target; `@AGENT` is one-shot.
Each line waits for one `StoryRuntime.converse()` call with pre-turn context;
the reply is displayed, never reparsed. Routing selects a responder in a shared
room, not a private channel. Conversation changes keep the target; session
attach, switch, and end reset it to lead.

The loop is synchronous per turn. `/compose`, active-turn cancellation, a persistent
event loop, history, search, transcript, debate, and artifact actions are
unavailable. See the [story-session scenario](../guides/story-sessions.md),
[interactive grammar](interactive-command-parser.md),
and [turn contract](runtime-api.md#one-conversation-turn).

## Workspace and session resolution

Except for `init` and `smoke`, the workspace must be initialized. Without an
explicit path, commands choose the nearest initialized ancestor. Bare entry
always discovers; `chat` accepts `--workspace`.

Session precedence is `--session`, the `session start|end` positional ID, then
saved selection; both explicit forms conflict. Successful start or shell
acquisition reselects; failure and end preserve selection.

Each story session has an independent, lazy conversation selection. It is not
stored in `cli_context.json`; see [Session storage](configuration/session-storage.md).

## Run options

Exactly one input is required. Positional input and `--prompt` are prompts;
`--draft` is inline, and `--draft-file` reads strict UTF-8 relative to the
caller's directory. Resolution precedes backend construction.

`--backend` is repeatable, accepts canonical agent IDs and valid backend names,
and lasts one invocation; duplicates fail. `--codex-model` selects the model.
`--codex-timeout` is positive and defaults to 300 seconds;
`--max-debate-rounds` is nonnegative.

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
