# Glossary

> **Availability:** the shell manages conversations, search, transcripts,
> history, and inspection. Python callers can run lead advisory/proposal turns
> and exact decisions, plus separately confirmed consensus phases. Consensus
> has no installed shell command and never itself publishes artifacts. See
> [Lead agent](guides/lead-agent.md) and [Consensus runs](guides/consensus-runs.md).

## A–W

- **Access claim and claim guard.** The temporary V2 claim names its host,
  process, and token. The workspace-global guard serializes claim, proved-dead-local
  replacement, and exact-token release. See [Session lifecycle](design/session-lifecycle.md).

- **Agent, role, and binding.** An agent is a fixed team member; its role defines
  responsibility and instructions, and a binding connects it to a backend. See
  [Agent contracts](reference/agent-contracts.md).

- **Lead front channel.** The Lead Orchestrator is the only story agent users
  address directly. It can answer, question, critique, recommend, or propose;
  runtime—not provider prose—owns proposal and confirmation authority. See
  [Lead agent](guides/lead-agent.md).

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

- **Consensus, deadlock, and continuation.** Phase-confirmed consensus moves one
  unpublished candidate through current-revision audits, nonvoting lead
  synthesis, optional repair/re-audit, and read-only finalization. Deadlock is
  terminal and nonpublishing, but an explicit continuation can resume the same
  lineage or create a linked workflow by referencing the exact candidate and
  unresolved critique material—never by copying their text. The continuation
  requires an operator ruling, a positive repair-round budget, and fresh user
  confirmation for every phase. Artifact Apply and Git Commit are later,
  separately confirmed operations. See [Consensus runs](guides/consensus-runs.md).

- **Conversation.** An immutable session-scoped identity with mutable display
  subject/status and authoritative schema-v2 collaboration history.
  See [Conversation catalog](reference/conversation-catalog.md).

- **Conversation catalog and event store.** The catalog owns identity,
  display-only subject, and discovery; the active v2 store owns atomic append,
  full reads, and bounded context. Archive does not hide transcript reads. See [Conversation
  transcripts](reference/conversation-transcripts.md).

- **Conversation citation, page, and cursor.** A citation identifies an exact
  event, readable field, and code-point range; a page is a bounded read, and
  its cursor continues only the unchanged snapshot. See
  [Conversation search](reference/conversation-search.md).

- **Conversation search.** Bounded literal matching over closed readable event
  fields in active current-session conversations. Subjects are display-only
  metadata. Direct `/search` is a provider-free CLI
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

- **Event, turn, and context window.** An event is one canonical schema-v2
  collaboration fact. A readable record exposes only its closed public fields.
  Provider context is an explicit bounded whole-record selection; its default
  bootstrap contains no history content.
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
  `manuscript/PART/PATH.md`. Direct repository components and the later
  execution workflow preserve distinct part-scoped revision
  paths. See [Consensus runs](guides/consensus-runs.md).

- **Owner-isolated state.** Concise persisted outcomes supplied only to their
  agent, unlike the blackboard; files remain plaintext. See
  [Persistence](design/persistence.md).

- **Prompt and supplied draft.** Both create a separately confirmed DRAFT
  request. A prompt asks the Execution Specialist to author `r0`; a supplied
  complete draft is transformed into newly authored `r0` before audit. See
  [Consensus runs](guides/consensus-runs.md).

- **Provider session.** Backend lifecycle state and resumable threads for one
  story session. See [Backend and sessions](reference/backend-and-sessions.md).

- **Process access.** `claimable`, `held_here`,
  `blocked_by_live_local_owner`, or `ownership_unknown`: the current safe view
  of exclusive session ownership.

- **Provider readiness.** Exact `True` configuration readiness combined with
  durable `ready` and process access `held_here`. The query is pure and does not
  acquire access.

- **Runtime.** The management/configuration/inspection and lead-orchestration
  facade. Its Python surface includes confirmed lead consultations and
  separately confirmed consensus phases; its installed shell still has no
  story-execution command. See [Runtime API](reference/runtime-api.md).

- **Story session.** Durable related work/history spanning debates and
  conversations. See [Story sessions](guides/story-sessions.md).

- **Workspace.** The project root for configuration, context, state, logs, and
  manuscripts—and the filesystem safety boundary. See
  [Workspaces](guides/workspaces.md).
