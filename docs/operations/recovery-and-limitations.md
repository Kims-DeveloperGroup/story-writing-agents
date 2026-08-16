# Recovery and limitations

> **Availability:** debate inspection, interactive consensus, conversation
> management, safe status/history/inspection, directives, retrieval-enabled
> turns, and local search/read are implemented. Automatic retry, stale-owner
> cleanup, and event deletion are not.

Key terms: **terminal**—an attempt that cannot resume; **lease proof**—the same
session is active and `OWNED`; **taint**—discarding incomplete provider history;
**unmatched turn**—a user event without an outcome.

## Debate and interactive-consensus recovery

A debate ID is reserved before provider work and cannot resume or be reused.
`active` is not liveness. Inspect, repair, and use a new ID; deadlock also needs
a revised instruction or draft.

Consensus, deadlock, and handled failure return to the prompt with fixed codes,
never candidate, critique, model, result-error, action-required, or exception
prose. Mappings belong to the [CLI reference](../reference/cli.md#interactive-consensus).

After a consensus workspace error, safe return requires the same active `OWNED`
session; failed proof clears uncertain state and exits through fatal cleanup.

The context sidecar distinguishes included, fresh, and legacy runs. It is
inspection evidence, never replay or resume state.

## Observer failure and cancellation

Debate sink failure raises fixed `ProgressObserverError`; before terminal state
it best-effort marks `interrupted`, while later failure never rewrites durable
results/artifacts. Python cancellation remains `asyncio.CancelledError`, joins
current work, and best-effort records interruption. Observer failure is only
its cause. Active-run TTY signal handling is not promised.

## Session, selection, and advisory recovery

A lease proves ownership, not remote health. Another or uncertain owner blocks
attachment regardless of age; never delete coordination files. End retains
threads, selections, state, artifacts, and transcripts.

Restart restores only an active saved conversation. Missing/archived records
open unselected without rewriting the stale ID. Create/use save failure keeps
the prior selection, though a new record can remain. Selected archive uses
[bounded compensation](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).

<a id="conversation-turn-recovery"></a>

Failure before user append leaves no event. Later failure normally stores one
safe runtime error. Hard interruption or outcome-write failure can leave an
unmatched user event; reads expose it, context excludes it, and no repair exists.

Invalid agent actions show `retrieval_invalid`: start a new turn and check
provider compatibility if repeated. Exhaustion shows
`retrieval_budget_exhausted`: ask a narrower question or use direct `/search`
and `/transcript`. Retrieval observer failure is fatal `internal_error`.

After any nonterminal action, failure or cancellation taints the incomplete
provider thread; the next compatible call starts fresh. A valid answer completes
provider history, so later reply-persistence failure does not taint it.
Cancellation stores the fixed transcript error and re-raises
`asyncio.CancelledError`. See [Bounded retrieval](../reference/conversation-retrieval.md).

## Search and transcript-read failures

Local search/read errors use fixed parser, conversation, transcript, workspace,
or cleanup categories. Workspace recovery requires the active owned lease.
Ctrl-C preserves scope/data; displayed rows remain in scrollback.

Each transcript command reads fresh; its hint is not a saved cursor. Python
cursors stale after append, and truncated search is not exhaustive. See
[Conversation search](../reference/conversation-search.md).

## Management failures and restart behavior

`/history` uses one authoritative snapshot; a concurrent later append appears
only on the next command. Empty history is normal. Transcript/catalog corruption
fails closed; no management read repairs it.

An explicit missing `/inspect ID` reports `not_found`. `/inspect last` with no
accepted run is normal, not an error. Its per-session mapping is
process-local: switching preserves it, restart resets it, and persisted debate
IDs are never guessed. Malformed inspection data is fatal `internal_error`
without echoing raw fields.

Unknown `/persona` agents are `agent_invalid`. Directive/configuration rejection
is `configuration_invalid`; validation occurs before atomic override
replacement, so failure preserves prior bytes. Successful management otherwise
preserves all other shell/story/provider state.

## Current product limits

- Codex is the only provider; reserved `gemini` fails preflight.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact rollback is best effort across files, not a database transaction.
- Advisory turns are single-line and have no active-turn cancellation.
- `/search` is active-only; there is no RAG, semantic retrieval, or saved index.
- `/transcript` retains no cursor; there is no event repair or deletion.
- Standalone `/compose` and artifact shell commands are unavailable; `/send`
  and `/cancel` remain scoped to bare `/run` composition. Active cancellation
  remains separate Issue #14.

For provider failure, check executable, login/key, model, timeout, session, and
lease. Persisted format errors fail closed; never skip damaged records. See
[Inspection and recovery](../guides/inspection-and-recovery.md) and
[Filesystem safety](filesystem-safety.md).
