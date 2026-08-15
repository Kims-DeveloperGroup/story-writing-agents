# Recovery and limitations

> **Availability:** inspection, new-ID debate recovery, subject-conversation
> management, and installed or Python advisory turns are implemented. Automatic
> retry, stale-owner cleanup, and event deletion are not.

Key terms: **immutable reservation**—an ID that cannot be reused; **deadlock**—
unresolved specialist conflict at the round limit; **liveness**—evidence about
whether an owner still runs; **retention**—how long persisted data remains;
**unmatched turn**—a stored user event without an outcome.

## Debate recovery

A session/debate pair is reserved before provider work. `active` proves
reservation, not liveness; there is no heartbeat, stale-owner cleanup, or
same-ID resume. Every terminal or abandoned outcome consumes the ID.

Cancellation propagates and is best-effort recorded as `interrupted`. Inspect
preserved state, resolve conflicts, repair the cause, and use a new debate ID.
A deadlock has no continuation or manuscript. Never delete lifecycle evidence.

## Session and lease recovery

Lease status is evidence, not remote-health proof. Another or uncertain owner
blocks attachment; age does not make it stale. Never delete coordination files
or replace the local-process claim guard. Exact rules are in
[Backend and sessions](../reference/backend-and-sessions.md).

End makes a session inactive but retains provider threads, both CLI selections,
overrides, state, debates, manuscripts, and provider-governed transcripts.

## Conversation selection recovery

Restart restores a saved conversation only when its session-owned catalog record
exists and is active. Missing or archived records open the inner lobby without
rewriting the stale file. Malformed or unsafe storage releases initial
attachment and exits `2`.

If create succeeds but selection save fails, metadata remains and the prior
effective choice stays. Repair storage and select the reported full ID. Failed
`use` saving also preserves the prior choice.

Selected archive writes status before successor or `null`. Save failure
continues only after unarchive proves the original record, selection, and lease;
otherwise it exits `2` with `cleanup_failed`. Restart may show a stale ID as no
selection. Follow the [lifecycle diagram](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).

## Conversation turn recovery

Failure before user append leaves no transcript evidence. Later failure normally
adds a sanitized error; cancellation best-effort adds its fixed outcome. Hard
interruption or outcome-write failure can leave an unmatched user. Reply-storage
failure creates no false provider error.

Full reads expose unmatched users; bounded context excludes them. Nothing
retries or repairs them. Inspect, fix, then call explicitly. See the
[pairing rules](../reference/conversation-transcripts.md) and
[sequence](../reference/runtime-api.md#one-conversation-turn).

For shell turns, configuration failures display `provider_preflight_failed`;
remaining `BackendProtocolError` failures, including provider exit or timeout,
display `provider_protocol_error`. Raw exception prose is not printed. Expected
typed failures return to the prompt; an unknown failure exits through safe
`internal_error` cleanup.

A cursor is valid only for its first-page snapshot; discard it after append.
ID-based citations survive rename, but event mismatches fail closed. Check
`scan_truncated` before treating search as exhaustive. See
[Conversation search](../reference/conversation-search.md).

## Current product limits

- Codex is the only implemented provider; `gemini` is reserved and fails
  preflight.
- Provider calls have a configurable timeout but no automatic retry or
  rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Filesystem guarantees depend on Python and operating-system atomic primitives.
- Inspection cannot prove process liveness or story-content correctness.
- The installed shell dispatches one synchronous line per advisory turn. It has
  no multiline `/compose`, persistent event loop, or active-turn cancellation.
- The shell writes turn transcripts but cannot view history, search, run
  debates, or write story artifacts.
- Transcript reads and search/pages remain Python-only. There is no legacy
  migration, database, persistent index, RAG, `/search`, or `/transcript`.
- The parser and terminal expose more actions than this shell dispatches; their
  capability metadata is not an availability promise.

## Operational response

Preserve plaintext evidence and IDs; inspect before retrying. For provider
failure, check executable, login/key, model, timeout, session, and lease. Do not
skip damaged records: persisted format errors intentionally fail closed.

Use [Inspection and recovery](../guides/inspection-and-recovery.md) for commands,
[Session lifecycle](../design/session-lifecycle.md) for coordination, and
[Filesystem safety](filesystem-safety.md) for durability guarantees.
