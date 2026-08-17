# Glossary

> **Availability:** after attachment, the shell manages subject conversations,
> sends routed advisory turns, searches active records, and reads bounded
> active or archived transcripts. See [Story sessions](guides/story-sessions.md).

Key terms: **story session**—related work; **conversation**—a session-scoped
subject and visible history; **turn**—user event plus outcome; **debate**—one consensus
attempt. See [Core concepts](concepts.md) for their relationships.

## A–W

- **Active, selected, and attached (legacy summary).** `active` maps only to
  durable `ready`/`paused`; `selected` means saved CLI default, never current
  attachment or conversation focus. Attached means `held_here` access. Provider
  readiness also requires configuration readiness to be exactly true. See
  [Session lifecycle](design/session-lifecycle.md).

- **Agent, role, and binding.** An agent is a fixed team member; its role defines
  responsibility and instructions, and a binding connects it to a backend. See
  [Agent contracts](reference/agent-contracts.md).

- **Artifact.** Any file the runtime produces or changes; a manuscript is one kind. See
  [Persistence](design/persistence.md).

- **Backend and provider.** The backend manages local routing/sessions and calls
  an external model provider. Only Codex is implemented. See
  [Prompts and provider](design/prompts-and-provider.md).

- **Blackboard.** Public append-only debate history: input, candidates, reviews,
  repairs, errors, and commit metadata. See [Persistence](design/persistence.md).

- **Candidate and revision.** The candidate is complete text under review; a
  revision identifies its version, beginning with `r0`. See
  [Consensus workflow](design/consensus-workflow.md).

- **Consensus and deadlock.** Consensus commits final artifacts after all voters
  approve one revision; deadlock leaves conflicts at the round limit. See
  [Inspection and recovery](guides/inspection-and-recovery.md).

- **Conversation.** An immutable identity inside a session. Its catalog stores subject and status;
  its event store holds the transcript. `StoryRuntime` addresses but does not
  create or select it. See [Conversation
  catalog](reference/conversation-catalog.md).

- **Conversation catalog and event store.** The catalog owns identity and discovery;
  the store owns append, full reads, and bounded context. Archive does not hide
  transcripts. See [Conversation
  transcripts](reference/conversation-transcripts.md).

- **Conversation citation, page, and cursor.** A citation holds stable identity
  and optional event. A page is a bounded read; its cursor continues that
  snapshot. See [Conversation search and cited transcript
  pages](reference/conversation-search.md).

- **Conversation search.** Bounded literal matching over current-session
  subjects and visible messages. Direct `/search` is a provider-free CLI
  command, not a provider call or tool; agent retrieval reaches memory only
  through runtime authorization. See [Conversation search](reference/conversation-search.md).

- **Core documents.** Editable outlines, character/world facts, and narrative
  rules; each role receives its allowed subset.
  See [Workspaces](guides/workspaces.md).

- **Debate.** One immutable attempt to reach consensus; a story session can
  contain many. See [Consensus workflow](design/consensus-workflow.md).

- **Directive.** An exact message that changes or resets session language or persona. See
  [Runtime overrides](reference/configuration/runtime-overrides.md).

- **Event, turn, and context window.** An event is one record; a complete turn
  pairs user input with a reply or error. Context is a bounded complete-turn view.
  See [Conversation transcripts](reference/conversation-transcripts.md).

- **Fingerprint and provider thread.** A fingerprint identifies resolved prompt
  context; its compatible provider thread is resumable. See
  [Prompts and provider](design/prompts-and-provider.md).

- **Lease, sidecar, and claim guard.** A lease is exclusive temporary attachment;
  its sidecar records ownership and the guard serializes local claims. See
  [Backend and sessions](reference/backend-and-sessions.md).

- **Manifest.** Durable provider-session active state and thread references,
  separate from the lease sidecar. See
  [Session storage](reference/configuration/session-storage.md).

- **Manuscript.** Immutable story output created after consensus/finalization. See
  [Consensus runs](guides/consensus-runs.md).

- **Owner-isolated state.** Concise persisted outcomes supplied only to their
  agent, unlike the blackboard; files remain plaintext. See
  [Persistence](design/persistence.md).

- **Prompt and draft.** A prompt asks for `r0`; a draft supplies it for review. See
  [Consensus runs](guides/consensus-runs.md).

- **Provider session.** Backend lifecycle state and resumable threads for one
  story session. See [Backend and sessions](reference/backend-and-sessions.md).

- **Runtime.** Local orchestration that validates, coordinates debates and turns,
  and persists results. See the
  [System overview](design/overview.md).

- **Story session.** Durable related work/history spanning debates and
  conversations. See [Story sessions](guides/story-sessions.md).

- **Workspace.** The project root for configuration, context, state, logs, and
  manuscripts—and the filesystem safety boundary. See
  [Workspaces](guides/workspaces.md).
