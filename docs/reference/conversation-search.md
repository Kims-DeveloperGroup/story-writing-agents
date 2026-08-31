# Conversation search and bounded transcript reads

> **Availability:** the installed shell exposes active-only `/search` and fresh
> `/transcript` pages after story-session attachment. The same provider-free
> capabilities are package-supported Python APIs. The shell uses its
> startup-selected filesystem or SQLite bundle; the direct-module
> `SQLiteConversationSearch` implements the same exact contract. Direct runtime
> construction still defaults to filesystem unless supplied a complete bundle.
> Separately, this module now exposes pure direct-only schema-v2 collaboration
> search, exact citation, snapshot-page, and derived-document-ID contracts.
> Those additions are not package-root exports and are not connected to the
> current shell, storage adapters, runtime, RAG, or provider calls; #475-#479
> own later integration.

Key terms: **scope**—one authorized story session; **hit**—the strongest match
from one conversation; **citation**—stable identity plus an optional exact
event reference; **snapshot**—the transcript end observed for one read;
**cursor**—Python continuation data for that unchanged snapshot.

The established schema-v1 search and transcript-page service below is unchanged.
Schema-v2 readable collaboration history uses the separate direct-only section
and never accepts schema-v1 events.

## Shell bridge

Neither command needs current conversation focus. `/search` constructs the
default `ConversationSearchQuery`: active records only, up to 10 hits. The
controller preserves the service's deterministic order and renders a
conversation-only citation for subject matches or a turn/sequence/event
citation for transcript matches.

`/transcript` resolves an exact ID or a case-sensitive unique prefix of at
least 12 characters across active and archived records. Each invocation asks
the memory service for a fresh full-snapshot range, starting at sequence 1 and
returning at most 24 events unless the command supplies other values. The
maximum is 100. A continuation hint exposes the next sequence and a copyable
visible handle, but the controller stores no cursor.

Search/read never changes defaults, either focus, provider state, or durable data.
See the [parser](interactive-command-parser.md) for grammar, the
[terminal](interactive-terminal.md) for records, and
[Story sessions](../guides/story-sessions.md) for the beginner flow.

<a id="schema-v2-readable-collaboration-history"></a>

## Schema-v2 readable collaboration history

### User impact and scenario

A future application can use one complete canonical schema-v2 collaboration
history to answer a user's inspection question without invoking a provider. For
example, it can search `OUTLINE RANGE`, show that the match occurred in the
proposal-purpose field, resolve the exact original text and Unicode range, and
page neighboring events from one unchanged snapshot. If the user explicitly
selects that event for later context, the pure selector can include the whole
record under fixed limits.

Today, a Python integration can call these pure functions directly. A shell
user cannot yet search or page v2 collaboration history, current storage
adapters do not persist it, and no readable record is automatically sent to a
provider or RAG service. Default context is content-free. Integration is owned
by #475-#479.

Before #474, `project_collaboration_history()` exposed current state and
recovery facts only. After #474, one separate readable record per event supports
inspection and search while preserving the complete typed payload. The state
projection is still the value a future `CollaborationEventStore.read_context()`
would return; `CollaborationReadableContext` is instead a bounded explicit
record selection for possible later provider context. Neither value is
execution authority.

### Direct import surface

The 12 additive names are supported only from the direct module:

```python
from story_writing_agents.conversation_search import (
    COLLABORATION_READABLE_SOURCE_SCHEMA_ID,
    CollaborationHistoryCitation,
    CollaborationHistoryPage,
    CollaborationHistoryPageCursor,
    CollaborationHistorySearchHit,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchResult,
    collaboration_readable_document_id,
    continue_collaboration_history,
    page_collaboration_history,
    resolve_collaboration_citation,
    search_collaboration_history,
)
```

The direct module exports exactly 25 names: these 12 plus the established 13
schema-v1 conversation search/page names. None of the 12 is exported from
`story_writing_agents`. The readable record/field projection and bounded
context contracts are separately available from the 83-name
`story_writing_agents.conversations` facade.

Public function signatures are:

```python
search_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    query: CollaborationHistorySearchQuery,
) -> CollaborationHistorySearchResult
page_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    *,
    start_sequence: int = 1,
    end_sequence: int | None = None,
    max_records: int = 24,
    max_utf8_bytes: int = 32_768,
) -> CollaborationHistoryPage
continue_collaboration_history(
    events: tuple[CollaborationEvent, ...],
    cursor: CollaborationHistoryPageCursor,
) -> CollaborationHistoryPage
resolve_collaboration_citation(
    events: tuple[CollaborationEvent, ...],
    citation: CollaborationHistoryCitation,
) -> str
collaboration_readable_document_id(
    *,
    workspace_id: str,
    metadata_revision: int,
    record: CollaborationReadableRecord,
    field: CollaborationReadableField,
) -> str
```

Every operation first depends on a fully canonical v2 history/readable record.
Schema-v1 events and corrupt v2 histories fail rather than producing partial
read results.

