# Conversation transcripts

> **Availability:** transcript persistence and read-only search/pages are
> implemented Python APIs. `StoryRuntime` uses the event-store contract for one
> explicit-ID provider turn; interactive selection and transcript commands are
> not implemented.

Key terms: **event**—one visible stored record; **complete turn**—one user
event plus one outcome; **unmatched turn**—a user event with no outcome yet;
**transcript**—the complete local event history; **context window**—a bounded
view containing only complete turns.

The transcript is the authoritative visible history for one conversation.
`read()` returns that full history; `read_context()` derives the smaller view
used by a runtime provider call without changing the transcript. [Conversation search
and cited transcript pages](conversation-search.md) defines a separate bounded
read projection over this same authoritative history.

## Public contract

The package facade exports these frozen, slotted value types, protocol, policy,
and filesystem adapter:

```python
ConversationEventAppend(session_id, conversation_id, turn_id,
                        sender, recipient, kind, content)
ConversationEvent(schema_version, session_id, conversation_id, turn_id,
                  sequence, timestamp, sender, recipient, kind, content)
ConversationContextPolicy(max_complete_turns, max_utf8_bytes)
ConversationContextWindow(events, total_complete_turns, retained_turns,
                          omitted_turns, retained_utf8_bytes, truncated)

CONVERSATION_EVENT_SCHEMA_VERSION = 1
CONVERSATION_CONTEXT_POLICY = ConversationContextPolicy(24, 32_768)
FilesystemConversationEventStore(workspace, *, catalog=None)
```

`ConversationEventStore` defines three operations:

```python
append(event: ConversationEventAppend) -> ConversationEvent
read(session_id: str, conversation_id: str) -> tuple[ConversationEvent, ...]
read_context(session_id: str, conversation_id: str) -> ConversationContextWindow
```

The caller supplies the logical event. `append()` assigns `schema_version`, a
one-based contiguous `sequence`, and a canonical UTC `timestamp`. Sequence is
the only authoritative ordering field; timestamps may be equal or move
backward when the wall clock changes.

## Exact event schema

Each line of `transcript.jsonl` is one JSON object with exactly these ten keys:

```json
{"schema_version":1,"session_id":"novel","conversation_id":"c_opening","turn_id":"turn_1","sequence":1,"timestamp":"2026-08-15T10:00:00+00:00","sender":"user","recipient":"plot_architect","kind":"user_message","content":"Should the reveal happen here?"}
```

No key may be missing, extra, or duplicated. IDs use the common
[safe-identifier contract](configuration/identifiers-and-errors.md) and must
match the owning path. Content must be a nonempty strict UTF-8 string and is
preserved without trimming or rewriting. `to_dict()` returns this exact
JSON-compatible shape.

`ConversationEventKind` fixes the valid actor pairs:

| Kind | Sender | Recipient | Pairing rule |
| --- | --- | --- | --- |
| `user_message` | `user` | one canonical agent | Starts a unique turn ID |
| `agent_reply` | addressed agent | `user` | Completes that agent's user turn |
| `runtime_error` | `runtime` | `user` | Completes an existing user turn |

An outcome cannot precede its user event, and a turn cannot have two outcomes.
Runtime-error text is visible content. The store does not redact it; direct
callers must sanitize their own values. `StoryRuntime` sanitizes the failures it
records, as defined in [Privacy](../operations/privacy-and-provider.md#runtime-error-diagnostics).

## Example: interruption leaves evidence

Suppose the user asks `plot_architect` about an opening scene:

1. `StoryRuntime` appends `turn_7` as `user_message` before provider work.
2. A hard interruption or outcome-storage failure prevents an outcome append.
3. `read()` still returns `turn_7`, preserving what happened locally.
4. `read_context()` excludes `turn_7` because it is incomplete. The store does
   not retry it; a later workflow must decide explicitly what to do.

The [Runtime API](runtime-api.md#one-conversation-turn) defines the complete
success, ordinary-failure, and cancellation sequence.

## Context selection

A complete turn is one `user_message` plus exactly one `agent_reply` or
`runtime_error`. Context selection walks complete turns newest to oldest and
retains at most 24 turns and 32,768 combined UTF-8 content bytes.

Selection stops at the first turn that would violate either cap. It never
splits a turn or skips an oversized turn to take an older one. Returned events
are restored to storage order. Unmatched user events remain in the transcript
but are not counted as complete, retained, or omitted turns.

The window reports total, retained, and omitted complete-turn counts, retained
content bytes, and whether truncation occurred. It is a projection only: it
does not compact, rewrite, or delete stored events.

## Storage and validation boundary

The filesystem path is
`discussion_logs/<session_id>/interactive/conversations/<conversation_id>/transcript.jsonl`.
The owning catalog identity must exist, but it may be archived. After that
identity check, a missing transcript reads as empty without creating files; a
present empty, malformed, noncontiguous-sequence, invalidly paired, or
path-mismatched transcript fails closed with `WorkspaceFormatError`.

The adapter validates the complete stored history before every read, context
selection, or append. It never invokes a provider, changes selection, performs
search itself, migrates legacy data, or deletes events. See
[Conversation catalog](conversation-catalog.md) for metadata,
[Conversation search](conversation-search.md) for the read-only consumer,
[Filesystem safety](../operations/filesystem-safety.md) for mutation and
locking, and [Privacy](../operations/privacy-and-provider.md) for exposure.
