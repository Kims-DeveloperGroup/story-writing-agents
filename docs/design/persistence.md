# Persistence design

> **Availability:** filesystem persistence for configuration, sessions,
> debates, agent memory, conversation metadata, and manuscripts is implemented.

Key terms: **blackboard**—the public debate log; **owner-isolated state**—
history visible only to its agent; **lifecycle**—reservation and terminal status;
**durability**—which completed file changes survive interruption.

The workspace intentionally uses inspectable files instead of a database. Each
channel has one owner and audience:

```mermaid
flowchart LR
    U[User input and candidates] --> B[Public debate JSONL]
    B --> I[Inspection and all agents]
    O[Concise provider outcomes] --> A[Per-agent JSONL]
    A --> E[Only the owning agent]
    L[Lifecycle and terminal result JSON] --> I
    P[Provider manifest and lease JSON] --> K[AgentBackend]
    C[Conversation metadata JSON] --> G[Catalog adapter]
    F[Approved candidate] --> M[Immutable manuscript]
```

In words: the public blackboard is the complete visible debate record;
per-agent state is a narrower owner-only memory channel; lifecycle/result files
support recovery and inspection; provider state belongs to the backend;
conversation metadata belongs to its adapter; only approved content reaches a
manuscript.

## What is retained

The blackboard keeps original input, candidates, verdict notes, critiques,
proposals, visible errors, finalization metadata, and commit record. Terminal
result JSON omits its duplicated message list; inspection combines it with
validated JSONL and lifecycle state. Agent state deliberately avoids copying
the entire prompt, candidate, or core documents, but keeps generic outcomes and
the owner's visible notes, critiques, and proposals, capped per entry. A
provider may still echo sensitive material into those fields.

Session override JSON survives runtime instances. Provider manifests preserve
compatible thread IDs across debates, detach, end, and backend close. CLI
selection survives session end. Conversation metadata retains its exact
display subject even when archived. None of these actions is deletion or
secure erasure.

## Ownership and durability boundaries

The workspace validates and mutates one channel at a time. Debate reservation
belongs to lifecycle state; blackboard and owner histories append separately;
terminal result is a projection rather than a second message store. Approved
artifact coordination crosses core documents and one manuscript, while a
conversation metadata transition is isolated to that identity. The catalog
performs no migration and an absent catalog read creates nothing.

Because these channels are separate files, persistence design does not imply a
database transaction across them. The normative containment, exclusive-create,
atomic-file, write-order, rollback, and interruption guarantees live in
[Filesystem safety](../operations/filesystem-safety.md); recovery behavior lives
in [Recovery and limitations](../operations/recovery-and-limitations.md).

The complete path tree and strict schemas live in
[Session storage](../reference/configuration/session-storage.md). Filesystem
trust boundaries are in [Filesystem safety](../operations/filesystem-safety.md).
