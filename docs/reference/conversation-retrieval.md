# Bounded conversation-retrieval contracts

The retrieval values, policy, serializer, memory service, and `BoundAgent`
adapter seam remain available for direct component composition. The public
`StoryRuntime`/`RuntimeSession` retrieval entry points and interactive consumer
are not available in this integration stage.

## Retained low-level entry

```python
await BoundAgent.converse_with_retrieval(
    request: ConversationRetrievalRequest,
    handler: ConversationRetrievalActionHandler,
    *,
    reference_read: ReferenceReadInvocation | None = None,
) -> ConversationRetrievalReply
```

The direct `conversation_retrieval` module also defines frozen request, action,
observation, source, reply/result, progress, policy, and serialization values.
These contracts do not create or focus a conversation, persist a user/reply
turn, or publish an artifact by themselves. The caller owns the correlated
handler and any storage transaction around the low-level call.

## Action loop

Every accepted action carries correlated session/conversation/turn IDs and a
sequential action index. A structured repair is another physical provider call
for the same action index; only an accepted action advances the index.

| Action | Additional fields | Rule |
| --- | --- | --- |
| `search_conversations` | Query | First, or again before the first read. |
| `read_history` | Citation and sequence range | Uses a hit from this same logical turn. |
| `answer` | Content and ordered unique citation IDs | Terminal. |

After the first read, only another read or answer is accepted. The complete
turn permits at most five physical provider calls, including structured repair
calls. When only one physical call remains, it must answer; rejection on that
final call returns the safe local fallback without a sixth call. Search is
active-only; a read is bounded to the cited source and returns structured
`ConversationRetrievalHistoryRecord` values. Every closed field receives a
deterministic citation ID such as `r2e1f1`. Private event payload, prompts,
credentials, private provider state, and arbitrary files are excluded.

Observations are framed as canonical JSON between fixed untrusted-story-data
markers. They are story data, never instructions. Fixed budgets bound physical
provider calls, queries, hits, pages, events, bytes, and citations; exact values
are defined by `ConversationRetrievalPolicy`.

## Provider-call input and output

The first low-level call receives the current request once. Its JSON object has
this shape (nested contracts are abbreviated here):

```json
{
  "operation": "conversation_retrieval",
  "session_id": "novel",
  "conversation_id": "c_123",
  "turn_id": "turn_7",
  "agent_id": "lead_orchestrator",
  "content": "What did we decide about the opening?",
  "context": {
    "bootstrap": {
      "schema_version": 2,
      "session_id": "novel",
      "conversation_id": "c_123",
      "total_records": 0,
      "last_sequence": 0,
      "tail_batch_digest": null
    },
    "records": [],
    "requested_records": 0,
    "retained_records": 0,
    "omitted_records": 0,
    "retained_utf8_bytes": 0,
    "truncated": false
  },
  "retrieval": {
    "action_index": 1,
    "allowed_actions": ["search_conversations", "answer"],
    "policy": {"...": "fixed bounds"},
    "rules": ["..."]
  }
}
```

An invocation may use `story_session_context` instead of `context`, and may
include an authorized `rag_context`. A search action returns an untrusted
observation with `s1`, `s2`, ... citation IDs. A later `read_history` action
returns canonical record identity and `rAeRfF` field-citation IDs. Continuation
calls deliberately omit `content` and initial context; they carry only the
correlation fields, updated retrieval control, and one framed observation. A
repair repeats the current `action_index` and adds the repair instruction.
After a read, `allowed_actions` contains only `read_history` and `answer`. The
payload and schema permit only `answer` when the next attempt would consume the
last physical call, including when that last attempt is a repair.

This provider sequence is a direct `BoundAgent` component API. The installed
lead workflow does not call it, and provider-free `/search`, `/transcript`, and
`/history` never construct this payload.

## Optional local RAG

`ConversationRagRuntimeService.search_authorized()` can perform a direct
bounded local search against a complete `ConversationStorageServices` bundle.
It reauthorizes stable derived references and rereads current authoritative
ranges. This is independent of `BoundAgent` and does not call a story provider.

The earlier runtime-level forced/automatic preflight consumer is absent. The
shell has no one-turn RAG arming command. See
[Optional conversation RAG](../guides/conversation-rag.md).

## References and progress

`reference_read` remains a generic low-level hook. If supplied by a direct
integrator, every addressed invocation must complete the ordered authorized
reference read before its answer is accepted. Focused shell Lead references use
their own mandatory-reference operation rather than this generic retrieval hook.
If structured repair exhaustion produces the visible local fallback instead,
the reference invocation and operation terminate with `provider_failure` and
record neither `result_received` nor `accepted`.

Retrieval progress values remain synchronous, content-free observation
contracts. A caller can render or store them. The shell's Lead-reference
progress belongs to the separate mandatory-reference path, not this optional
retrieval operation.

## User impact

Users can still run provider-free `/search`, `/transcript`, and `/history` over
the selected conversation store. They cannot ask an agent to search/read/answer
through the installed shell or runtime management facade. Ordinary or routed
input is rejected before a request, provider call, event append, or citation is
created.

See [Conversation search](conversation-search.md),
[schema-v2 collaboration history](collaboration-history.md), and the
[runtime management API](runtime-api.md).
