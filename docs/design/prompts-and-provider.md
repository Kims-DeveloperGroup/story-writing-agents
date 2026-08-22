# Prompts and provider boundary

> **Availability:** file-referenced definitions, Codex prompt staging, advisory
> turns with optional bounded retrieval, context-aware debates, and compatible
> threads are implemented.

Key terms: **definition**—stable context for one role; **fingerprint**—its
hash-derived identity; **binding**—the provider object for a definition;
**visible context**—events explicitly sent in a request; **provider-private
history**—state behind a provider thread ID.

Stable prompt context and dynamic call data use different channels. During
definition construction, role prompts and personas are read once, while the
artifact repository selectively reads only the current provenance-backed core
documents allowlisted for that role. Every result becomes an immutable
content-backed reference containing a lexical absolute path, SHA-256, and the
exact captured bytes. The backend stages those bytes at binding and never
rereads the canonical artifact paths. Request JSON enters separately for each
call.

## Bound role context

When eligible, the plot architect receives global and part outlines; the
character custodian gets character sheets; the execution specialist gets
narrative rules; and the lead gets all four core documents. `read_current()`
touches only those requested core paths, so accumulating manuscripts are not
read while definitions are assembled. Absent, manual, stale, or unprovenanced
core files are omitted rather than bound. Definitions include no unrelated
workspace paths.

The fingerprint covers agent, provider, language, component identities, and
hashes. Editing a role prompt or persona changes the next definition. An
external manual core edit made while workspace processes are stopped removes
that document from eligibility; generic `Workspace` writes reject canonical
artifact paths. Later agent republication can bind new provenanced bytes. Either
effective-context change starts a fresh provider thread for that agent.
Provider conversation state remains partitioned by story session and agent.

## Binding resource lifecycle

The runtime bounds binding and prompt-staging ownership per canonical agent.
These internal states do not add a command, status field, or durable record:

| Internal state | Transition and user-visible meaning |
| --- | --- |
| Desired/current | The newest requested fingerprint becomes the definition for new work. At quiescence, the runtime retains at most one current binding for that agent. |
| Single-flight bind | Concurrent requests for the same fingerprint await one bind and share its immutable staged definition. |
| Operation lease | Debate invocation, advisory conversation, and retrieval each retain the exact selected binding until that operation exits on success, failure, or cancellation. |
| Superseded-active / retiring | A different fingerprint may become current while old work is still running. New work uses the newest desired definition; existing work keeps its original definition and staging until its last lease exits. More than one staging directory may therefore exist during overlap. |
| Retired | An idle superseded or abandoned binding is retired exactly once. Its owned temporary staging is then eligible for best-effort removal and cannot be selected for later work. |

If fingerprints change from A to B and back to A, the newest desired definition
still wins; a compatible binding that remains safely retained may become current
again, while abandoned out-of-order bind results are retired. Repeated
agent-authored core publication therefore does not accumulate idle bindings or
prompt-staging directories without bound. Provider calls, replies, conversation
threads, and durable session state retain their existing contracts.

Snapshot timing matters. Once a definition exists, changing, deleting, or
replacing a source path does not change that definition or the bytes staged for
its binding. A later definition construction performs a fresh verified read and
may produce a different fingerprint. This prevents a path swap between
repository verification and provider staging from changing what the agent
receives.

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
implications are in [Privacy and provider exposure](../operations/privacy-and-provider.md),
and provenance states are in
[Canonical writing artifacts](../reference/writing-artifacts.md).
