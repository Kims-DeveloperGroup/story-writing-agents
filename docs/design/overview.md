# System design overview

> **Availability:** this page describes the implemented runtime, Codex backend,
> filesystem adapters, and installed session/conversation shell.

Key terms: **orchestration**—coordination of provider work; **controller**—the
owner of interactive transition order; **adapter**—a concrete implementation
of a provider-neutral contract; **selection**—a saved pointer, not the record
it names.

`story-writing-agents` separates story workflow from provider invocation and
filesystem persistence. In this project, “commit” means writing approved text
to workspace artifacts; the runtime never invokes Git.

`StoryRuntime` owns workflow validation, sequencing, concurrency, and reply
validation. For debates it also owns reservation, consensus, the public
blackboard, owner-isolated history, and final artifact coordination. For one
advisory turn it owns preflight, one per-conversation lock, the event-store
collaborator, and the user-before-provider/outcome sequence. Exact operation
rules belong to the [Runtime API](../reference/runtime-api.md#one-conversation-turn).

`AgentBackend` owns provider routes, Codex executable preflight, prompt staging,
team-session leases, and resumable provider thread metadata. A `BoundAgent`
owns one immutable definition and its vendor-specific invocation path. Codex is
implemented; Gemini is a known configuration name that fails preflight.

`Workspace` is the single local persistence and path-safety boundary. It owns
strict UTF-8 reads, JSON/JSONL artifacts, reservations, manuscripts,
inspection, and approved-artifact coordination. Exact mutation guarantees
belong to [Filesystem safety](../operations/filesystem-safety.md).

## Fixed collaboration model

The lead orchestrator moderates, synthesizes, and finalizes but does not vote.
The plot architect, character custodian, and execution specialist independently
audit every current revision. Consensus cannot be declared until all three
return valid approvals for that same revision.

The blackboard is public to the team and append-only. It contains user input,
candidates, verdicts, critiques, proposals, visible errors, and commit
metadata. Owner-isolated agent history is a separate persistence channel: an
agent receives only its own concise outcomes across the story session. The
runtime writes peer outputs in stable roster order even when provider calls
finish in another order.

## Conversation responsibilities

| Owner | Responsibility |
| --- | --- |
| `interactive_commands` | Pure command grammar plus canonical agent-route IDs, aliases, and advisory domains |
| `interactive_controller` | Attachment, selection, sticky target, synchronous dispatch, transition order, and backend cleanup coordination |
| `cli_context` | Saved story-session selection and lazy per-session conversation selections |
| `ConversationCatalog` | Conversation identity, display subject, archive status, and ordered metadata reads |
| `InteractiveTerminal` | Synchronous input and safe roster, status, error, and result presentation |
| `AgentBackend` | Session manifests, leases, provider routes, threads, and provider resources |
| `ConversationEventStore` | Transcript append, authoritative read, and bounded complete-turn context |
| `StoryRuntime` | Debate orchestration; advisory context, provider, and transcript sequencing |

The controller constructs one retained `StoryRuntime` with an event store bound
to the same catalog, but calls only `StoryRuntime.converse()` for accepted
content. It never reads context, appends events, invokes a bound agent, or
reparses a result itself. `StoryRuntime` depends on the event-store protocol,
not selection files; the read-only memory service consumes catalog and event
store contracts without owning either. See [Conversation catalog](../reference/conversation-catalog.md),
[Conversation transcripts](../reference/conversation-transcripts.md), and
[Conversation search](../reference/conversation-search.md).

## Dependency direction

The CLI creates the terminal, backend, and controller. The controller depends
downward on backend, selection, catalog, parser, terminal, runtime, models, and
workspace contracts; neither parser nor terminal depends back on it.
Orchestration depends on provider-neutral models, conversation-event contracts,
and workspace operations. The production import graph remains acyclic.

This separation allows deterministic test backends that satisfy the same
duck-typed binding and invocation shape, but it is not a dynamically loaded
plugin system. Exact public imports live in [Package API](../reference/package-api.md);
the remaining design pages explain provider, session, consensus, and
persistence dataflows without repeating signatures.
