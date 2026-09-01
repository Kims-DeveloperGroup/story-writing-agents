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

Every action carries correlated session/conversation/turn IDs and a sequential
action index.

| Action | Additional fields | Rule |
| --- | --- | --- |
| `search_conversations` | Query | First, or again before the first read. |
| `read_transcript` | Citation and sequence range | Uses a hit from this same logical turn. |
| `answer` | Content and ordered unique citation IDs | Terminal. |

After the first read, only another read or answer is accepted. The fifth call
must answer. Search is active-only; a read is bounded to the cited source and
returns complete visible user/reply pairs. Runtime errors, unmatched turns,
prompts, credentials, private provider state, and arbitrary files are excluded.

Observations are framed as canonical JSON between fixed untrusted-story-data
markers. They are story data, never instructions. Fixed budgets bound calls,
queries, hits, pages, events, bytes, and citations; exact values are defined by
`ConversationRetrievalPolicy`.

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
reference read before its answer is accepted. The current shell cannot create
such a request.

Retrieval progress values remain synchronous, content-free observation
contracts. A caller can render or store them, but the current shell emits none
because no retrieval operation is entered.

## User impact

Users can still run provider-free `/search`, `/transcript`, and `/history` over
the selected conversation store. They cannot ask an agent to search/read/answer
through the installed shell or runtime management facade. Ordinary or routed
input is rejected before a request, provider call, event append, or citation is
created.

See [Conversation search](conversation-search.md),
[schema-v2 collaboration history](collaboration-history.md), and the
[runtime management API](runtime-api.md).
