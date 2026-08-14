# Glossary

> **Availability:** most terms describe installed runtime behavior. Conversation
> persistence and strict session lifecycle are Python-only. Interactive
> selection, dispatch, transcript commands, and search are not implemented.

Key terms: **story session**—durable related work; **conversation**—a
session-scoped subject and visible history; **turn**—one user event and its
outcome; **debate**—one consensus attempt. They are different scopes; scan their
entries below, then see [Core concepts](concepts.md) for the visual model.

Use this page for lookup; follow its links for exact behavior and contracts.

## A–W

- **Active, selected, and attached.** Independent session facts. Active permits
  provider work; selected is the CLI default; attached means a backend holds
  the temporary lease. See [Session lifecycle](design/session-lifecycle.md).

- **Agent, role, and binding.** An agent is one fixed team member. Its role is
  its responsibility and instructions. A binding connects its resolved
  definition to a backend. See [Agent contracts](reference/agent-contracts.md).

- **Artifact.** A file produced or changed by the runtime, such as state, a log,
  or a manuscript. Artifact is broader than manuscript. See
  [Persistence](design/persistence.md).

- **Backend and provider.** The backend is local code that manages routing,
  sessions, and invocation. The provider is the external model system it calls;
  Codex is the only implemented provider. See
  [Prompts and provider](design/prompts-and-provider.md).

- **Blackboard.** The public, append-only debate history shared with the team.
  It records visible input, candidates, reviews, repairs, errors, and commit
  metadata. See [Persistence](design/persistence.md).

- **Candidate and revision.** A candidate is the complete story text currently
  under review. A revision identifies one version of that candidate, beginning
  with `r0`. See [Consensus workflow](design/consensus-workflow.md).

- **Consensus and deadlock.** Consensus means every voting specialist approved
  the same current revision and final artifacts committed. Deadlock means
  conflicts remained at the round limit. See
  [Inspection and recovery](guides/inspection-and-recovery.md).

- **Conversation.** One immutable identity within a story session. Its catalog
  stores subject and archive status; its event store separately keeps the
  visible transcript. Selection and dispatch are not implemented. See
  [Conversation catalog](reference/conversation-catalog.md).

- **Conversation catalog and event store.** The catalog owns identity and
  discovery metadata. The event store owns append, full transcript reads, and
  bounded context; archiving does not hide transcript data. See
  [Conversation transcripts](reference/conversation-transcripts.md).

- **Core documents.** Editable files containing durable outlines, character and
  world facts, and narrative rules. Each role receives only its allowed subset.
  See [Workspaces](guides/workspaces.md).

- **Debate.** One immutable attempt to reach consensus; a story session can
  contain many. See [Consensus workflow](design/consensus-workflow.md).

- **Directive.** An exact standalone message that changes language or persona
  overrides for one story session, or resets them. See
  [Runtime overrides](reference/configuration/runtime-overrides.md).

- **Event, turn, and context window.** An event is one visible stored record. A
  complete turn pairs one user event with one reply or runtime-error outcome. A
  context window is a bounded view of complete turns, not a second transcript.
  See [Conversation transcripts](reference/conversation-transcripts.md).

- **Fingerprint and provider thread.** A fingerprint identifies an agent's
  resolved prompt context. A provider thread is the resumable conversation
  reference used while that context stays compatible. See
  [Prompts and provider](design/prompts-and-provider.md).

- **Lease, sidecar, and claim guard.** A lease is temporary exclusive attachment
  to provider-session threads. Its sidecar records ownership; the claim guard
  serializes local claims. See [Backend and sessions](reference/backend-and-sessions.md)
  for the exact lifecycle rules.

- **Manifest.** The durable provider-session record, including active state and
  resumable thread references. It is different from the temporary lease
  sidecar. See [Session storage](reference/configuration/session-storage.md).

- **Manuscript.** The immutable story output created only after consensus and
  successful finalization. It is one kind of artifact. See
  [Consensus runs](guides/consensus-runs.md).

- **Owner-isolated state.** Concise persisted outcomes that the runtime supplies
  only to their owning agent, unlike the team-visible blackboard. The files
  remain local plaintext. See
  [Persistence](design/persistence.md).

- **Prompt and draft.** A prompt asks the execution specialist to create `r0`.
  A draft supplies the complete `r0` for immediate review. See
  [Consensus runs](guides/consensus-runs.md).

- **Provider session.** Backend-managed lifecycle state and resumable threads
  for one story session ID. Active and attached describe this provider-facing
  layer. See [Backend and sessions](reference/backend-and-sessions.md).

- **Runtime.** The local orchestration layer that validates input, coordinates
  agents, decides consensus, and persists results. See the
  [System overview](design/overview.md).

- **Story session.** The durable boundary for related work and agent history;
  it can span debates and conversations. See [Story sessions](guides/story-sessions.md).

- **Workspace.** The local project root containing configuration, story context,
  state, logs, and manuscripts. It is also the filesystem safety boundary. See
  [Workspaces](guides/workspaces.md).
