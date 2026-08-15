# Prompts and provider boundary

> **Availability:** file-referenced definitions, Codex prompt staging,
> phase-specific debate invocation, one-turn conversation calls, and persistent
> compatible threads are implemented.

Key terms: **agent definition**—stable context for one role; **fingerprint**—a
hash-derived identity for that context; **binding**—the provider object created
for a definition; **visible context**—prior events explicitly sent in a request;
**provider-private history**—state retained behind a provider thread ID.

Stable prompt context and dynamic turn data travel through different channels.
The runtime resolves the role prompt, persona, language, and allowlisted core
documents into one immutable definition. Each file reference contains an
absolute workspace path and SHA-256 digest, not the file text. Verified file
content is staged during binding; request JSON enters separately for each call.

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

## Conversation turn flow

The installed controller resolves its attached session, selected conversation,
and routed agent, then makes one synchronous `StoryRuntime.converse()` call for
the input line. It does not assemble context or invoke the backend directly.
Returned content goes to safe terminal output and never back through the command
parser; only `result.context.truncated` controls the optional context notice.

`StoryRuntime.converse()` is the high-level provider handoff. It reads the
addressed conversation's pre-turn context, preflights and binds the definition,
saves the user event, calls exactly one `BoundAgent`, validates the reply, and
saves the outcome. Exact ordering and failures live in the
[Runtime API](../reference/runtime-api.md#one-conversation-turn).

`BoundAgent.converse()` is the lower-level provider boundary. It receives an
already validated `ConversationRequest`, projects its visible fields into JSON,
invokes Codex, and returns a correlated `ConversationReply`. It may update
provider-thread metadata, but it never reads or writes the local transcript.

The provider payload contains the request's session, conversation, turn, agent,
current content, and context. A context item exposes only its turn, actors,
kind, and content—not storage metadata or window counters. If the same agent is
later asked about an `ending` conversation, this visible context contains only
validated `ending` events. Codex may still resume the compatible session-and-
agent thread that saw `opening`: conversation IDs scope visible context, not
provider-private history. `/to` and `@AGENT` select a responder in this shared
room; neither creates a private provider or transcript boundary.

## Codex process boundary

The backend launches one constrained Codex subprocess from the isolated
staging directory. Stable verified context reaches its temporary top-level
`AGENTS.md`, while request JSON reaches standard input; provider replies return
through schema and phase validation. The exact sandbox, approval, tool,
configuration, and instruction-discovery controls are documented once in
[Privacy and provider exposure](../operations/privacy-and-provider.md).

Debate request JSON contains task, original input, current candidate, public
blackboard snapshot, and only the receiving agent's persisted outcomes.
Conversation request JSON uses the exact projection above. Neither repeats role
prompts, persona text, language directives, or core-document text. Provider
output becomes a reply only after its schema and request correlation checks;
debate replies also receive phase-specific validation.

This boundary does not make the remote model or installed executable trusted.
SHA-256 detects change but does not encrypt or authorize content. The provider
may retain its own transcripts, and session end keeps thread references.

Exact model shapes are in [Agent contracts](../reference/agent-contracts.md);
privacy implications are in [Privacy and provider exposure](../operations/privacy-and-provider.md).
