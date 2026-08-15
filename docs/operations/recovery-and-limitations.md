# Recovery and limitations

> **Availability:** debate inspection, interactive consensus, conversation
> management, advisory turns, search, and bounded transcript reads are
> implemented. Automatic retry, stale-owner cleanup, and event deletion are not.

Key terms: **terminal**—an attempt that cannot resume; **lease proof**—the same
session is active and `OWNED`; **observer failure**—progress delivery failed;
**unmatched turn**—a user event without a stored outcome.

## Debate and interactive-consensus recovery

A session/debate ID is reserved before provider work and cannot resume or be
reused. `active` does not prove liveness because there is no heartbeat. Inspect
the attempt, repair its cause, and start with a new debate ID. Deadlock needs a
new instruction or draft; `failed` and `interrupted` also need new IDs.

The shell returns to the same prompt after consensus, deadlock, and handled
failure. It reports fixed codes and trusted metadata, never candidate, critique,
model, result-error, action-required, or raw exception prose. Exact input,
context, provider, workspace, and artifact mappings belong to the
[CLI reference](../reference/cli.md#interactive-consensus).

Before returning after any consensus workspace error, the controller must prove
the same session is active and its lease is still `OWNED`. Failed proof clears
uncertain process state and exits through fatal cleanup instead of claiming safe
recovery.

The separate context sidecar shows whether a run was included or fresh and
preserves the exact included snapshot. Its absence means a legacy run. It is
inspection evidence, not a replay or resume mechanism.

## Observer failure and cancellation

A preterminal synchronous progress-sink failure raises fixed-message
`ProgressObserverError`, with the original only as `__cause__`, and best-effort
marks the debate `interrupted`; normally there is no terminal result. A sink
failure after result/state durability still raises, but never rewrites that
terminal state or committed artifacts. Inspect before retrying with a new ID.

External Python coroutine cancellation remains `asyncio.CancelledError`. The
runtime joins current work, best-effort marks `interrupted`, and emits trusted
interruption progress only after confirming that state. If its observer fails,
cancellation stays primary and the observer error is the cause. This is the
Python API contract, not a promise about active-run TTY signal handling.

## Session, selection, and advisory recovery

A story-session lease is ownership evidence, not remote-health proof. Another
or uncertain owner blocks attachment regardless of age; never delete
coordination files. End keeps provider threads, selections, state, artifacts,
and transcripts.

Restart restores only an existing active saved conversation. Missing or
archived records open unselected without rewriting the stale saved ID. Create or
use save failure preserves the prior selection, though a newly created record
can remain. Selected archive crosses metadata and selection files and uses the
[bounded compensation](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).

<a id="conversation-turn-recovery"></a>

Failure before an advisory user append leaves no transcript event. Later
ordinary failure normally stores a safe runtime error. Hard interruption or an
outcome-write failure can leave an unmatched user event; full reads expose it,
later context excludes it, and no automatic repair exists. Preserve the record
and inspect the [pairing rules](../reference/conversation-transcripts.md).

## Search and transcript-read failures

Search and read errors use fixed parser, conversation, transcript, workspace, or
cleanup categories. After a workspace error, the controller likewise proves the
active owned lease before returning. Ctrl-C during these local reads preserves
scope and durable data, but already displayed rows remain in scrollback.

Every transcript command starts a fresh read; its continuation hint is not a
saved cursor. A Python cursor becomes stale after append. Search can truncate,
so do not treat a truncated result as exhaustive. See
[Conversation search](../reference/conversation-search.md).

## Current product limits

- Codex is the only provider; reserved `gemini` fails preflight.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact rollback is best effort across files, not a database transaction.
- Advisory turns are single-line and have no active-turn cancellation.
- `/search` is active-only; there is no RAG, semantic retrieval, or saved index.
- `/transcript` retains no cursor; there is no event repair or deletion.
- General `/compose`, `/history`, and standalone artifact shell commands are
  unavailable.

For provider failure, check executable, login/key, model, timeout, session, and
lease. Persisted format errors fail closed; never skip damaged records. See
[Inspection and recovery](../guides/inspection-and-recovery.md) and
[Filesystem safety](filesystem-safety.md).
