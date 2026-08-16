# System design overview

> **Availability:** this page describes implemented runtime ownership,
> dependency direction, filesystem adapters, and the installed interactive
> controller.

Key terms: **orchestration**—coordination of provider work;
**controller**—owner of interactive transition order; **adapter**—a concrete
provider-neutral implementation; **scope**—the session one service may read;
**selection**—a saved pointer, not the record it names.

`story-writing-agents` separates workflow, provider invocation, terminal
presentation, and filesystem persistence. In this project, “commit” means
writing approved story text; the runtime never invokes Git.

## Runtime and provider ownership

`StoryRuntime` owns workflow validation, sequencing, concurrency, and reply
validation. For debates it also owns reservation, consensus, blackboard,
owner-isolated history, and artifacts. For advisory turns it owns conversation
locking, context and transcript order, retrieval scope/actions/budgets, and the
final reply or safe error. See the [Runtime API](../reference/runtime-api.md#one-conversation-turn).

`AgentBackend` owns provider routes, Codex preflight, prompt staging,
story-session leases, provider processes, and resumable thread metadata. A
`BoundAgent` owns one immutable definition and invocation path. Codex is
implemented; Gemini remains a reserved name that fails preflight.

`Workspace` is the local persistence and path-safety boundary. It owns strict
UTF-8 reads, JSON/JSONL artifacts, reservations, manuscripts, inspection, and
atomic file replacement. Exact guarantees belong to
[Filesystem safety](../operations/filesystem-safety.md).

## Fixed collaboration model

The lead orchestrator moderates, synthesizes, and finalizes but does not vote.
The plot architect, character custodian, and execution specialist audit every
current revision. Consensus requires all three approvals for that revision.
The public blackboard is append-only; owner-isolated agent history is a
separate channel. Peer outputs are persisted in stable roster order even when
provider calls finish in another order.

## Interactive ownership

| Owner | Responsibility |
| --- | --- |
| `interactive_commands` | Pure grammar and canonical agent-route metadata |
| `interactive_controller` | Attachment, selection, sticky target, dispatch, transition order, and cleanup coordination |
| `cli_context` | Saved session and per-session conversation selections |
| `ConversationCatalog` | Conversation identity, subject, archive status, and ordered metadata |
| `ConversationMemoryService` | One attachment-scoped search/read authorization |
| `conversation_retrieval` | Immutable actions, observations, policy, and runtime/backend handler port |
| `ConversationEventStore` | Authoritative events and complete-turn context |
| `InteractiveTerminal` | Synchronous input and safe primitive presentation |
| `StoryRuntime` | Debate/advisory orchestration and retrieval authority |
| `AgentBackend` | Sessions, threads, provider processes, and retrieval call loop |

```mermaid
flowchart LR
    accTitle: Interactive conversation ownership
    accDescr: The controller passes its attached-session memory service to the runtime, which alone authorizes provider-requested reads.
    C[Interactive controller] --> R[Retained StoryRuntime]
    C --> M[Attachment-scoped memory service]
    C --> K[Conversation catalog]
    R -->|authorized search and read| M
    R --> B[Agent backend]
    R --> E[One shared event store]
    M --> E
    M --> K
```

The controller constructs one filesystem event store against its resolved
catalog and gives that exact instance to the retained runtime. For each
attachment it constructs a `ConversationMemoryService` over the same catalog
and event store.

On session switch, the destination service is built after destination
validation but before source detach. It becomes current only when the
destination attachment commits. End, abort, and final cleanup clear it.
Conversation create, use, rename, and archive do not replace it; their
consequence is simply that later reads observe current catalog and transcript
data in the same attached-session scope.

For advisory content the controller passes its exact memory service to
`StoryRuntime.converse_with_retrieval()`. Runtime alone calls its search/read
methods; backend receives only the handler protocol and framed observations.
Direct `/search` and `/transcript` remain controller-to-service local reads.
The controller's sole direct event-store call is public
`ConversationEventStore.read(...)`, used by `/history` and the transcript portion
of `/status`; it never appends, calls `read_context`, or calls a bound agent.

## Dependency direction

The CLI creates terminal, backend, and controller. Controller depends downward
on runtime and presentation/storage contracts. Runtime and backend both depend
on provider-neutral retrieval contracts; only runtime depends on memory search.
The handler port prevents backend-to-orchestrator access, keeping the graph
acyclic.

This supports deterministic test collaborators but is not a dynamically
loaded plugin system. Exact public imports live in
[Package API](../reference/package-api.md); conversation contracts live in
[Conversation search](../reference/conversation-search.md),
[Conversation transcripts](../reference/conversation-transcripts.md), and
[Bounded retrieval](../reference/conversation-retrieval.md).