### Search and exact citation contract

`CollaborationHistorySearchQuery(text, limit=10)` uses the same literal
strict-UTF-8, NFKC, case-fold, category-C, and whitespace rules as the
established search. Input is nonblank, at most 200 characters before and after
normalization, and cannot contain a category-C character. The maximum hit limit
is 50. Search examines no more than 10,000 readable records and returns the
first normalized-literal occurrence from each matching closed field.

Hits are ordered by event sequence, closed readable-field order, zero-based
assignment ordinal, then zero-based input ordinal. A hit includes a bounded
normalized snippet and `CollaborationHistoryCitation`. The citation contains
session/conversation/event identity, one-based event sequence, field key,
optional nested ordinal, and zero-based start-inclusive/end-exclusive Unicode
codepoint offsets in the original field.

Normalization retains an exact map to the original text: `Straße` can match
`STRASSE`, full-width `ＡＢＣ` can match `abc`, and `e` plus a combining accent
and repeated spaces before `vessel` can match `é vessel` while still citing the
complete original codepoint range. `resolve_collaboration_citation()` validates
the current history and returns the exact original substring. It rejects a stale
event ID/sequence, another history identity, missing field/ordinal, or range
past the current field.

```python
query = CollaborationHistorySearchQuery(text="OUTLINE RANGE", limit=1)
result = search_collaboration_history(history, query)
hit = result.hits[0]
output = {
    "normalized_query": result.normalized_query,
    "event_id": hit.citation.event_id,
    "field": hit.citation.field_key.value,
    "range": (hit.citation.start_codepoint, hit.citation.end_codepoint),
    "original": resolve_collaboration_citation(history, hit.citation),
}
# For the canonical example in collaboration-history.md:
assert output == {
    "normalized_query": "outline range",
    "event_id": "proposal_event",
    "field": "proposal.purpose",
    "range": (37, 50),
    "original": "outline range",
}
```

### Snapshot paging

Page ranges are inclusive and records remain event-atomic in canonical
sequence. The default is 24 records; the fixed maxima are 100 records and
32,768 aggregate readable-field UTF-8 bytes. An oversized first record fails
instead of being split. Starting exactly one past the current end returns an
empty page; invalid or stale ranges fail.

An initial page snapshots both `snapshot_last_sequence` and
`snapshot_tail_batch_digest`. Its cursor preserves those facts, the requested
end, next sequence, and count/byte limits. Continuation validates the entire
history again and rejects either tail fact changing, including an ordinary
append. It also rejects a cursor for another session/conversation.

```python
page = page_collaboration_history(history, max_records=1)
page_output = {
    "event_ids": tuple(record.event_id for record in page.records),
    "snapshot_last_sequence": page.snapshot_last_sequence,
    "has_more": page.next_cursor is not None,
}
assert page_output == {
    "event_ids": ("proposal_event",),
    "snapshot_last_sequence": 2,
    "has_more": True,
}
assert page.next_cursor is not None
continued = continue_collaboration_history(history, page.next_cursor)
assert tuple(record.event_id for record in continued.records) == ("request_event",)
```

### Derived readable-document identity

`COLLABORATION_READABLE_SOURCE_SCHEMA_ID` is exactly
`story-collaboration-readable-event-field-v2`. For one field belonging to one
readable record, `collaboration_readable_document_id()` returns `sha256:` plus
64 lowercase hexadecimal digits. It length-frames the complete canonical event
and every fixed schema/workspace/metadata/field coordinate and readable-text
fact before hashing. A different workspace, metadata revision, event byte,
field, ordinal, or readable text produces a different ID.

```python
document_id = collaboration_readable_document_id(
    workspace_id="workspace_a",
    metadata_revision=1,
    record=records[0],
    field=records[0].fields[0],
)
assert document_id.startswith("sha256:")
assert len(document_id) == 71
```

The identity is for derived lookup/context sources only. It is never event or
batch identity, an append receipt, structured confirmation, provider/RAG
authorization, or state/recovery evidence. This function performs no I/O and
does not create a document.

