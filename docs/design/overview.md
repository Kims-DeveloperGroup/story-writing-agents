# System design overview

> **Availability:** this page describes the implemented asynchronous runtime,
> concrete Codex backend, and Python-only conversation persistence and memory
> foundations.

Key terms: **orchestration**—coordination of debates and advisory turns;
**backend**—the provider/session coordinator; **adapter**—a concrete
implementation of a provider-neutral contract; **event store**—the adapter
boundary for visible conversation events.

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

Storage-neutral protocols separate conversation identity and discovery
metadata from visible transcript events. The catalog owns create, get, list,
rename, archive, and unarchive. The event store owns append, authoritative read,
and bounded complete-turn context. Filesystem adapters use `Workspace` for
fixed, safe paths.

The catalog, event-store, and memory modules remain provider-neutral.
`StoryRuntime` depends on the event-store protocol to persist an explicitly
addressed advisory turn; it does not depend on the catalog or memory service
and does not own discovery or selection. The memory service reads the catalog
and event store to provide bounded literal search, stable citations, and
event-atomic pages without a stored index. See [Conversation catalog](../reference/conversation-catalog.md),
[Conversation transcripts](../reference/conversation-transcripts.md), and
[Conversation search](../reference/conversation-search.md).

## Dependency direction

The CLI translates arguments and workspace selection into public runtime or
backend calls. Orchestration depends on provider-neutral models, conversation
event contracts, and workspace operations. The concrete backend consumes the
models and persists provider metadata through the workspace. Provider output
does not bypass runtime reply validation or write conversation/story content
directly.

This separation allows deterministic test backends that satisfy the same
duck-typed binding and invocation shape, but it is not a dynamically loaded
plugin system. Exact public imports live in [Package API](../reference/package-api.md);
the remaining design pages explain provider, session, consensus, and
persistence dataflows without repeating signatures.
