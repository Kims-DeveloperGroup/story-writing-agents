# Conversation transcripts

> **Availability:** interactive turns use the startup-selected filesystem or
> SQLite event store, append transcripts, and consume bounded context.
> `/transcript` reads bounded pages; focused `/history` shows one full snapshot
> with context accounting; `/search` cites active events. Both adapters remain
> available through their documented Python imports; direct runtime
> construction defaults to filesystem unless supplied a complete bundle.

Key terms: **event**—one visible stored record; **complete turn**—one user event
plus one outcome; **unmatched turn**—a user event without an outcome;
**transcript**—the authoritative local event history; **context window**—a
bounded complete-turn projection for provider input.

The transcript is authoritative. `read()` returns its complete stored history;
`read_context()` derives provider context without changing it. The
[memory service](conversation-search.md) supplies bounded search and read
projections over the same events.

## Public contract

The package-root facade exports these frozen, slotted contracts and filesystem
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

The established
`story_writing_agents.FilesystemConversationEventStore` and
`story_writing_agents.workspace.FilesystemConversationEventStore` imports are
aliases of the same class defined by
`story_writing_agents.conversation_storage.filesystem`. Direct owner-module
imports are available for internal or advanced ownership clarity. The
`story_writing_agents.conversation_storage` package facade deliberately does
not export the class.

`ConversationEventStore` defines:

```python
append(event: ConversationEventAppend) -> ConversationEvent
read(session_id: str, conversation_id: str) -> tuple[ConversationEvent, ...]
read_context(session_id: str, conversation_id: str) -> ConversationContextWindow
```

`select_bounded_conversation_context(events)` is supported from
`story_writing_agents.conversations`, not the package facade. It validates/selects
from one exact tuple; the store protocol, facade exports, and schemas are unchanged.

The optional adapter implements the same event-store contract:

```python
from story_writing_agents.conversation_storage.sqlite import (
    SQLiteConversationEventStore,
)

store = SQLiteConversationEventStore(
    workspace,
    mutation_coordinator=None,
    mutation_timeout_seconds=5.0,
)
```