See [Structured collaboration history](collaboration-history.md#readable-record-and-field-projection)
for the complete field set and
[state projection versus context](collaboration-history.md#state-projection-versus-possible-provider-context)
for the content-free default and explicit selection example.

## Python service

```python
import story_writing_agents as swa

workspace = swa.Workspace("/path/to/initialized-story")
catalog = swa.FilesystemConversationCatalog(workspace)
store = swa.FilesystemConversationEventStore(workspace, catalog=catalog)
memory = swa.ConversationMemoryService(swa.ConversationSearchScope("novel"), catalog, store)

result = memory.search(swa.ConversationSearchQuery("council vessel"))
if result.hits:
    citation = result.hits[0].citation
    page = memory.read_transcript(citation.conversation_id,
                                  start_sequence=citation.sequence or 1,
                                  max_events=1, citation=citation)
```

The service retains its collaborators and authorized scope but owns no
persistence or provider resource.

For an already provisioned optional SQLite database, direct Python code can
compose the direct SQLite implementation with one shared provenance and
mutation coordinator:

```python
import story_writing_agents as swa
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_storage,
)

workspace = swa.Workspace("/path/to/initialized-story")
services = compose_sqlite_conversation_storage(workspace)
memory = swa.ConversationMemoryService(
    swa.ConversationSearchScope("novel"),
    conversation_storage_services=services,
)
result = memory.search(swa.ConversationSearchQuery("council vessel"))
```

The composed bundle exposes a raw `SQLiteConversationSearch`, whose
`search(scope, query)` port requires an explicit scope. Wrapping that raw port
through `conversation_storage_services` creates the authorized one-argument
memory service used above while preserving the bundle's catalog, event-store,
search, and coordinator provenance. Direct composition does not change desired
or active shell state. See the
[preflight and provisioning example](package-api.md#optional-sqlite-conversation-storage)
before composition, or the
[next-shell workflow](cli.md#conversation-storage-selection) for installed
selection.

## Search contract

`ConversationSearchQuery(text, limit=10, include_archived=False)` is literal,
not fuzzy, regex, or semantic. Text is strict UTF-8, nonblank, at most 200
characters before and after normalization, and contains no Unicode category-C
characters. Comparison applies NFKC, case folding, trim, and whitespace
collapse. Python callers may explicitly include archived records; the shell
does not expose that option.

These pure literal mechanics are shared rather than adapter-specific. The
supported direct module `story_writing_agents.conversation_literal_search`
owns versioned normalization, bounded query normalization, subject-tier
classification, deterministic snippet construction, and result sorting. Its
six-name surface is neither a package-root nor conversation-storage facade
export and performs no I/O. See the exact
[direct imports](package-api.md#conversation-literal-search-mechanics).

Each conversation contributes at most one hit, using its first strongest
source:

| Rank | Match kind | Source |
| ---: | --- | --- |
| 1 | `exact_subject` | Whole normalized subject |
| 2 | `subject_prefix` | Start of normalized subject |
| 3 | `subject_substring` | Anywhere in normalized subject |
| 4 | `transcript` | First matching user message or agent reply |

Subject matches skip transcript reads; runtime-error events consume scan work
but never match. The reference adapter scans conversations by creation time
then ID and events by sequence. Results sort by rank, normalized subject, then
conversation ID. Snippets are normalized bounded context, not verbatim text.

A subject citation contains session and conversation IDs. A transcript
citation adds one atomic `turn_id`, one-based `sequence`, and `event_kind`
trio. Renaming can age displayed hit metadata but not citation identity.
`scan_truncated` identifies scan exhaustion; `truncated` also includes the hit
limit. Counts describe only the bounded scan.

## Transcript-page contract

```python
read_transcript(conversation_id, *, start_sequence=1, end_sequence=None,
                turn_id=None, max_events=24, citation=None)
continue_transcript(cursor)
```

Ranges are inclusive. An omitted end snapshots the current last sequence.
Events remain chronological and event-atomic, including runtime errors. A page
stops before its event or 32,768-content-byte cap. An oversized first event,
malformed range, stale reference, or start beyond one past the snapshot fails.
Starting exactly one past the end returns an empty page.

Python continuation preserves the request and snapshot; any append makes that
cursor stale. The shell instead performs a new `read_transcript()` for every
command, so its continuation text is an explicit next request, not retained
state.

## Fixed limits and failures

| Area | Default | Maximum |
| --- | ---: | ---: |
| Returned search hits | 10 | 50 |
| One scan | — | 256 conversations; 10,000 events |
| Snippet | — | 240 characters; 1,024 UTF-8 bytes |
| Transcript page | 24 events | 100 events; 32,768 content bytes |

Wrong types raise `TypeError`; invalid values and stale pages raise
`ValueError`; unsafe IDs raise `SessionIdError`; missing conversations raise
`ConversationNotFoundError`; storage corruption or containment failures use
`WorkspaceFormatError` or `WorkspaceSecurityError`. Shell mappings belong to
[Recovery](../operations/recovery-and-limitations.md).

Operations are read-only and provider-free. The reference adapter is
provider-neutral and index-free; neither implementation adds RAG or changes
citations. `SQLiteConversationSearch` owns one main-database read transaction
for its complete search. It examines the same bounded 256-conversation,
10,000-event domain and derives exact normalization, subject precedence,
first-visible-event matches, snippets, counters, flags, and ordering from
authoritative rows.

SQLite may consult a private disposable FTS5 companion for event candidate
hints while that main snapshot is open. Hints never establish or exclude a
match, affect scan counters, or change order. Missing, stale, false, busy,
malformed, unsafe, runtime-incompatible, or unavailable hints fall back to the
same complete authoritative literal scan. Search performs no companion repair,
and FTS availability is not SQLite storage readiness. The
[catalog](conversation-catalog.md) owns identity;
[transcripts](conversation-transcripts.md) own event meaning.
