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

`story smoke` writes compact key-sorted JSON. Success keys are exactly
`checks`, `command`, `debate_id`, `debate_status`,
`note`, `provider_tested`, `revision_id`, `rounds_completed`, `session_id`,
`status`, and `workspace`; a named invariant failure adds only `failed_check`.
`workspace` contains only `mode`, `path`, and `retained`.

On a pass, `provider_tested:false`; `checks` is exactly, in order:
`workspace_initialized`,
`directive_persisted`, `conversation_catalog_persisted`, `conversation_routing`,
`conversation_search`, `conversation_resumed`, `conversation_retrieval`,
`consensus_context`, `revision_workflow`, `consensus_persisted`,
`manuscript_committed`, `core_documents_unchanged`. No top-level conversation ID or
provider detail appears.

Implicit workspaces are removed after success or invariant failure; explicit
ones retain mutable plaintext diagnostics. Exit `0` passes, `1` includes the
completed check prefix and `failed_check`, and `2` covers usage/setup/unexpected
failure. Pre-debate failure reports `debate_status:"not_run"`, null revision, and zero rounds.
See the [smoke guide](../guides/smoke-test.md).

<a id="interactive-streams"></a>

`argparse` owns syntax; bare entry takes no options. Both streams must be TTYs
before workspace/backend access or exit `2`. The guarded CLI adds main-prompt
live editing only on capable POSIX with nonempty, non-`dumb` `TERM`. Unsupported
or safely restored pre-input failure uses `input()` without ANSI. Other reads
are unchanged; the editor neither parses nor dispatches. See the
[exact contract](interactive-terminal.md#main-prompt-live-suggestions).

## Interactive management and gates

`/help [COMMAND]` (`/?`) and `/status` are global. Status reports lobby or
attached scope, target, context/search, and latest-run facts.

Target, conversation, local-read, directive, and inspection actions require
attachment; messages, consensus, and history also require active selection.
Transcript includes archives; search is active-only.

Attached `/language ARG`, `/persona AGENT ARG`, and `/reset` use runtime
directives without selection. Agent IDs canonicalize; only atomic success
mutates. Errors are `agent_invalid` or `configuration_invalid`.

`/history [COUNT]` reads one selected snapshot: omitted means all; positive ASCII
means newest events chronologically. It preserves kinds, unmatched users, and
whitespace; its summary accounts for events/context. No pager, provider, or
write exists.

`/inspect` summarizes attachment; `ID` reads a current-session debate and `last`
uses this process's latest accepted `started`. Output whitelists metadata, never
content/provider fields. Missing is normal or `not_found`.

See [grammar](interactive-command-parser.md),
[transcripts](conversation-transcripts.md), and [inspection](../guides/inspection-and-recovery.md).

<a id="interactive-consensus"></a>

## Interactive consensus

| Syntax | Behavior |
| --- | --- |
| `/run PROMPT` or bare `/run` then `/send` | Prompt with one selected bounded snapshot. |
| `/run --fresh PROMPT` | Records selected identity; sends no transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft with one selected snapshot. |

Runs revalidate scope and may write artifacts, never transcripts. Draft paths
use caller CWD, expand `~`, follow symlinks, and invoke no shell; invalid/empty
is `draft_input_invalid`.

Fixed codes are `consensus_deadlock`, `provider_preflight_failed`,
`provider_protocol_error`, `transcript_invalid`, `workspace_invalid`,
`artifact_conflict`, and `consensus_failed`; unexpected contracts are fatal
`internal_error`. Output omits exception/model/result prose; `/send` and
`/cancel` only compose bare `/run`; artifact commands remain unavailable.

## Interactive cancellation

Main Ctrl-C takes no action; composer Ctrl-C or `/cancel` discards its buffer;
EOF cleans up.

Pre-entry first SIGINT recovers only from a quiescent Runner, without runtime,
provider, or durable mutation. Entered recovery requires `CancelledError` and
join; consensus also requires terminal `INTERRUPTED` proof.

Missing proof, nonquiescence, or join failure is fatal. Cancelled local reads
keep completed output without result/write. See the
[terminal reference](interactive-terminal.md) and [recovery guide](../operations/recovery-and-limitations.md).

## Workspace, noninteractive behavior, and exits

Except `init`/`smoke`, commands need the nearest workspace. Session precedence
is `--session`, lifecycle positional ID, then saved selection. Noninteractive
`run` has no conversation context; its JSON/stderr and `inspect` JSON are
unchanged.

Exits: `0` success/shell end; `1` failed smoke invariant; `2` usage/setup/fatal
shell; `3` noninteractive deadlock; `4` noninteractive failure. See [Consensus runs](../guides/consensus-runs.md) and [Terminal safety](../operations/terminal-safety.md).
