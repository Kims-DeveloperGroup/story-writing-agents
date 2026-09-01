# Glossary

> **Availability:** with a session attached, the shell manages conversations,
> search, transcripts, history, and inspection. Public advisory turns and
> consensus are unavailable in this integration stage. See
> [Story sessions](guides/story-sessions.md).

## A–W

- **Access claim and claim guard.** The temporary V2 claim names its host,
  process, and token. The workspace-global guard serializes claim, proved-dead-local
  replacement, and exact-token release. See [Session lifecycle](design/session-lifecycle.md).

- **Agent, role, and binding.** An agent is a fixed team member; its role defines
  responsibility and instructions, and a binding connects it to a backend. See
  [Agent contracts](reference/agent-contracts.md).

- **Writing artifact.** Agent-produced story output at one of four exact core
  paths or beneath `manuscript/PART/`. Runtime internals are not writing
  artifacts. See [Canonical writing artifacts](reference/writing-artifacts.md).

- **Backend and provider.** The backend manages local routing/sessions and calls
  an external model provider. Codex is the final fallback; Antigravity is also
  supported through `agy`. See
  [Prompts and provider](design/prompts-and-provider.md) and the
  [Antigravity guide](guides/antigravity.md).

- **Provider authentication state and workspace default.** A fresh native probe
  yields authenticated, unauthenticated, unavailable, or indeterminate. Only
  authenticated authorizes a route. Nullable agent routes inherit the persisted
  workspace default and then Codex; interactive onboarding fills the default
  only when missing. See the
  [CLI authentication contract](reference/cli.md#interactive-provider-authentication).

- **Blackboard.** Public append-only debate history: input, candidates, reviews,
  repairs, errors, and commit metadata. See [Persistence](design/persistence.md).

- **Candidate and revision.** The candidate is complete text under review; a
  revision identifies its version, beginning with `r0`. See
  [Consensus workflow](design/consensus-workflow.md).

- **Complete live frame.** All terminal rows and cells drawn for one capable
  input read, including context, prompt, editable text, suggestions or choices,
  validation, and redraws. It clears before the read returns; persistent
  semantic result records are separate. See the
  [interactive design contract](design/interactive-cli-design-system.md#submitted-input-lifecycle).

- **Consensus and deadlock.** Consensus commits final artifacts after all voters
  approve one revision; deadlock leaves conflicts at the round limit. See
  [Inspection and recovery](guides/inspection-and-recovery.md).

- **Conversation.** An immutable session-scoped subject and visible history.
  Its catalog stores identity and status; its event store holds the transcript.
  See [Conversation catalog](reference/conversation-catalog.md).

- **Conversation catalog and event store.** The catalog owns identity and discovery;
  the store owns append, full reads, and bounded context. Archive does not hide
  transcripts. See [Conversation
  transcripts](reference/conversation-transcripts.md).

- **Conversation citation, page, and cursor.** A citation identifies a record;
  a page is a bounded read, and its cursor continues that snapshot. See
  [Conversation search](reference/conversation-search.md).

- **Conversation search.** Bounded literal matching over current-session
  subjects and visible messages. Direct `/search` is a provider-free CLI
  command, not a provider call or tool; agent retrieval reaches memory only
  through runtime authorization. See [Conversation search](reference/conversation-search.md).

- **Current attachment.** The story session whose process access this shell
  holds. A default session is not necessarily attached. See
  [Session lifecycle](design/session-lifecycle.md).

- **Current conversation focus.** The process-local conversation selected for
  management and inspection. It does not select an advisory responder.

- **Default session and default conversation.** Persisted fallbacks used when a
  command omits a session or when session attachment restores conversation
  scope. A default does not prove attachment, focus, or provider readiness.

- **Core documents.** Outlines, character/world facts, and narrative rules at
  four exact stable paths. Built-in contextual execution treats role-relevant
  current bytes as authoritative P2 sources; it does not permanently bind them
  as stable provider instructions. Publication provenance separately records
  agent-authored replacements.
  See [Workspaces](guides/workspaces.md).

- **Context manifest, checkpoint, and action.** The manifest inventories
  content-free current source facts; a READY checkpoint authorizes a compatible
  later delta; bounded SEARCH/READ actions add exact authorized evidence. None
  is a free-form memory or replacement for a mandatory reference. See
  [Bounded agent context](guides/agent-context.md).

- **Debate.** One immutable attempt to reach consensus; a story session can
  contain many. See [Consensus workflow](design/consensus-workflow.md).

- **Directive.** An exact message that changes or resets session language or persona. See
  [Runtime overrides](reference/configuration/runtime-overrides.md).

- **Durable state.** `absent`, `ready`, or `paused`: whether a provider session
  is missing, permits provider work, or is intentionally stopped. It is
  independent of process access.

- **Event, turn, and context window.** An event is one record; a complete turn
  pairs user input with a reply or error. Context is a bounded complete-turn view.
  See [Conversation transcripts](reference/conversation-transcripts.md).

- **Fingerprint and provider thread.** A fingerprint identifies resolved prompt
  context; its compatible provider thread is resumable. See
  [Prompts and provider](design/prompts-and-provider.md).

- **Manifest.** Durable V2 provider-session state and thread references,
  separate from the temporary access claim. See
  [Session storage](reference/configuration/session-storage.md).

- **Provider-native state.** Plaintext files managed by the local provider CLI,
  distinct from the runtime manifest and transcripts. Antigravity-native state
  is isolated by story session, agent, and definition fingerprint and retained
  when the backend closes. See
  [Session storage](reference/configuration/session-storage.md).

- **Manuscript.** Create-only story output at
  `manuscript/PART/PATH.md`. Direct repository components and the future
  confirmed collaboration workflow preserve distinct part-scoped revision
  paths. See [Consensus runs](guides/consensus-runs.md).

- **Owner-isolated state.** Concise persisted outcomes supplied only to their
  agent, unlike the blackboard; files remain plaintext. See
  [Persistence](design/persistence.md).

- **Prompt and draft.** A prompt asks for `r0`; a draft supplies it for review. See
  [Consensus runs](guides/consensus-runs.md).

- **Provider session.** Backend lifecycle state and resumable threads for one
  story session. See [Backend and sessions](reference/backend-and-sessions.md).

- **Process access.** `claimable`, `held_here`,
  `blocked_by_live_local_owner`, or `ownership_unknown`: the current safe view
  of exclusive session ownership.

- **Provider readiness.** Exact `True` configuration readiness combined with
  durable `ready` and process access `held_here`. The query is pure and does not
  acquire access.

- **Runtime.** The current management/configuration/inspection facade plus
  retained low-level orchestration components. It has no public provider-turn
  method in this stage. See the
  [System overview](design/overview.md).

- **Story session.** Durable related work/history spanning debates and
  conversations. See [Story sessions](guides/story-sessions.md).

- **Workspace.** The project root for configuration, context, state, logs, and
  manuscripts—and the filesystem safety boundary. See
  [Workspaces](guides/workspaces.md).
