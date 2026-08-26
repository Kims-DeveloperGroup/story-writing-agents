# Bounded conversation retrieval

> **Availability:** focused shell conversations use bounded retrieval. Python
> callers opt in; `converse()` remains one-step. Python callers may also inject
> the optional conversation-RAG runtime service and force a preflight query.
> Inline canonical references in either form are captured by `StoryRuntime` and
> enforced through the optional `BoundAgent` reference coordinator before the
> unchanged retrieval loop. Noninteractive `story rag search` exposes the same
> bounded authorized excerpt/citation boundary without an agent. `/settings`,
> automatic shell wiring, and `/rag QUERY` remain deferred.

Key terms: **retrieval turn**—one message, action loop, and answer; **action**—a
correlated provider request; **observation**—authorized untrusted story data;
**source**—a subject and stable citation; **scope**—the readable story session.

## Public entry points and values

```python
await StoryRuntime.converse_with_retrieval(session_id, conversation_id,
    agent_id, text, *, memory_service, progress_sink=None,
    rag_query=None) -> ConversationRetrievalResult
await RuntimeSession.converse_with_retrieval(conversation_id, agent_id, text,
    *, memory_service, progress_sink=None,
    rag_query=None) -> ConversationRetrievalResult
await BoundAgent.converse_with_retrieval(request, handler, *,
    reference_read: ReferenceReadInvocation | None = None) -> ConversationRetrievalReply
handler.handle_conversation_retrieval_action(action) -> ConversationRetrievalObservation | ConversationRetrievalReply
ConversationMemoryService.scope -> ConversationSearchScope
serialize_conversation_retrieval_observation(observation) -> str  # module only

ConversationRetrievalRequest(*, session_id, conversation_id, turn_id, agent_id, content, context)
ConversationRetrievalReply(*, session_id, conversation_id, turn_id, content, sources)
ConversationRetrievalResult(*, session_id, conversation_id, turn_id, agent_id, content, context, sources)
ConversationRetrievalSource(*, subject, citation)
ConversationRagAttemptOutcome(*, outcome, reason)
ConversationRagExcerpt(*, citation_id, source, reference, content)
ConversationRagContext(*, outcome, excerpts, retained_utf8_bytes, truncated)
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
`content`, `context`, and `retrieval`, plus `rag_context` only after an attempted
RAG preflight. Continuations omit content, context, and RAG context and add one
`observation`. Retrieval control has index, allowed actions, policy, and rules.
Without references or an attempted RAG preflight, established payloads stay
unchanged. With an internal reference coordinator, one separate content-free
prelude runs first and `untrusted_writing_artifact_context` appears only on this
initial retrieval payload, never on a continuation.

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

<a id="optional-conversation-rag-preflight"></a>

## Optional conversation RAG preflight

`StoryRuntime` accepts an application-composed
`ConversationRagRuntimeService`. Its default automatic policy is off. A
non-`None` `rag_query` forces exactly one preflight and overrides automatic
query derivation. With no configured service, a forced query yields an empty
`literal_fallback`/`disabled` context; with no query, no attempt occurs and both
the request's `rag_context` and result's `rag_outcome` remain `None`.

An attempted context has the exact top-level fields `outcome`, `reason`,
`excerpts`, `retained_utf8_bytes`, and `truncated`. Outcomes are `complete`,
`partial`, or `literal_fallback`; fallback reasons are `disabled`,
`unavailable`, `stale`, `corrupt`, `incompatible`, or `failed`. Excerpts use
contiguous `g1` through `g20` citation IDs and carry an authoritative `source`,
stable `reference`, and reread `content`. They are explicitly untrusted story
data, not instructions. The agent may cite those IDs in the same answer as
later `s...` or `r...` sources; the runtime resolves all of them through its
existing supplied-source authorization map.

Only active conversations and complete visible user/reply pairs participate.
The runtime merges literal candidates first, accepts at most one semantic
excerpt per current projected document, rejects provider text, and rereads the
complete current authoritative target before retaining any range. A context
holds at most 20 excerpts, each at most 800 codepoints, and at most 32,768 UTF-8
bytes. Candidate omission or stale identities/ranges makes the outcome partial
or fallback. Rejected semantic identities or ranges also mark derived state
stale best-effort. Citation ranges map to the earliest intersected subject,
user, or reply interval.

`rag_context` is present only on the initial substantive retrieval payload;
continuations retain the RAG-specific citation/trust rules but repeat no RAG
content. When canonical writing-artifact references and RAG are both present,
the separate reference prelude remains first, and the initial substantive
payload carries both independently authorized contexts. Neither context appears
on a continuation. The preflight itself adds no story-agent provider call.

After a successful reply append, a configured runtime projects all current
active complete visible turns under schema
`story-conversation-rag-visible-turn-v1`. Ordinary projection failures cannot
alter the committed reply; process-control exceptions propagate after commit.
The fixed projection, state-port, and composition contracts are in
[Package API](package-api.md#optional-conversation-rag-boundary).

| Retrieval-loop limit | Exact maximum |
| --- | ---: |
| Nonterminal actions / retrieval provider calls | 4 / 5 |
| Search hits per action / total | 10 / 20 |
| Transcript turns / visible events | 12 / 24 |
| Cumulative framed observation bytes | 32,768 UTF-8 bytes |

Retrieval action indices and the loop budget remain exactly `1..5`. Without
references, a turn makes one to five physical provider calls. A referenced
turn adds one prelude outside the loop and therefore makes two to six physical
calls. There is no sixth retrieval action or retrieval-call grace; only that
separate prelude can be the physical sixth call. No clipping, automatic retry,
or whole-turn deadline exists.

## Short sequence

When an internal reference coordinator is supplied, a content-free prelude
first requires the complete ordered reference action. It has no retrieval
index. The retrieval loop then remains:

1. Retrieval call one receives the question, focused context, and—only for a
   referenced turn—the transient untrusted writing-artifact context.
2. The agent searches active same-session conversations; it receives `s1h1`.
3. It reads through `s1h1`, receiving pair `r2e1`, `r2e2`.
4. It answers with supplied IDs; sources retain subject and citation.

A direct answer stops at retrieval call one without memory or sources. It is
physical provider call one without references and call two after a reference
prelude.

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
automatic retry. Failure to store an outcome or this process-control path
leaves the user unmatched and excluded from later context. Incomplete
failed/cancelled history is tainted when required, so the next compatible call
starts fresh. A completed answer stays untainted if reply storage fails.

For a referenced backend call, any prelude, provider, handler, protocol,
acceptance, or cancellation failure also terminates the one-use reference
coordinator. Once the provider sequence has advanced, reference handling
best-effort taints the compatible thread without replacing the primary failure.
There is no automatic retry; a caller-directed retry starts a new top-level
operation with a fresh capture, correlation, coordinator, capability, and
ordinal sequence. Retrieval action indices restart independently at `1`; they
are never used as reference invocation ordinals.

Apart from the separate best-effort diagnostic log and content-free reference
evidence, retrieval-domain mutations remain limited to transcript events and
provider metadata.
