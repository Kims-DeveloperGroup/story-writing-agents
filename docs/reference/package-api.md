# Package API and exports

> **Availability:** every name below is exported from the implemented
> `story_writing_agents` package facade. Context and progress types are Python
> APIs; an export does not imply a CLI command.

Key terms: **package facade**—the names re-exported at the package root;
**public import**—a supported symbol imported from that facade; **alias**—a
second name for the same object; **enum**—a type with a fixed value set.

Import public contracts from the package root:

```python
from story_writing_agents import StoryRuntime, Workspace
```

The exact 120-name export inventory is:

- roster: `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`, `LEAD_ORCHESTRATOR`,
  `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`, `EXECUTION_SPECIALIST`;
- backend: `AgentBackend`, `BoundAgent`;
- session lifecycle: `SessionDurableState`, `SessionAccessState`,
  `SessionLifecycleState`, and `provider_work_is_ready`;
- agent, conversation-turn, and debate models: `PromptFileReference`,
  `AgentDefinition`, `AgentRequest`, `AgentReply`, `ConversationRequest`,
  `ConversationReply`, `ConversationResult`, `AgentStateEntry`,
  `DebateMessage`, `DebateResult`, `InputKind`, `Phase`, `Verdict`,
  `DebateStatus`, `DebateContextMode`, `DebateContextRequest`,
  `DebateFailureKind`, `DebateProgressPhase`, `DebateProgressEvent`, and
  `DebateProgressSink`;
- runtime: `StoryRuntime`, `RuntimeSession`, `Runtime`, `Session`;
- directives: `DirectiveKind`, `RuntimeDirective`, `parse_directive`,
  `apply_directive`;
- workspace: `Workspace`, `initialize_workspace`,
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`;
- conversation catalog: `ConversationCatalog`, `ConversationMetadata`,
  `ConversationStatus`, `normalize_conversation_subject`,
  `CONVERSATION_METADATA_SCHEMA_VERSION`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`, and
  `MAX_CONVERSATION_SUBJECT_LENGTH`;
- conversation transcripts: `ConversationEventStore`, `ConversationEvent`,
  `ConversationEventAppend`, `ConversationEventKind`,
  `ConversationContextPolicy`, `ConversationContextWindow`,
  `CONVERSATION_EVENT_SCHEMA_VERSION`, and `CONVERSATION_CONTEXT_POLICY`;
- conversation search and cited reads: `ConversationMemoryPolicy`,
  `CONVERSATION_MEMORY_POLICY`, `ConversationSearchScope`,
  `ConversationSearchQuery`, `ConversationSearchMatchKind`,
  `ConversationCitation`, `ConversationSearchHit`, `ConversationSearchResult`,
  `ConversationSearch`, `ReferenceConversationSearch`,
  `ConversationMemoryService`, `ConversationTranscriptCursor`, and
  `ConversationTranscriptPage`;
- bounded retrieval: `CONVERSATION_RETRIEVAL_POLICY`,
  `ConversationRetrievalPolicy`, `ConversationRetrievalActionKind`,
  `ConversationRetrievalAction`, `ConversationRetrievalSearchAction`,
  `ConversationRetrievalReadAction`, `ConversationRetrievalAnswerAction`,
  `ConversationRetrievalObservation`, `ConversationRetrievalSearchObservation`,
  `ConversationRetrievalReadObservation`, `ConversationRetrievalSearchItem`,
  `ConversationRetrievalTranscriptEvent`, `ConversationRetrievalSource`,
  `ConversationRetrievalRequest`, `ConversationRetrievalReply`,
  `ConversationRetrievalResult`, `ConversationRetrievalActionHandler`,
  `ConversationRetrievalProgressPhase`, `ConversationRetrievalProgressEvent`,
  and `ConversationRetrievalProgressSink`;
- interactive parser: `ActionKind`, `InteractiveAction`, `CommandSpec`,
  `COMMANDS`, `parse_interactive_line`;