Construction does not provision the database. Use the explicit flow in the
[package API](package-api.md#optional-sqlite-conversation-storage) before an
operation, or the [next-shell selection workflow](cli.md#conversation-storage-selection)
for the installed shell.

`append()` assigns schema version, a one-based contiguous `sequence`, and a
canonical UTC timestamp. Sequence is authoritative ordering; wall-clock
timestamps may be equal or move backward.

## Event meaning

In the filesystem adapter, each `transcript.jsonl` line is one JSON object with
exactly ten keys:

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

The SQLite adapter stores those same ten public values as normalized event
rows. Required canonical `searchable_content` supports exact literal search;
private `turn_slot` and database constraints enforce one user event and at most
one outcome for a turn, contiguous sequence, addressed reply ownership, and the
catalog foreign key. Neither derived value is a public event field. Reads
reconstruct the exact `ConversationEvent` values and ordering and reject a
stored lexical projection that differs from the public content. Stored `TEXT`
is decoded as strict UTF-8; malformed bytes fail closed through a content-free
`WorkspaceFormatError`.

## Interactive read bridge

`/transcript` reads a fresh bounded page from an attached active or archived
identity without focusing it. `/history [COUNT]` requires current focus on an
active conversation and reads the store once. Omission displays all; a positive count
displays newest `min(count,total)` chronologically. Every kind, unmatched user,
and whitespace stays visible; empty is normal. Its same-tuple summary reports
displayed/total events, canonical turns/bytes/truncation, and fixed caps. It has
no cursor, pager, search, provider, or write.

Each event renders once as a speaker-aware chat block while storage stays exact.
The surrounding ListView supplies its position/total, and the block uses a human
direction header: `You → Agent`, `Agent → You`, or `Runtime → You`. Sequence is
always present. With Message details On, the same metadata line also includes
Turn, Kind, and the canonical sender-to-recipient IDs. The full body is visible
in both modes; Off is compact presentation, not content deletion. The header is
directly contiguous with its railed body, and the block owns one unrailed
trailing separator so adjacent transcript owners cannot visually merge.

For presentation, an `agent_reply` event attempts strict complete-object/array
JSON formatting with two-space indentation, preserved key order, and Unicode
output; malformed, scalar, duplicate-key, non-finite, embedded-prose, or
otherwise unserializable content is displayed unchanged. User messages and
runtime errors are never JSON-projected. This projection is not persisted and
does not alter search or context data. Ctrl-C during history stops after
completed message blocks, emits its fixed cancellation instead of the summary,
and performs no second read or write. See
[terminal presentation](interactive-terminal.md) and [page/search limits](conversation-search.md).

## Context-window derivation and failure evidence

A complete turn is one `user_message` plus one `agent_reply` or
`runtime_error`. The pure selector and filesystem store walk complete turns
newest to oldest and retain at most 24 turns and 32,768 combined content bytes.
The SQLite store returns the identical projection from one read transaction:
it obtains the exact complete-turn total, materializes at most the newest 25
indexed outcomes and their addressed users—50 events—and uses the extra pair
to detect the 24-turn boundary. Derivation stops at the first turn that would
exceed either cap, never splits a turn, and restores retained events to storage
order. Its count, ordered-limit, and addressed lookup queries remain compatible
with the documented SQLite 3.7.16 minimum and require no window functions.

After an advisory user append, `asyncio` cancellation best-effort adds
`runtime_error` content `CancelledError: conversation turn was cancelled`;
ordinary failures attempt a `runtime_error` through the safe diagnostic policy.
A provider `KeyboardInterrupt` or `SystemExit` appends no transcript outcome.
The runtime attempts a separate content-safe `cancelled`/`interrupted`
operational record and re-raises the identical exception without automatic
retry. Failed outcome storage or that process-control path leaves the user
unmatched. Full reads expose it; context excludes it without retry or repair.
The window reports
total/retained/omitted complete turns, bytes, and truncation. It never compacts,
rewrites, or deletes events.

## Storage and validation boundary

The `story_writing_agents.conversation_storage.filesystem` owner holds the weak
process-wide per-conversation lock across filesystem catalog validation,
transcript parsing, complete-file replacement, and context selection. It owns
the exact JSONL serialization and path
`discussion_logs/<session_id>/interactive/conversations/<conversation_id>/transcript.jsonl`.
The catalog identity must exist but may be archived. A missing transcript reads
as empty without creating a file. A present empty, malformed,
noncontiguous-sequence, invalidly paired, or path-mismatched file fails closed
with `WorkspaceFormatError`.

The SQLite adapter stores all sessions and conversations in the fixed optional
`discussion_logs/conversation_storage.sqlite3` database. A conversation with
no event rows reads as empty. Each ordinary read uses one explicit stable
read-only transaction; each append uses one coordinator-first write
transaction. These guarantees cover one adapter operation, not multiple service
calls as one database-wide snapshot.

Both adapters validate the addressed complete history for a full `read()`.
Filesystem context derivation also validates that complete tuple. SQLite
`read_context()` instead uses its exact aggregate count and addressed indexed
queries described above; it never materializes more than 25 complete pairs.
SQLite append likewise remains history-bounded inside its write transaction: it
obtains one plus the indexed maximum sequence and reads at most the addressed
turn's user/outcome pair through the unique turn-slot index. Constraints and
triggers enforce the stored sequence, pairing, addressed reply, and catalog
ownership without a full-transcript scan. Explicit SQLite preflight and archive
boundaries retain their documented global validation. Neither adapter invokes
a provider, changes defaults/focus, searches, migrates old data, or deletes
events. See
[Filesystem safety](../operations/filesystem-safety.md) for mutation and
locking and [Privacy](../operations/privacy-and-provider.md) for exposure.
