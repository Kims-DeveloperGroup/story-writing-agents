# Conversation search and cited history pages

<a id="schema-v2-readable-collaboration-history"></a>

The selected filesystem or SQLite conversation bundle exposes one provider-free
schema-v2 search and inspection path. Search reads canonical collaboration
events; catalog subjects are display metadata, not searchable text.

## Shell workflow

With a session attached:

```text
/search QUERY
/transcript [ID [COUNT] [--from SEQUENCE]]
/history [COUNT]
```

`/search` scans active conversations only. `/transcript` accepts an active or
archived conversation and does not change focus. `/history` reads the active
focus. All three read local storage without binding or calling a provider and
without appending an event.

Bare `/transcript` opens the attached session's active and archived conversation
picker; an explicit ID reads directly. Choices show `Subject (conversation_id)`
with full IDs in catalog order. Selection resolves the ID again before reading.
An empty catalog, cancellation, Ctrl-C, or EOF reads no transcript and preserves
attachment, focus, and conversation history.

A search hit has this semantic shape (IDs and subject vary):

```text
Search results (1/1)
  Status: active
  Subject (display only): Focused collaboration
  Citation: conversation c_12ab34cd56, event proposal_event, sequence 1, field proposal.purpose, range 0:19
  Snippet: consult specialists before recommending the next action.
[result] Search returned 1 of 1 matching readable fields.
```

The range is zero-based and end-exclusive in Unicode code points. The subject
helps a user recognize the conversation but is never indexed. Consequently,
this new catalog entry produces no match:

```text
/conversation new Opening review
/search Opening review
[result] Search returned 0 of 0 matching readable fields.
```

Follow a hit with its visible handle:

```text
/transcript c_12ab34cd56 1 --from 1
[result] Transcript page returned 1 records.
[hint] Continue with /transcript c_12ab34cd56 1 --from 2.
```

The request, records, result, and continuation hint appear inside the transient
viewer. Closing clears them and restores the preceding shell screen, as
`/exchanges` does. Without `COUNT`, the viewer instead includes the complete
conversation, starting at `--from` when supplied.

## Search authority

`CollaborationHistorySearchQuery` is a bounded literal query.
`CollaborationHistorySearchScope` fixes one session and whether archives are
eligible. `CollaborationHistoryService.search()` returns
`CollaborationHistorySearchResult` with closed readable-field hits. Each
`CollaborationHistoryCitation` fixes:

- session and conversation IDs;
- canonical event ID and sequence;
- `CollaborationReadableFieldKey`; and
- start/end code-point offsets in that exact field value.

The implementation normalizes no subject into event content. Before returning
a derived hit, the active service reauthorizes its conversation and canonical
event/field coordinates. Malformed, private, cross-session, archived-out-of-
scope, or changed facts fail closed.

## Read and continuation APIs

```python
scope = CollaborationHistorySearchScope("novel", include_archived=False)
service = CollaborationHistoryService(
    scope,
    conversation_storage_services=services,
)

result = service.search(CollaborationHistorySearchQuery("Consult specialists"))
page = service.read_history(
    conversation_id,
    start_sequence=1,
    end_sequence=None,
    max_records=24,
    citation=result.hits[0].citation,
)
if page.next_cursor is not None:
    page = service.continue_history(page.next_cursor)
```

`read_history()` validates catalog scope, rereads canonical events once, can
resolve an exact citation, and returns bounded `CollaborationReadableRecord`
values. `continue_history()` accepts only the same immutable snapshot. Its
cursor binds session/conversation, next sequence, limits, snapshot last
sequence, and tail-batch digest. Any append makes the cursor stale; start a
fresh read rather than joining two snapshots.

Inside an explicitly bounded `/transcript` viewer, the continuation hint shows a
new command rather than retaining a storage cursor. Each invocation reads one
fresh validated snapshot; scrolling stays within that snapshot, even if the
conversation later changes. Without `COUNT`, the complete selected suffix is
available in the viewer. Direct Python callers that require snapshot continuity
use `continue_history()`.

## Public boundaries

The direct `story_writing_agents.conversation_search` module exports the v2
query, result, citation, page, cursor, reference-search, and service contracts.
The selected `ConversationStorageServices` supplies one catalog, one
`CollaborationEventStore`, one aggregate search adapter, and one mutation
coordinator with identical provenance. Concrete filesystem and SQLite event
store/search adapters are direct-module-only implementation choices, not
package-root, workspace-facade, or storage-facade exports.

Search does not generate story text. The low-level provider action sequence is
documented separately in [Conversation retrieval](conversation-retrieval.md).
See [Conversation transcripts](conversation-transcripts.md) for record fields
and [Collaboration history](collaboration-history.md) for event/state rules.
