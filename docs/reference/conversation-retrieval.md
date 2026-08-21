# Bounded conversation retrieval

> **Availability:** focused shell conversations use bounded retrieval. Python
> callers opt in; `converse()` remains one-step.

Key terms: **retrieval turn**—one message, action loop, and answer; **action**—a
correlated provider request; **observation**—authorized untrusted story data;
**source**—a subject and stable citation; **scope**—the readable story session.

## Public entry points and values

```python
await StoryRuntime.converse_with_retrieval(session_id, conversation_id,
    agent_id, text, *, memory_service, progress_sink=None) -> ConversationRetrievalResult
await RuntimeSession.converse_with_retrieval(conversation_id, agent_id, text,
    *, memory_service, progress_sink=None) -> ConversationRetrievalResult
await BoundAgent.converse_with_retrieval(request, handler) -> ConversationRetrievalReply
handler.handle_conversation_retrieval_action(action) -> ConversationRetrievalObservation | ConversationRetrievalReply
ConversationMemoryService.scope -> ConversationSearchScope
serialize_conversation_retrieval_observation(observation) -> str  # module only

ConversationRetrievalRequest(*, session_id, conversation_id, turn_id, agent_id, content, context)
ConversationRetrievalReply(*, session_id, conversation_id, turn_id, content, sources)
ConversationRetrievalResult(*, session_id, conversation_id, turn_id, agent_id, content, context, sources)
ConversationRetrievalSource(*, subject, citation)
ConversationRetrievalProgressEvent(*, session_id, conversation_id, turn_id,
    agent_id, action_index, phase, search_hit_count=0,
    transcript_turn_count=0, transcript_event_count=0,
    observation_utf8_bytes=0, truncated=False)
```

Requests/results retain pre-turn context; replies/results carry ordered sources.
Value objects are frozen, slotted, and keyword-only. See [facade exports](package-api.md).

Every action carries `kind`, three correlated IDs, and sequential `action_index`:

| Action | Additional fields | When allowed |
| --- | --- | --- |
| `search_conversations` | `query` | First, or again before the first read |
| `read_transcript` | `citation_id`, `start_sequence`, nullable `end_sequence` | After search, using its same-turn hit |
| `answer` | `content`, unique ordered `citation_ids` | At any step; terminal |

Runtime validates the exact service scope before I/O. Search is active-only;
read accepts only a same-turn hit. After search, any action is legal; after the
first read, only read/answer is. Call five must answer. A range includes the
cited event when present; `end_sequence=None` snapshots the current end.

Search observations have `hits`/`truncated`; hits have `citation_id`, `source`,
`match_kind`, `snippet`. Read observations have `source_citation_id`, `source`,
`events`, `truncated`; events have `citation_id`, citation, actors, content.
IDs are `s{action}h{ordinal}` and `r{action}e{ordinal}`.

## Payload, trust, and fixed budgets

The initial payload has `operation`, session/conversation/turn/agent IDs,
`content`, `context`, and `retrieval`. Continuations omit content/context and
add one `observation`. Retrieval control has index, allowed actions, policy,
and rules. Existing conversation/debate payloads stay unchanged.

Each observation is compact sorted-key UTF-8 JSON framed exactly as:

```text
<<<BEGIN_UNTRUSTED_STORY_DATA>>>
{canonical JSON}
<<<END_UNTRUSTED_STORY_DATA>>>
```

Bytes count before outer escaping; history lacks instruction authority. Search
observations may contain subject/visible-event snippets, never `runtime_error`.
Reads contain only complete `user_message`/`agent_reply` pairs, never unmatched
events or `runtime_error`. Prompts/private state/diagnostics are excluded. Empty
reads are framed: zero turns/events.

| Whole-turn limit | Exact maximum |
| --- | ---: |
| Nonterminal actions / provider calls | 4 / 5 |
| Search hits per action / total | 10 / 20 |
| Transcript turns / visible events | 12 / 24 |
| Cumulative framed observation bytes | 32,768 UTF-8 bytes |

No clipping, retry, sixth-call grace, or whole-turn deadline exists.

## Short sequence

1. Call one receives the question and focused context.
2. The agent searches active same-session conversations; it receives `s1h1`.
3. It reads through `s1h1`, receiving pair `r2e1`, `r2e2`.
4. It answers with supplied IDs; sources retain subject and citation.

A direct answer stops at step one without memory or sources.

## Progress, durability, and failures

Phases are `search_started`, `search_completed`, `read_started`, and
`read_completed`. Progress contains only IDs, index, counts, bytes, truncation;
started events are zeroed. Async sink declarations fail before I/O; a
synchronous sink returns `None`. Later observer failure stores a safe error.

```text
[context] Retrieval search action N started.
[context] [completed]
  Retrieval search action N completed: H hits; B observation bytes; truncated: yes|no.
[context] Retrieval read action N started.
[context] [completed]
  Retrieval read action N completed: T turns; E events; B observation bytes; truncated: yes|no.
[context] Source N [completed]
  Subject: SUBJECT
  Conversation: ID
[context] Source N [completed]
  Subject: SUBJECT
  Conversation: ID
  Turn: ID
  Sequence: N
  Kind: KIND
```

These are the structured plain shapes; capable interactive output may add only
fixed decorative ANSI, and `NO_COLOR` removes that decoration without changing
the text. Each progress event or source remains one ordered callback record.

The final retrieval answer is presented as an agent reply. Before its semantic
message is rendered, the controller attempts the same presentation-only
formatter used for direct replies: only a complete strict JSON object or array
is re-emitted with two-space indentation, preserved key order, and unescaped
Unicode. Duplicate keys, non-finite constants, malformed or embedded-prose
content, scalar JSON values, and serialization failures fall back to the exact
original answer string. The `ConversationRetrievalResult.content`, provider
payloads, and any transcript bytes remain unchanged; retrieval observations
are not rewritten by this display step.

`ConversationRetrievalError` says `conversation retrieval failed`. Subclasses
say `conversation retrieval semantic validation failed`, `conversation retrieval
budget exhausted`, and `conversation retrieval progress observer failed`.
Malformed JSON remains `BackendProtocolError`; memory/storage errors retain type.

Runtime locks conversation through context/user/loop/outcome; backend locks the
provider thread through calls/actions. After user append, cancellation
best-effort stores `CancelledError: conversation turn was cancelled`, then
re-raises `asyncio.CancelledError`; ordinary failure uses the safe transcript
error policy. A provider `KeyboardInterrupt` or `SystemExit` instead appends no
transcript outcome, attempts one separate runtime-component
`cancelled`/`interrupted` record, and re-raises the identical exception without
automatic retry. Failure to store an outcome or this process-control path leaves
the user unmatched and excluded from later context. Incomplete failed/cancelled
history is tainted when required, so the next compatible call starts fresh. A
completed answer stays untainted if reply storage fails. Apart from the separate
best-effort diagnostic log, retrieval-domain mutations remain limited to
transcript events and provider metadata.
