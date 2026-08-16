# CLI reference

> **Availability:** bare `story` and `story chat` open the TTY shell for
> sessions, conversations, management, advisory turns, local reads, and
> selected-context consensus.

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

<a id="interactive-streams"></a>

`argparse` handles help and syntax first. Bare entry takes no options. Both
interactive streams must be TTYs or the command exits `2` before workspace or
backend access.

## Interactive management and gates

Global commands are registry-derived `/help [COMMAND]` (alias `/?`) and
`/status`. Lobby status has normalized workspace, saved session, no attachment,
unavailable conversation/context/search, and no last run. Attached status shows
saved/attached sessions; refreshed selected ID/subject/status/revision or none;
target; total events; retained/total/omitted context turns, bytes, truncation,
and 24-turn/32,768-byte caps; active-only search; and latest current-session
run ID/status/input/revision/rounds/context conversation/mode or none.

Target/conversation/search/transcript/directive/inspection require attachment;
messages/consensus/history also require active selection. Transcript includes
archives; search is active-only.

Attached `/language ARG`, `/persona AGENT ARG`, and `/reset` apply
`[SYS: LANG=ARG]`, `[OVERRIDE: CANONICAL_ID Persona=ARG]`, and `[RESET: ALL]`
through `StoryRuntime.apply_directive`; no conversation is required. Agents
normalize canonically. Only validated atomic success mutates overrides. Unknown
agents are `agent_invalid`; configuration rejection is `configuration_invalid`.

`/history [COUNT]` reads one selected snapshot: omission means all; positive
ASCII count means newest `min(count,total)`, chronologically. Every kind,
unmatched user, and whitespace stays; empty is normal. Summary: displayed/total
events plus canonical context turns/bytes/truncation/caps. No pager, search,
provider, or write.

`/inspect` summarizes attachment; `/inspect ID` reads that current-session
debate (`not_found` if missing); `/inspect last` uses this process's per-session
latest accepted `started`, never storage. None is normal. It whitelists
lifecycle/artifact/context identity/mode/count metadata, never messages,
candidates, errors, actions, content, or provider fields. Noninteractive JSON
is unchanged.

Management preserves attachment, selection, target, transcript, catalog,
artifacts, and provider state except successful directives. See the
[grammar](interactive-command-parser.md), [transcripts](conversation-transcripts.md),
and [inspection guide](../guides/inspection-and-recovery.md).

<a id="interactive-consensus"></a>

## Interactive consensus

| Syntax | Behavior |
| --- | --- |
| `/run PROMPT` or bare `/run` then `/send` | Prompt with one selected bounded snapshot. |
| `/run --fresh PROMPT` | Records selected identity; sends no transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft with one selected snapshot. |

Attachment/selection are revalidated before input access. Runs may write
debates/artifacts, never transcripts/shell scope. Draft paths use caller CWD,
expand `~`, follow symlinks, and invoke no shell; invalid/empty is
`draft_input_invalid`.

Fixed codes are `consensus_deadlock`, `provider_preflight_failed`,
`provider_protocol_error`, `transcript_invalid`, `workspace_invalid`,
`artifact_conflict`, or `consensus_failed`. Unexpected contracts are fatal
`internal_error`; output omits exception/model/result prose.
Standalone `/compose` and artifact commands remain unavailable; `/send` and
`/cancel` control only bare `/run` composition.

## Interactive cancellation

One controller-owned `asyncio.Runner`/loop serves advisory and consensus work.
Main Ctrl-C takes no action; composer Ctrl-C or `/cancel` discards its buffer.
Either EOF requests normal cleanup.

Advisory Ctrl-C is recoverable only after `CancelledError` reached and joined
the operation. A durable user event normally gets fixed `runtime_error`; failed
storage leaves it unmatched. Scope remains; taint forces a fresh provider thread.

Pre-entry consensus cancellation is provider-free and creates no debate. After
entry, only trusted terminal `INTERRUPTED` proves recovery; it cannot resume. Missing
proof is fatal `operation_interrupted` and abandons attachment. Cancelling
`/search`, `/transcript`, or `/history` keeps completed output but emits no
final result/hint, provider call, or write.
Exact records and failure mapping are in the [terminal reference](interactive-terminal.md)
and [recovery guide](../operations/recovery-and-limitations.md).

## Workspace, noninteractive behavior, and exits

Except `init`/`smoke`, commands need the nearest workspace. Session precedence:
`--session`, lifecycle positional ID, saved selection. Noninteractive `run` has
no conversation context; its JSON/stderr and `inspect` JSON stay unchanged.

Exit `0` means success or normal shell end, `1` a failed smoke invariant,
`2` usage/setup/fatal shell failure, `3` noninteractive deadlock, and `4`
noninteractive failure. See [Consensus runs](../guides/consensus-runs.md) and
[Terminal safety](../operations/terminal-safety.md).
