# CLI reference

> **Availability:** bare `story` and `story chat` open the TTY shell for session
> and conversation management, advisory/local reads, and selected-context consensus.

`story` and `story-writing-agents` are aliases for
`story_writing_agents.cli:main`.

## Top-level syntax

| Command | Exact syntax |
| --- | --- |
| Interactive | `story` or `story chat [SESSION] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS] [PROMPT \| --prompt TEXT \| --draft TEXT \| --draft-file PATH] [--output PATH] [--debate-id ID] [--max-debate-rounds N]` |
| Inspect | `story inspect [--workspace PATH] [--session ID] [--debate ID]` |
| Session | `story session (start \| end) [ID] [--workspace PATH] [--session ID]` |
| Smoke | `story smoke [--workspace PATH]` |

<a id="smoke-report"></a>

## Smoke report

`story smoke` writes one-line compact key-sorted JSON. Success keys are exactly
`checks`, `command`, `debate_id`, `debate_status`,
`note`, `provider_tested`, `revision_id`, `rounds_completed`, `session_id`,
`status`, and `workspace`; a named invariant failure adds only `failed_check`.
`workspace` contains only `mode`, `path`, and `retained`.

On a pass, `provider_tested:false` and `checks` is exactly, in order:
`workspace_initialized`,
`directive_persisted`, `conversation_catalog_persisted`, `conversation_routing`,
`conversation_search`, `conversation_resumed`, `conversation_retrieval`,
`consensus_context`, `revision_workflow`, `consensus_persisted`,
`manuscript_committed`, `core_documents_unchanged`. No top-level conversation ID
or provider detail is added.

Without `--workspace`, normal success or invariant failure removes the temporary
workspace; an explicit workspace retains unique plaintext diagnostics and is
not read-only. Exit `0` passes, `1` returns failed JSON with the completed check
prefix and `failed_check`, and `2` covers usage, setup, or unexpected failure.
Pre-debate failure keeps `debate_status:"not_run"`, null revision, and zero
rounds. See the [smoke guide](../guides/smoke-test.md).

<a id="interactive-streams"></a>

`argparse` owns help and syntax. Bare entry takes no options. Both streams must
be TTYs before workspace or backend access; failure exits `2`.

## Interactive management and gates

`/help [COMMAND]` (alias `/?`) and `/status` are global. Lobby status reports
workspace/saved session; attached status adds selected conversation, target,
context/search accounting, and latest current-session run.

Target/conversation/search/transcript/directive/inspection require attachment;
messages/consensus/history also require active selection. Transcript includes
archives; search is active-only.

Attached `/language ARG`, `/persona AGENT ARG`, and `/reset` use runtime
directives without selection. Agent IDs canonicalize; only valid atomic success
mutates. Errors are `agent_invalid` or `configuration_invalid`.

`/history [COUNT]` reads one selected snapshot: omitted means all; positive ASCII
means newest events chronologically. It preserves kinds, unmatched user events,
and whitespace; empty is normal. Summary includes event/context accounting. No
pager, provider, or write.

`/inspect` summarizes attachment; `/inspect ID` reads a current-session debate;
`/inspect last` uses this process's latest accepted `started`. Output whitelists
lifecycle/artifact/context metadata, never content/provider fields. Missing is
normal or `not_found` as documented.

Management preserves scope, transcripts, catalog, artifacts, and provider state
except successful directives. See [grammar](interactive-command-parser.md),
[transcripts](conversation-transcripts.md), and [inspection](../guides/inspection-and-recovery.md).

<a id="interactive-consensus"></a>

## Interactive consensus

| Syntax | Behavior |
| --- | --- |
| `/run PROMPT` or bare `/run` then `/send` | Prompt with one selected bounded snapshot. |
| `/run --fresh PROMPT` | Records selected identity; sends no transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft with one selected snapshot. |

Runs revalidate scope and may write debate artifacts, never transcripts. Draft
paths use caller CWD, expand `~`, follow symlinks, and invoke no shell;
invalid/empty is `draft_input_invalid`.

Fixed codes are `consensus_deadlock`, `provider_preflight_failed`,
`provider_protocol_error`, `transcript_invalid`, `workspace_invalid`,
`artifact_conflict`, and `consensus_failed`; unexpected contracts are fatal
`internal_error`. Output omits exception/model/result prose. `/send` and
`/cancel` only compose bare `/run`; artifact commands remain unavailable.

## Interactive cancellation

One controller loop serves advisory and consensus. Main Ctrl-C does nothing;
composer Ctrl-C or `/cancel` discards its buffer; either EOF cleans up.

Pre-entry first SIGINT recovers only from a quiescent Runner, without runtime,
provider, or durable mutation. Entered recovery requires `CancelledError` and
join; consensus also requires terminal `INTERRUPTED` proof.

Missing proof, nonquiescence, or join failure is fatal. Cancelled local reads
keep completed output without final result or write. See the
[terminal reference](interactive-terminal.md) and [recovery guide](../operations/recovery-and-limitations.md).

## Workspace, noninteractive behavior, and exits

Except `init`/`smoke`, commands need the nearest workspace. Session precedence:
`--session`, lifecycle positional ID, saved selection. Noninteractive `run` has
no conversation context; its JSON/stderr and `inspect` JSON remain unchanged.

Other exits are `0` success/shell end, `2` usage/setup/fatal shell, `3` deadlock,
and `4` failure. See [Consensus runs](../guides/consensus-runs.md) and
[Terminal safety](../operations/terminal-safety.md).
