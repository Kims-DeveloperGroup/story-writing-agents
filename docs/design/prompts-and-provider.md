# Prompts and provider boundary

> **Availability:** file-referenced definitions, Codex prompt staging, advisory
> turns, debate requests with optional context, and compatible threads are
> implemented.

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

The controller passes its session, selected conversation, route, and input to
`StoryRuntime.converse()`; it does not build context or call the backend.
The runtime reads bounded pre-turn context, preflights and binds, saves the user
event, makes one call, validates the reply, and saves the outcome.

`BoundAgent.converse()` receives a validated `ConversationRequest` and projects
its fields to JSON. Each context item exposes turn ID, sender, recipient, kind,
and content, not storage metadata or window counts. It never reads or writes the
local transcript. See [Runtime API](../reference/runtime-api.md#one-conversation-turn).

Conversation IDs scope visible events, not provider-private history. The same
session-and-agent thread can have seen other subject conversations and debate
calls. Direct routing chooses a responder; it is not a private provider channel.

## Debate context field

An included debate adds a top-level `conversation_context` field to every agent
request. Drafting, audits, proposals, synthesis, and finalization all receive
the same captured event snapshot. The runtime does not reread or search the
transcript between phases. Fresh and legacy requests omit the field entirely.

This context is not concatenated into the bound prompt, phase `task`, original
`user_payload`, current candidate, public blackboard, or owner state. There is
no semantic retrieval, search result, or hidden memory injection. Exact request
fields are in [Agent contracts](../reference/agent-contracts.md#debate-request).

## Codex process boundary

The backend launches a constrained Codex subprocess from isolated staging.
Verified stable context reaches its temporary top-level `AGENTS.md`; request
JSON reaches standard input. Replies pass schema, correlation, and phase checks.
The exact sandbox, approval, tool, configuration, and instruction controls live
in [Privacy and provider exposure](../operations/privacy-and-provider.md).

Debate JSON contains task, input, candidate, blackboard, the receiving agent's
stored outcomes, and only when included, conversation context. It does not
repeat role prompts, persona, language directives, or core-document text.

This boundary does not make Codex or its executable trusted. Hashes detect
change but do not encrypt or authorize content. Provider transcripts may be
retained, and ending a story session keeps local thread references.

Exact models are in [Agent contracts](../reference/agent-contracts.md); privacy
implications are in [Privacy and provider exposure](../operations/privacy-and-provider.md).
