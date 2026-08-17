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

`session_lifecycle` owns immutable provider-neutral lifecycle facts, legacy
projections, and readiness, with no resource or I/O. `AgentBackend` retains
manifests, lease claims, prompt staging, routes, processes, and threads;
`BoundAgent` owns one binding. Codex is implemented; Gemini fails preflight.

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
| `InteractiveMainLineEditor` | Call-local POSIX main-input editing, suggestions, fallback, and restoration |
| `InteractiveTerminal` | Typed input facade and safe primitive presentation |
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

The controller gives one filesystem event store to the retained runtime and
builds each attachment's memory service over that store and resolved catalog.

On switch, destination memory is built after validation and becomes current
only with committed attachment. End, abort, and cleanup clear it. Conversation
changes reuse it; later reads observe current data in the same session scope.

For advisory work the controller supplies the exact service to runtime. Runtime
alone calls search/read; backend sees only the handler and framed observations.
Direct `/search` and `/transcript` stay local. Controller calls the event store
only through `read(...)` for `/history` and `/status`; it never appends, calls
`read_context`, or calls a bound agent.

## Dependency direction

The facade depends downward on canonical `session_lifecycle` contracts;
`models` compatibility-forwards the exact legacy objects. Backend still reaches
them through `models`. Controller, terminal, storage, and transition ownership
are unchanged; no new shell copy/navigation exists.

The CLI injects the editor's main-read callback before backend/controller
construction. The editor uses command metadata and terminal sanitization.
Controller receives completed actions and depends downward on runtime and
presentation/storage. Retrieval's handler port prevents backend-to-orchestrator
access, keeping the graph acyclic.

This supports deterministic collaborators, not a dynamic plugin system. Exact
imports live in [Package API](../reference/package-api.md), lifecycle meaning in
[Session lifecycle](session-lifecycle.md), and conversation contracts in
[Conversation search](../reference/conversation-search.md),
[Conversation transcripts](../reference/conversation-transcripts.md), and
[Bounded retrieval](../reference/conversation-retrieval.md).
