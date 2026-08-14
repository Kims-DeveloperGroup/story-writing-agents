# CLI reference

> **Availability:** this is the complete installed CLI. Bare `story`, chat
> dispatch, conversation selection, transcript, search, and slash commands are
> not available as runtime commands.

Key terms: **command alias**—another executable name for the same parser;
**workspace discovery**—the upward search for an initialized root; **session
selection**—the saved default session ID; **exit code**—the process outcome number.

The package installs `story` and `story-writing-agents` with the identical
entry point `story_writing_agents.cli:main`. Replace one with the other exactly.

## Command syntax

| Command | Exact syntax |
| --- | --- |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS] [PROMPT \| --prompt TEXT \| --draft TEXT \| --draft-file PATH] [--output PATH] [--debate-id ID] [--max-debate-rounds N]` |
| Inspect | `story inspect [--workspace PATH] [--session ID] [--debate ID]` |
| Start/end session | `story session (start \| end) [ID] [--workspace PATH] [--session ID]` |
| Smoke | `story smoke [--workspace PATH]` |

`argparse` supplies `-h`/`--help` for the root and subcommands. `init` defaults
to the current directory. It creates missing scaffold and preserves existing
files; `--overwrite-prompts` replaces only the four canonical
`config/agents/<agent-id>/AGENTS.md` files.

## Workspace and session resolution

For every command except `init` and `smoke`, an explicit `--workspace` must
already be initialized. Smoke instead initializes missing scaffold in its
explicit diagnostic directory. Without an explicit path, normal commands
search upward from the current directory and choose the nearest fully
initialized ancestor. They never silently initialize during resolution.

Session precedence is `--session`, then the positional ID accepted by
`session start|end`, then `selected_session` in CLI context. Supplying both
lifecycle forms is an error. Only a successful `session start` atomically
changes selection. Failed start, end, run, directive, and inspect preserve it.

## Run options

Exactly one input is required. A positional value and `--prompt` are prompts;
`--draft` is a complete inline candidate; `--draft-file` reads a complete
strict-UTF-8 candidate relative to the caller's current directory.
Input selection and draft-file decoding finish before provider construction.

`--backend` is repeatable and requires a canonical agent ID, one `=`, and a
valid backend value. Duplicate agent assignments are rejected. The assignment
lasts one run and does not rewrite configuration. `--codex-model` overrides
the Codex default for that run. `--codex-timeout` is a positive per-call
timeout and defaults to `300` seconds. `--max-debate-rounds` is a nonnegative
replacement count.

Omitted debate IDs are generated. Omitted outputs become
`<session_id>_<debate_id>.md` beneath `manuscript/`. Run prints complete
`DebateResult.to_dict()` JSON, including blackboard messages.

## Outputs

Initialization, directives, inspection, and session lifecycle print indented
JSON. Start/end output contains `session_id` and `active`. Caught exceptions
print `ExceptionType: message` to standard error.

Smoke without a workspace creates and removes a temporary workspace. With a
path, it initializes or reuses the directory and retains unique diagnostics.
It never constructs `AgentBackend` or calls a provider. Passed compact JSON
contains `command: smoke`, `status: passed`, `provider_tested: false`, workspace
mode/path/retention, IDs, `debate_status: consensus`, `revision_id: r1`,
`rounds_completed: 1`, six check names, and a disclaimer. Failed invariant
JSON includes `failed_check`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Init, directive, inspection, or lifecycle succeeded; run reached consensus; or smoke passed. |
| `1` | Smoke completed but an invariant failed. |
| `2` | CLI usage or a caught exception, including setup and preflight failure. |
| `3` | Run returned `deadlock`. |
| `4` | Run returned `failed`. |

See [Identifiers and errors](configuration/identifiers-and-errors.md) for
validation and [Consensus runs](../guides/consensus-runs.md) for task guidance.
