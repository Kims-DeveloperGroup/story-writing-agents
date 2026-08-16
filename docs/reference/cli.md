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

`/help [COMMAND]` (alias `/?`) and `/status` are global; help comes from the
registry. Lobby status shows normalized workspace, saved session,
no attachment, unavailable conversation/context/search, and no last run.
Attached status distinguishes saved and attached sessions; refreshes selected
ID, subject, status, and revision or reports none; and reports sticky target;
total events; canonical retained/total/omitted context turns, bytes, truncation,
and 24-turn/32,768-byte caps; active-only search; and latest current-session run
ID/status/input/revision/rounds/context
conversation and mode or none.

Target, conversation, search, transcript, directive, and inspection commands
require attachment. Messages, consensus, and `/history` require an active
selection. `/transcript` may read active or archived data; `/search` is
active-only.

`/language ARG`, `/persona AGENT ARG`, and `/reset` pass respectively
`[SYS: LANG=ARG]`, `[OVERRIDE: CANONICAL_ID Persona=ARG]`, and
`[RESET: ALL]` to `StoryRuntime.apply_directive`. Agent aliases normalize to
canonical IDs. Validated candidate overrides replace atomically; only success
mutates them. Unknown persona agents report `agent_invalid`; configuration
rejection reports `configuration_invalid`. No conversation is required.

`/history [COUNT]` reads one fixed selected snapshot. Omitted count
shows all; supplied positive ASCII count shows the newest
`min(count,total)` events chronologically. User, reply, runtime-error,
unmatched, and whitespace events remain visible. Empty is normal. Its summary
reports displayed/total events and canonical retained/total/omitted turns,
bytes, truncation, and caps. It has no pager, search, provider, or write.

`/inspect` summarizes the attached session; `/inspect ID` reads the
exact current-session debate and reports missing as `not_found`.
`/inspect last` uses the process-local per-session latest accepted started
run—success, deadlock, failure, or interruption—and reports no run normally. It
does not persist or guess `last`. Output allows lifecycle/artifact metadata and
context identity/mode/counts, never raw messages, candidates, errors, actions,
context content, or provider fields. Noninteractive inspection JSON is unchanged.

Management preserves attachment, selection, sticky target, transcript, catalog,
artifact, and provider state; only successful directives change overrides.
See the [grammar](interactive-command-parser.md), [transcript contract](conversation-transcripts.md),
and [inspection guide](../guides/inspection-and-recovery.md).

<a id="interactive-consensus"></a>

## Interactive consensus

| Syntax | Behavior |
| --- | --- |
| `/run PROMPT` or bare `/run` then `/send` | Prompt with one selected bounded snapshot. |
| `/run --fresh PROMPT` | Records selected identity; sends no transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft with one selected snapshot. |

Attachment and selection are revalidated before input access. Runs may write
debates/artifacts, but not transcripts or shell scope. Draft paths use caller
CWD, expand `~`, follow `pathlib` symlinks, and never invoke a
shell. Invalid or empty text is `draft_input_invalid`.

Deadlock is `consensus_deadlock`; failures map `preflight` to
`provider_preflight_failed`, `protocol` to `provider_protocol_error`, `context`
to `transcript_invalid`, `workspace` to `workspace_invalid`, artifact conflict
to `artifact_conflict`, and artifact/internal failure to `consensus_failed`.
Unexpected contracts are fatal `internal_error`. Output excludes raw exceptions
and model/result prose.
Standalone `/compose` and artifact commands remain unavailable; `/send` and
`/cancel` still control only bare `/run` composition. Active-turn/run
cancellation remains separate Issue #14.

## Workspace, noninteractive behavior, and exits

Except `init` and `smoke`, commands need the nearest initialized workspace.
Session precedence is `--session`, lifecycle positional ID, then saved
selection. Noninteractive `run` uses no conversation context and retains its
JSON/stderr contract; `inspect` JSON is also unchanged.

Exit `0` means success or normal shell end, `1` a failed smoke invariant,
`2` usage/setup/fatal shell failure, `3` noninteractive deadlock, and `4`
noninteractive failure. See [Consensus runs](../guides/consensus-runs.md) and
[Terminal safety](../operations/terminal-safety.md).
