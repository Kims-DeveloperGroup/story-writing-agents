# Prompts and provider boundary

> **Availability:** file-referenced definitions, provider-specific prompt
> binding, advisory turns with optional bounded retrieval, context-aware
> debates, and compatible threads are implemented for Codex and Antigravity.

Key terms: **definition**—stable context for one role; **fingerprint**—its
hash-derived identity; **binding**—the provider object for a definition;
**visible context**—events explicitly sent in a request; **provider-private
history**—state behind a provider thread ID.

Stable prompt context and dynamic call data use different channels. A definition
references the role prompt, persona, language, and allowlisted core documents by
absolute path and SHA-256 digest. Verified file content is staged at binding;
request JSON enters separately for each call.

## Bound role context

The plot architect receives global and part outlines; the character custodian
gets character sheets; the execution specialist gets narrative rules; the lead
gets all four core documents. Definitions include no unrelated workspace paths.

The fingerprint covers agent, provider, language, component identities, and
hashes. Editing one component changes the next definition and starts a fresh
provider thread for that agent. Identical bindings are cached for one runtime;
provider conversation state is still partitioned by story session and agent.

<a id="conversation-turn-flow"></a>

## Advisory context

The shell passes its focused identity, route, input, and current-session
memory service to `StoryRuntime.converse_with_retrieval()`. Runtime owns context,
memory authorization, and transcript order. Backend holds one provider-thread
lock across the action loop and calls only the synchronous runtime handler; it
never receives a search, catalog, transcript, or filesystem collaborator.

The first provider call receives current input and bounded focused context.
Later calls receive only runtime-authorized observations framed as untrusted
story data. Exact payloads and action order live in
[Bounded retrieval](../reference/conversation-retrieval.md). Direct Python
`converse()` and its `ConversationRequest` payload remain one-step and unchanged.

Conversation IDs scope visible events, not provider-private history. One
session-and-agent thread can span subjects and debates. Direct routing chooses
a responder; it is not a private provider channel.

## Debate context field

An included debate adds a top-level `conversation_context` field to every agent
request. Drafting, audits, proposals, synthesis, and finalization all receive
the same captured event snapshot. The runtime does not reread or search the
transcript between phases. Fresh and context-omitted requests omit the field.

This context is not concatenated into the bound prompt, phase `task`, original
`user_payload`, current candidate, public blackboard, or owner state. There is
no debate-time retrieval, search result, or hidden memory injection. Exact request
fields are in [Agent contracts](../reference/agent-contracts.md#debate-request).

## Provider process boundaries

The backend launches a constrained Codex subprocess from isolated staging.
Verified stable context reaches its temporary top-level `AGENTS.md`; request
JSON reaches standard input. Replies pass schema, correlation, and phase checks.
The exact sandbox, approval, tool, configuration, and instruction controls live
in [Privacy and provider exposure](../operations/privacy-and-provider.md).

The Antigravity adapter binds the same verified definition and request models
to an isolated provider-state root. It launches `agy` without a shell, sends
story payloads through standard input using stream JSON, and requests streamed
JSON output. Runtime-owned JSON schema, provider-state path, optional
model/effort, and provider conversation ID remain process arguments. Slash
commands are disabled, plan mode is selected, and sandbox controls are applied. Exact
setup, precedence, preflight, and lifecycle contracts are in the
[Antigravity guide](../guides/antigravity.md).

Debate JSON contains task, input, candidate, blackboard, the receiving agent's
stored outcomes, and only when included, conversation context. It does not
repeat role prompts, persona, language directives, or core-document text.

Neither boundary makes a provider or executable trusted. Hashes detect change
but do not encrypt or authorize content. Provider transcripts may be retained,
and ending a story session keeps local thread references. Antigravity-native
state is retained on backend close; a tainted thread or changed definition
starts a fresh provider conversation without deleting prior provider data.

Exact models are in [Agent contracts](../reference/agent-contracts.md); privacy
implications are in [Privacy and provider exposure](../operations/privacy-and-provider.md).
