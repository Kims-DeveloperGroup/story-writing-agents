# Recovery and limitations

> **Availability:** debate inspection, subject-conversation management,
> advisory turns, active conversation search, and bounded active/archived
> transcript reads are implemented. Automatic retry, stale-owner cleanup,
> event deletion, and `/history` are not.

Key terms: **liveness**—evidence that an owner still runs; **unmatched
turn**—a user event without an outcome; **fresh read**—one new snapshot;
**partial output**—records displayed before interruption.

## Debate, session, and selection recovery

A session/debate ID is reserved before provider work and cannot resume. `active`
does not prove liveness; there is no heartbeat or stale-owner cleanup. Inspect,
repair, and use a new ID. Deadlock has no continuation or manuscript.

A lease is ownership evidence, not remote-health proof. Another or uncertain
owner blocks attachment regardless of age. Never delete coordination files.
End retains provider threads, selections, state, artifacts, and transcripts.

Restart restores only an existing active saved conversation. Missing or
archived records open unselected without rewriting the saved file. Unsafe or
malformed initial storage releases attachment and exits `2`.

Create/use save failure preserves the prior choice, though a created record
remains. Archive writes status before successor selection; failed saving is
recoverable only if unarchive proves the original state and lease. See the
[lifecycle diagram](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).

<a id="conversation-turn-recovery"></a>

## Conversation turn recovery

Failure before user append leaves no transcript evidence. Later failure usually
adds a safe runtime error. Hard interruption or outcome-write failure can leave
an unmatched user: full reads expose it, context excludes it, and nothing
repairs it. See the
[pairing rules](../reference/conversation-transcripts.md).

Shell configuration failures use `provider_preflight_failed`; other backend
protocol failures use `provider_protocol_error`. Unknown failures exit through
`internal_error` cleanup. Raw exception prose is never printed.

## Search and transcript-read failures

Failures are classified without inspecting exception prose:

| Condition | Interactive category |
| --- | --- |
| Invalid command shape or unsafe/overlong query | `command_invalid` |
| Malformed ID or nonexact prefix shorter than 12 | `conversation_invalid` |
| Zero or multiple case-sensitive matches | `conversation_not_found` or `conversation_ambiguous` |
| Unsafe workspace containment | `workspace_unsafe` |
| Invalid catalog, search result, ordinary I/O, or other storage contract | `workspace_invalid` |
| Malformed transcript, stale/range error, count above 100, or tampered page | `transcript_invalid` |

A missing service identity remains `conversation_not_found`. Archived or
cross-session search results are invalid workspace behavior. Service-classified
errors include no rejected values, paths, collaborator results, or exception
messages; parser-owned errors may name a command token or flag.

After `workspace_invalid` or `workspace_unsafe`, the controller confirms that
the same session is active and still owned before returning to the prompt. If
it cannot prove that lease, it clears uncertain process state, reports
`cleanup_failed`, and relies on final backend close instead of claiming safe
recovery.

Ctrl-C during search or transcript reading returns to the same attached scope
without changing selection, sticky target, or durable data. Output is emitted
one hit or event record at a time. If interruption occurs after some records,
those rows remain in scrollback; cancellation does not roll them back or mean
the result was complete. An ordinary output-callback failure similarly leaves
already written rows visible, then exits through internal cleanup.

Every shell transcript command starts a fresh read. Its continuation hint is
not retained cursor state, so use the shown next sequence explicitly. A Python
cursor is valid only for its original snapshot and becomes stale after append.
Check search truncation before treating results as exhaustive. See
[Conversation search](../reference/conversation-search.md).

## Current product limits

- Codex is the only implemented provider; `gemini` is reserved and fails
  preflight.
- Provider calls have a timeout but no automatic retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Filesystem guarantees depend on Python and operating-system primitives.
- The shell sends one synchronous line per advisory turn; it has no multiline
  `/compose`, persistent event loop, or active-turn cancellation.
- `/search` is active-only. There is no archived-search switch, RAG, semantic
  retrieval, database, or persisted search index.
- `/transcript` retains no cursor. There is no legacy migration, event repair,
  event deletion, `/history`, shell debate command, or shell artifact command.

Preserve plaintext evidence and IDs; inspect before retrying. For provider
failure, check executable, login/key, model, timeout, session, and lease. Do not
skip damaged records: persisted format errors fail closed. See
[Inspection and recovery](../guides/inspection-and-recovery.md) and
[Filesystem safety](filesystem-safety.md).
