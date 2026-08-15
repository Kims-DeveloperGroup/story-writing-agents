# Recovery and limitations

> **Availability:** inspection, new-ID debate recovery, and one explicitly
> addressed conversation turn are implemented. Automatic retry, stale-owner
> cleanup, event deletion, and interactive conversation commands are not.

Key terms: **immutable reservation**—an ID that cannot be reused; **deadlock**—
unresolved specialist conflict at the round limit; **liveness**—evidence about
whether an owner still runs; **retention**—how long persisted data remains;
**unmatched turn**—a stored user event without an outcome.

## Debate recovery

A session/debate pair is reserved before provider work. Its owner token
coordinates transitions but is not authentication. `active` proves reservation,
not liveness; there is no heartbeat, stale-owner cleanup, or same-ID resume.

External coroutine cancellation propagates to provider calls and is
best-effort recorded as terminal `interrupted`. Consensus, deadlock, failure,
interruption, and abandoned active work all consume the ID. Inspect preserved
state, make any user ruling, repair the cause, and start a new debate ID.

A deadlock reports `action_required` but has no continuation API or manuscript.
Failures may require filesystem inspection because multi-file commit is not a
transaction. Never delete lifecycle evidence to reuse an ID.

## Session and lease recovery

Lease status is evidence, not proof of ownership or remote health. Another or
uncertain owner blocks attachment; do not infer staleness from age or delete
coordination files. Exact classification and reclamation rules live only in
[Backend and sessions](../reference/backend-and-sessions.md).

The persistent claim guard coordinates only cooperating local processes. It is
not authentication, a distributed lock, or a network-filesystem guarantee. Do
not replace its pathname while the workspace may be active.

Ending a session makes it inactive but retains provider thread references,
external provider transcripts, CLI selection, runtime overrides, agent state,
debates, manuscripts, and unrelated session files. Provider transcript
retention and deletion remain governed by the provider.

## Conversation turn recovery

Failure before the user append leaves no transcript evidence. After it, an
ordinary failure normally adds a sanitized error; cancellation best-effort adds
its fixed outcome. A hard interruption or failed reply/error/cancellation append
can leave an unmatched user. Reply-storage failure creates no false provider
error; failed ordinary-error persistence surfaces the storage exception chained
from the provider failure.

Full reads expose an unmatched user; bounded context excludes it. The runtime
and store do not retry or repair it. Inspect, fix the cause, then make an
explicit later call. See [pairing rules](../reference/conversation-transcripts.md)
and the [complete sequence](../reference/runtime-api.md#one-conversation-turn).

A transcript cursor is valid only for the last-sequence snapshot from its first
page. If any event is appended, discard the stale cursor and begin a new read.
A citation remains valid across a subject rename because it uses immutable IDs;
an exact event citation fails closed if its event identity no longer matches.
Search callers must inspect `scan_truncated` before treating an empty or partial
result as exhaustive. Exact recovery conditions are in [Conversation search and
cited transcript pages](../reference/conversation-search.md).

## Current product limits

- Codex is the only implemented provider; `gemini` is reserved and fails
  preflight.
- Provider calls have a configurable timeout but no automatic retry or
  rate-limit manager.
- Stable parallel outcome order is roster order, not completion order.
- Session-wide owner history has no compaction, count limit, expiry, or
  retention policy.
- Filesystem safety relies on Python and the underlying operating system
  correctly implementing the atomic primitives used.
- Debate inspection validates persisted state but cannot establish process
  liveness or validate provider-generated story content.
- The catalog, event store, and search/page service remain Python foundations.
  `StoryRuntime` can persist one explicit-ID provider turn, but there is no
  current selection, automatic dispatch, legacy migration, database,
  persistent index, RAG, or conversation/search/transcript command.
- The interactive command registry and parser do not provide dispatch, a TTY,
  provider control, session switching, artifact actions, `/conversation`,
  `/search`, or `/transcript`.

## Operational response

Preserve plaintext evidence, record the old IDs and output paths, and inspect
before retrying after interruption. Resolve user-level continuity conflicts
explicitly. For provider failure, check executable, login or environment key,
model, timeout, active session, and lease owner. For persisted format errors,
do not let the runtime silently skip damaged records—it intentionally fails
closed.

Use [Inspection and recovery](../guides/inspection-and-recovery.md) for commands,
[Session lifecycle](../design/session-lifecycle.md) for coordination, and
[Filesystem safety](filesystem-safety.md) for durability guarantees.
