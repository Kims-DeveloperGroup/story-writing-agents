# Prompts and provider boundary

> **Availability:** file-referenced definitions, Codex prompt staging,
> phase-specific invocation, and persistent compatible threads are implemented.

Key terms: **agent definition**—stable context for one role; **fingerprint**—a
hash-derived identity for that context; **binding**—the provider object created
for a definition; **staging**—assembling verified context in a temporary area.

Stable prompt context and dynamic turn data travel through different channels.
Before each phase, the runtime resolves the role prompt, persona, language, and
allowlisted core documents into one immutable definition. Each file reference
contains an absolute workspace path and SHA-256 digest, not the file text.

```mermaid
flowchart LR
    F[Role prompt + persona + language + core files] --> D[Content-addressed definition]
    D --> B[AgentBackend binding]
    B --> G[Isolated temporary AGENTS.md]
    Q[Dynamic AgentRequest JSON] --> C[Codex subprocess]
    G --> C
    C --> V[Schema and phase validation]
    V --> R[AgentReply]
```

In words: stable files are verified and staged during binding; request-scoped
JSON enters separately for each turn; provider output becomes a reply only
after schema and phase validation.

## Least-privilege context

Each role has one editable `config/agents/<agent-id>/AGENTS.md`. The plot
architect receives the global and part outlines; the character custodian gets
character sheets; the execution specialist gets narrative rules; the lead gets
all four. Definitions never include unrelated workspace paths.

The runtime verifies referenced bytes before and after a new bind. A definition
fingerprint covers agent ID, resolved provider and language, component
identities, and hashes. Bindings are cached for one runtime by agent and
fingerprint. Matching concurrent requests share one in-flight bind; cancelling
one waiter does not cancel a bind another still needs, and failed binds are not
cached.

Editing a role prompt, selected persona, language, or allowlisted core document
therefore changes the next applicable definition. The backend makes a new
binding and a changed fingerprint starts a fresh thread for that agent. An
identical binding may serve multiple story sessions, while provider conversation
state remains partitioned by session and agent.

## Codex process boundary

The backend launches one constrained Codex subprocess from the isolated
staging directory. Stable verified context reaches its temporary top-level
`AGENTS.md`, while request JSON reaches standard input; provider replies return
through schema and phase validation. The exact sandbox, approval, tool,
configuration, and instruction-discovery controls are documented once in
[Privacy and provider exposure](../operations/privacy-and-provider.md).

Dynamic request JSON contains task, original input, current candidate, public
blackboard snapshot, and only the receiving agent's persisted outcomes. It does
not repeat role prompts, persona text, language directives, or core-document
text. Phase-specific output schemas and runtime checks constrain draft, audit,
proposal, synthesis, and finalization replies.

This boundary does not make the remote model or installed executable trusted.
SHA-256 detects change but does not encrypt or authorize content. The provider
may retain its own transcripts, and session end keeps thread references.

Exact model shapes are in [Agent contracts](../reference/agent-contracts.md);
privacy implications are in [Privacy and provider exposure](../operations/privacy-and-provider.md).
