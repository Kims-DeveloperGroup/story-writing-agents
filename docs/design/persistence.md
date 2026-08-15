# Persistence design

> **Availability:** filesystem persistence covers configuration, session and
> conversation selections, provider sessions, debates, conversation metadata
> and transcripts, and manuscripts.

Key terms: **blackboard**—the public debate log; **owner-isolated state**—
history visible only to its agent; **lifecycle**—reservation and terminal status;
**transcript**—one conversation's visible event history; **durability**—which
completed file changes survive interruption.

The workspace uses inspectable files instead of a database. Channels stay
separate:

| Channel | Durable owner or reader |
| --- | --- |
| Public debate JSONL and lifecycle/result JSON | Runtime and inspection |
| Per-agent JSONL | Only its owning agent |
| Provider manifest and temporary lease | `AgentBackend` |
| Story-session and conversation selections | CLI context helpers |
| Sticky interactive target | Controller memory only; never persisted |
| Conversation metadata | Catalog adapter |
| Conversation transcript JSONL | Event-store adapter |
| Approved manuscript | Workspace commit path |

## Conversation durability

Subject metadata and per-session selection are different files. Create writes
metadata before saving the new selection; use changes only selection; rename
changes only metadata. Archive of the selected record crosses both files and
uses the bounded compensation described in the [lifecycle diagram](session-lifecycle.md#conversation-lifecycle-and-compensation).
There is no atomic transaction across those files. A stale saved ID is retained
for diagnosis and becomes an unselected lobby state at restart.

Each accepted interactive line delegates persistence to
`StoryRuntime.converse()`: it saves the user event before its one provider call,
then attempts to save a valid `agent_reply` or a sanitized `runtime_error`.
Terminal status and reply rendering create no extra event. The event store never
calls or retries the provider.

A hard interruption or failed reply/error/cancellation write can leave only the
user event. That unmatched event stays authoritative but is excluded from later
complete-turn context. The exact operation belongs
to the [Runtime API](../reference/runtime-api.md#one-conversation-turn); operator
response belongs to [Recovery](../operations/recovery-and-limitations.md#conversation-turn-recovery).

## What is retained

The blackboard keeps original input, candidates, verdict notes, critiques,
proposals, visible errors, finalization metadata, and commit record. Terminal
result JSON omits its duplicated message list; inspection combines it with
validated JSONL and lifecycle state. Agent state deliberately avoids copying
the entire prompt, candidate, or core documents, but keeps generic outcomes and
the owner's visible notes, critiques, and proposals, capped per entry. A
provider may still echo sensitive material into those fields.

Session override JSON survives runtime instances. Provider manifests preserve
compatible thread IDs across debates, detach, end, and backend close. CLI
session and conversation selections survive session end. Conversation metadata
retains its exact display subject when archived. Its transcript retains every
visible event, including an unmatched user remainder. None of these actions is
deletion or secure erasure.

## Ownership and durability boundaries

The workspace validates and mutates one channel at a time. Blackboard and
owner histories append separately; terminal result is a projection, not a
second message store. Approved artifact coordination crosses core documents and
one manuscript. Each selection or metadata replacement is atomic only for its
own file. The event store owns transcript replacement and context projection.
Neither conversation adapter performs migration, and absent reads create
nothing.

The conversation memory service is another projection, not a persistence
owner. It reads the catalog and event-store contracts but stores no search
index, result, citation, page, or cursor and changes no metadata or transcript.
Its exact read contract lives in [Conversation search and cited transcript
pages](../reference/conversation-search.md).

Because these channels are separate files, persistence design does not imply a
database transaction across them. The normative containment, exclusive-create,
atomic-file, write-order, rollback, and interruption guarantees live in
[Filesystem safety](../operations/filesystem-safety.md); recovery behavior lives
in [Recovery and limitations](../operations/recovery-and-limitations.md).

The complete path tree and strict schemas live in
[Session storage](../reference/configuration/session-storage.md). Filesystem
trust boundaries are in [Filesystem safety](../operations/filesystem-safety.md).
The exact event and context contract lives in
[Conversation transcripts](../reference/conversation-transcripts.md).
