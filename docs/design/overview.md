# System design overview

> **Availability:** this page describes the implemented asynchronous runtime,
> concrete Codex backend, and metadata-only conversation catalog.

Key terms: **orchestration**—coordination of the debate workflow; **backend**—
the provider/session coordinator; **blackboard**—the team's public append-only
history; **adapter**—a concrete implementation of a provider-neutral contract.

`story-writing-agents` separates story workflow from provider invocation and
filesystem persistence. In this project, “commit” means writing approved text
to workspace artifacts; the runtime never invokes Git.

`StoryRuntime` owns input validation, immutable debate reservation, phase
sequencing, concurrency, reply validation, consensus, and final artifact
coordination. It resolves current configuration and builds each role's agent
definition before a phase. It also controls the public blackboard and
owner-isolated history supplied to requests.

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

## Separate conversation foundation

The conversation catalog is alongside, not inside, debate orchestration. Its
storage-neutral protocol handles logical session and conversation IDs plus
immutable metadata values. The filesystem adapter uses `Workspace` for a
fixed, safe metadata path.

It deliberately has no dependency on providers or the consensus runtime.
Create, get, list, rename, archive, and unarchive affect metadata only.
Current-conversation selection, event storage, transcripts, indexing, search,
interactive dispatch, and RAG are not implemented. Subject text is a display
label, not identity; duplicate display and normalized values are allowed.

## Dependency direction

The CLI translates arguments and workspace selection into public runtime or
backend calls. Orchestration depends on provider-neutral models and workspace
operations. The concrete backend consumes those models and persists provider
metadata through the workspace. Provider output does not bypass runtime reply
validation or write manuscripts directly.

This separation allows deterministic test backends that satisfy the same
duck-typed binding and invocation shape, but it is not a dynamically loaded
plugin system. Exact public imports live in [Package API](../reference/package-api.md);
the remaining design pages explain provider, session, consensus, and
persistence dataflows without repeating signatures.
