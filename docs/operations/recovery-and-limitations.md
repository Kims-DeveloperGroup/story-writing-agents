# Recovery and limitations

> **Availability:** inspection and new-ID recovery are implemented. Automatic
> retry, same-debate continuation, stale-owner cleanup, conversation-event
> deletion, and interactive search/transcript commands are not.

Key terms: **immutable reservation**—an ID that cannot be reused; **deadlock**—
unresolved specialist conflict at the round limit; **liveness**—evidence about
whether an owner still runs; **retention**—how long persisted data remains;
**unmatched turn**—a stored user event without an outcome.

## Debate recovery

A session/debate pair is atomically reserved before a provider call. The
persisted owner token coordinates state transitions but is not user
authentication. `active` proves reservation only; there is no heartbeat,
timeout, process-liveness check, stale-owner cleanup, or same-ID resume.

External coroutine cancellation propagates to provider calls and is
best-effort recorded as terminal `interrupted`. Consensus, deadlock, failure,
interruption, and abandoned active work all consume the ID. Inspect preserved
state, make any user ruling, repair the cause, and start a new debate ID.

A deadlock includes `action_required` but has no continuation or ruling API.
It writes no manuscript. Provider/protocol/finalization failures usually return
`failed`; artifact failures may require filesystem inspection because approved
multi-file commit is not a transaction. Never delete lifecycle evidence merely
to reuse an ID.

## Session and lease recovery

Lease status is conservative operational evidence, not proof of ownership or
remote health. Another or uncertain owner blocks attachment; do not infer
staleness from age or manually delete coordination files. The runtime has no
automatic lease expiry or stale-owner cleanup. Exact classification,
platform-specific probing, and reclamation rules are defined only in
[Backend and sessions](../reference/backend-and-sessions.md).

The persistent claim guard coordinates only cooperating local processes. It is
not authentication, a distributed lock, or a network-filesystem guarantee. Do
not replace its pathname while the workspace may be active.

Ending a session makes it inactive but retains provider thread references,
external provider transcripts, CLI selection, runtime overrides, agent state,
debates, manuscripts, and unrelated session files. Provider transcript
retention and deletion remain governed by the provider.

## Conversation recovery

A caller must persist the visible user event before future provider work. If
that work is interrupted before a reply or terminal error is appended, the
user event remains in the authoritative transcript. Full reads expose it for
diagnosis; bounded context excludes it because the turn is incomplete.

The store does not automatically retry or complete that turn, and it does not
delete, compact, or silently repair it. A later workflow must make recovery
explicit. See
[Conversation transcripts](../reference/conversation-transcripts.md) for the
single-turn example and exact pairing rules.

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
- The conversation catalog, event store, and read-only search/page service are
  Python foundations only. They do not provide current selection, provider
  dispatch, legacy migration, a database, a persistent index, RAG, or
  conversation/search/transcript commands.
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
