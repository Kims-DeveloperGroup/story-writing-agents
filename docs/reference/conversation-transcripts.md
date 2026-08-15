# Conversation transcripts

> **Availability:** interactive turns append transcripts and consume bounded
> context. After attachment, `/transcript` reads fresh bounded pages and
> `/search` can cite active transcript events. The storage APIs remain public.

Key terms: **event**—one visible stored record; **complete turn**—one user event
plus one outcome; **unmatched turn**—a user event without an outcome;
**transcript**—the authoritative local event history; **context window**—a
bounded complete-turn projection for provider input.

The transcript is authoritative. `read()` returns its complete stored history;
`read_context()` derives provider context without changing it. The
[memory service](conversation-search.md) supplies bounded search and read
projections over the same events.

## Public contract

The package facade exports these frozen, slotted contracts and filesystem
adapter:

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

`ConversationEventStore` defines:

```python
append(event: ConversationEventAppend) -> ConversationEvent
read(session_id: str, conversation_id: str) -> tuple[ConversationEvent, ...]
read_context(session_id: str, conversation_id: str) -> ConversationContextWindow
```

`append()` assigns schema version, a one-based contiguous `sequence`, and a
canonical UTC timestamp. Sequence is authoritative ordering; wall-clock
timestamps may be equal or move backward.

## Event meaning

Each `transcript.jsonl` line is one JSON object with exactly ten keys:

```json
{"schema_version":1,"session_id":"novel","conversation_id":"c_opening","turn_id":"turn_1","sequence":1,"timestamp":"2026-08-15T10:00:00+00:00","sender":"user","recipient":"plot_architect","kind":"user_message","content":"Should the reveal happen here?"}
```

No key may be missing, extra, or duplicated. IDs must match the owning path.
Content is a nonempty strict-UTF-8 string preserved without trimming or
rewriting; whitespace-only, multiline, and trailing-LF content are valid.

| Kind | Sender | Recipient | Pairing rule |
| --- | --- | --- | --- |
| `user_message` | `user` | one canonical agent | Starts a unique turn ID |
| `agent_reply` | addressed agent | `user` | Completes that agent's user turn |
| `runtime_error` | `runtime` | `user` | Completes an existing user turn |

An outcome cannot precede its user event, and one turn cannot have two
outcomes. Runtime-error text is visible content. Direct callers must sanitize
their own values; `StoryRuntime` applies the
[runtime diagnostic policy](../operations/privacy-and-provider.md#runtime-error-diagnostics).

## Interactive read bridge

The controller gives the memory service the attached session and shared event
store. A transcript command may explicitly read an active or archived catalog
identity and does not require or change conversation selection. Every command
obtains a fresh snapshot and returns chronological, whole events; the shell
does not retain a cursor or write back to the transcript.

Each event is passed once to the terminal's primitive renderer. Storage content
remains exact while controls become visible and line structure remains within
one output record. Exact presentation belongs to the
[terminal reference](interactive-terminal.md), and page limits and stale-range
rules belong to [Conversation search](conversation-search.md).

## Context selection and failure evidence

A complete turn is one `user_message` plus one `agent_reply` or
`runtime_error`. Context selection walks complete turns newest to oldest and
retains at most 24 turns and 32,768 combined content bytes. It stops at the
first turn that would exceed either cap, never splits a turn, and restores
retained events to storage order.

If interruption or outcome-storage failure follows a durable user append, the
user event remains unmatched. Full reads expose it; context excludes it and
does not retry or repair it. The window reports total, retained, and omitted
complete turns, retained bytes, and truncation. It never compacts, rewrites, or
deletes events.

## Storage and validation boundary

The path is
`discussion_logs/<session_id>/interactive/conversations/<conversation_id>/transcript.jsonl`.
The catalog identity must exist but may be archived. A missing transcript reads
as empty without creating a file. A present empty, malformed,
noncontiguous-sequence, invalidly paired, or path-mismatched file fails closed
with `WorkspaceFormatError`.

The adapter validates the complete history before read, context selection, or
append. It never invokes a provider, changes selection, searches, migrates
legacy data, or deletes events. See [Filesystem safety](../operations/filesystem-safety.md)
for mutation and locking and [Privacy](../operations/privacy-and-provider.md)
for exposure.