- errors: `StoryWritingAgentsError`, `WorkspaceSecurityError`,
  `WorkspaceFormatError`, `WorkspaceVersionUnsupportedError`,
  `ConfigurationError`, `DirectiveError`,
  `SessionIdError`, `ConversationNotFoundError`,
  `ConversationExistsError`, `ManuscriptExistsError`,
  `BackendProtocolError`, `SessionExistsError`, `SessionNotFoundError`,
  `SessionPausedError`, `SessionAccessBlockedError`,
  `SessionOwnershipUnknownError`, `DebateExistsError`, `ProgressObserverError`,
  `ConversationRetrievalError`, `ConversationRetrievalSemanticError`,
  `ConversationRetrievalBudgetError`, and `ConversationRetrievalObserverError`.

`Runtime` is exactly an alias of `StoryRuntime`; `Session` is exactly an alias
of `RuntimeSession`. They do not provide different behavior.

Canonical artifact value contracts live in
`story_writing_agents.writing_artifacts`, but are intentionally absent from the
120-name package-root facade. In addition to immutable path, catalog, kind, and
reference values, the subpackage exports content-free authorization, operation
history/state, read-request/action, `ReferenceOperationFailure`, and parser
contracts. Filesystem-backed `WritingArtifactRepository`, content-bearing read
results, private capabilities, runtime coordinators, the ranged viewer, and
interactive activation values remain internal. The installed controller uses
that private UI boundary for `$`/`$$` prompt editing without adding a public
selection API or standalone artifact command. See
[Canonical writing artifacts](writing-artifacts.md).

The export inventory and public signatures are unchanged by runtime diagnostics.
Public `AgentBackend` and `StoryRuntime` operations use an internal
workspace-bound writer and may append bounded operational records, including
for reads that do not mutate provider-session, conversation, debate, or story
state. No debug-log class, enum, configuration, or telemetry surface is exported
from the package root. See [Runtime API](runtime-api.md#runtime-diagnostic-side-effect),
[Backend and sessions](backend-and-sessions.md#backend-diagnostic-side-effect),
and [Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

## Enum values

| Enum | Exact values |
| --- | --- |
| `InputKind` | `prompt`, `draft` |
| `Phase` | `draft`, `audit`, `propose_revision`, `synthesize`, `finalize` |
| `Verdict` | `approve`, `conflict` |
| `DebateStatus` | `consensus`, `deadlock`, `failed` |
| `DebateContextMode` | `included`, `fresh` |
| `DebateProgressPhase` | `started`, `drafted`, `audit_started`, `audit_completed`, `synthesized`, `artifacts_committed`, `consensus`, `deadlock`, `failed`, `interrupted` |
| `DebateFailureKind` | `preflight`, `protocol`, `context`, `workspace`, `artifact_conflict`, `artifact_failure`, `internal` |
| `DirectiveKind` | `language`, `persona`, `reset` |
| `SessionDurableState` | `absent`, `ready`, `paused` |
| `SessionAccessState` | `claimable`, `held_here`, `blocked_by_live_local_owner`, `ownership_unknown` |
| `ConversationStatus` | `active`, `archived` |
| `ConversationSearchMatchKind` | `exact_subject`, `subject_prefix`, `subject_substring`, `transcript` |
| `ConversationRetrievalActionKind` | `search_conversations`, `read_transcript`, `answer` |
| `ConversationRetrievalProgressPhase` | `search_started`, `search_completed`, `read_started`, `read_completed` |

These are `StrEnum` values. Use members such as `Verdict.APPROVE`; a plain
string like `"approve"` is not accepted where reply validation requires the
enum member.

There are no alternate lifecycle summaries, projections, or package-root
aliases. See [Backend and sessions](backend-and-sessions.md#lifecycle-value-contracts).

## Reference map

- [Backend and sessions](backend-and-sessions.md)
- [Agent contracts](agent-contracts.md)
- [Runtime API](runtime-api.md)
- [Workspace API](workspace-api.md)
- [Canonical writing artifacts](writing-artifacts.md)
- [Conversation catalog](conversation-catalog.md)
- [Conversation transcripts](conversation-transcripts.md)
- [Conversation search and cited transcript pages](conversation-search.md)
- [Bounded conversation retrieval](conversation-retrieval.md)
- [Interactive command parser](interactive-command-parser.md)
- [Identifiers and errors](configuration/identifiers-and-errors.md)

Lower-level modules may contain implementation helpers, but the package facade
above is the supported public import surface and matches `__all__`.
