# Conversation search and bounded transcript reads

> **Availability:** the installed shell exposes active-only `/search` and fresh
> `/transcript` pages after story-session attachment. The same provider-free
> capabilities are package-supported Python APIs.

Key terms: **scope**—one authorized story session; **hit**—the strongest match
from one conversation; **citation**—stable identity plus an optional exact
event reference; **snapshot**—the transcript end observed for one read;
**cursor**—Python continuation data for that unchanged snapshot.

## Shell bridge

Neither command needs a selected conversation. `/search` constructs the
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

Search/read never changes selection, routing, provider state, or durable data.
See the [parser](interactive-command-parser.md) for grammar, the
[terminal](interactive-terminal.md) for records, and
[Story sessions](../guides/story-sessions.md) for the beginner flow.

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

## Search contract

`ConversationSearchQuery(text, limit=10, include_archived=False)` is literal,
not fuzzy, regex, or semantic. Text is strict UTF-8, nonblank, at most 200
characters before and after normalization, and contains no Unicode category-C
characters. Comparison applies NFKC, case folding, trim, and whitespace
collapse. Python callers may explicitly include archived records; the shell
does not expose that option.

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

Operations are read-only, provider-free, and index-free: no RAG or persisted
search index. The [catalog](conversation-catalog.md) owns identity;
[transcripts](conversation-transcripts.md) own event meaning.
