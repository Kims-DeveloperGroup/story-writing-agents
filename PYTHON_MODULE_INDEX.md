# Python Module Index

## Scope and Authority

This index describes the current importable production units under
`src/story_writing_agents/`. It is a navigation record for later modularity
analysis and review, not a plan or a substitute for the
[Python Modularity Policy](.codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md).

Maintenance and mismatch handling are defined by the linked policy. This file
records only the observed implementation. Supported public imports are taken
from `story_writing_agents.__all__`, documented import paths, and the script
targets in `pyproject.toml`; test-only direct imports are recorded as internal.

Source root: `src/`

Package root: `src/story_writing_agents/`

Importable production units indexed: 19.

## Current Dependency Direction

The current graph flows from entry points and orchestration toward provider,
configuration, prompt, persistence, and stable contract modules:

```text
__main__ -> cli -> interactive_controller -> agents/backend/cli_context/conversation_search
                 |                         -> conversations/errors/interactive_commands
                 |                         -> interactive_terminal/models/orchestrator/workspace
                 -> interactive_terminal -> interactive_commands/agents
                 -> orchestrator -> prompts -> config -> workspace
                 |               |         |         -> conversations/models/errors/agents
                 |               |         -> workspace
                 |               -> conversations/workspace/models/agents
                 -> backend -> config/workspace/models/errors/agents
                 -> cli_context -> workspace/errors/agents

smoke -> orchestrator/workspace/models/agents
directives -> config/workspace/errors/agents
interactive_commands -> agents
interactive_terminal -> interactive_commands/agents
story_writing_agents facade -> interactive_commands
                            -> conversation_search -> conversations/errors
models -> conversations/agents/errors
workspace -> conversations -> agents/errors
```

Some modules directly consume lower-level contracts rather than following
every intermediate arrow. The current runtime graph is acyclic.

## Package and Module Entries

### `story_writing_agents`

- **Source:** `src/story_writing_agents/__init__.py`
- **Responsibility:** Provide the intentional package-level API facade.
- **Supported surface:** Exactly the names in `__all__`, grouped by owner:
  `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`, `LEAD_ORCHESTRATOR`,
  `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`, and `EXECUTION_SPECIALIST` from
  `agents`; `AgentBackend` and `BoundAgent` from `backend`; `DirectiveKind`,
  `RuntimeDirective`, `apply_directive`, and `parse_directive` from `directives`;
  `StoryWritingAgentsError`, `WorkspaceSecurityError`, `WorkspaceFormatError`,
  `ConfigurationError`, `DirectiveError`, `SessionIdError`,
  `ConversationNotFoundError`, `ConversationExistsError`,
  `ManuscriptExistsError`, `BackendProtocolError`, `SessionExistsError`,
  `SessionNotFoundError`, `SessionInUseError`, and `DebateExistsError` from
  `errors`; `CONVERSATION_CONTEXT_POLICY`,
  `CONVERSATION_EVENT_SCHEMA_VERSION`,
  `CONVERSATION_METADATA_SCHEMA_VERSION`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`,
  `MAX_CONVERSATION_SUBJECT_LENGTH`, `ConversationCatalog`,
  `ConversationContextPolicy`, `ConversationContextWindow`,
  `ConversationEvent`, `ConversationEventAppend`, `ConversationEventKind`,
  `ConversationEventStore`, `ConversationMetadata`, `ConversationStatus`, and
  `normalize_conversation_subject` from `conversations`;
  `CONVERSATION_MEMORY_POLICY`, `ConversationCitation`,
  `ConversationMemoryPolicy`, `ConversationMemoryService`,
  `ConversationSearch`, `ConversationSearchHit`,
  `ConversationSearchMatchKind`, `ConversationSearchQuery`,
  `ConversationSearchResult`, `ConversationSearchScope`,
  `ConversationTranscriptCursor`, `ConversationTranscriptPage`, and
  `ReferenceConversationSearch` from `conversation_search`; `ActionKind`,
  `InteractiveAction`, `CommandSpec`, `COMMANDS`, and
  `parse_interactive_line` from `interactive_commands`; `InputKind`, `Phase`,
  `Verdict`, `DebateStatus`, `SessionLeaseState`, `SessionSummary`,
  `DebateMessage`, `AgentStateEntry`, `PromptFileReference`, `AgentDefinition`,
  `AgentRequest`, `AgentReply`, `ConversationRequest`, `ConversationReply`,
  `ConversationResult`, and `DebateResult` from `models`; `StoryRuntime`,
  `RuntimeSession`, `Runtime`, and `Session` from `orchestrator`; and
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`,
  `Workspace`, plus `initialize_workspace` from `workspace`.
  The re-exported `ActionKind` and `InteractiveAction` include additive search
  and transcript cases plus the optional positive `start_sequence` payload;
  their public names and import paths are unchanged.
- **Direct internal dependencies:** `agents`, `backend`, `conversation_search`,
  `conversations`, `directives`, `errors`, `interactive_commands`, `models`,
  `orchestrator`, and `workspace`.
- **State, resources, and side effects:** Owns no state or external resource;
  importing the facade loads its exported modules.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_conversation_runtime.py`, `tests/test_codex_backend.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_session_lifecycle.py`, `tests/test_conversation_event_store.py`,
  `tests/test_interactive_commands.py`, `tests/test_conversation_search.py`,
  `docs/reference/package-api.md`, `docs/reference/runtime-api.md`,
  `docs/reference/conversation-search.md`, and
  `docs/reference/conversation-transcripts.md`.

### `story_writing_agents.__main__`

- **Source:** `src/story_writing_agents/__main__.py`
- **Responsibility:** Start the CLI for `python -m story_writing_agents`.
- **Supported surface:** Module execution only; no reusable API.
- **Direct internal dependencies:** `cli`.
- **State, resources, and side effects:** Calls `cli.main()` and raises
  `SystemExit` at module execution.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py` and
  `docs/reference/cli.md`.

### `story_writing_agents.agents`

- **Source:** `src/story_writing_agents/agents.py`
- **Responsibility:** Define the canonical story-agent roster and static
  least-context metadata.
- **Supported surface:** Package-supported `AGENT_SPECS`, `ROSTER`,
  `SPECIALIST_IDS`, `LEAD_ORCHESTRATOR`, `PLOT_ARCHITECT`,
  `CHARACTER_CUSTODIAN`, and `EXECUTION_SPECIALIST`. `AgentSpec`,
  `CORE_DOCUMENTS`, and `AGENT_STATE_DIRECTORIES` are internal contracts.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Immutable module metadata only.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `docs/concepts.md`, and `docs/reference/configuration/agent-configuration.md`.

### `story_writing_agents.backend`

- **Source:** `src/story_writing_agents/backend.py`
- **Responsibility:** Adapt immutable agent definitions to provider invocation
  and own provider-session manifest, attachment, and lease lifecycles.
- **Supported surface:** Package-supported `AgentBackend` and `BoundAgent`.
  `BoundAgent` exposes structured debate `invoke` and one-turn conversation
  `converse` operations. `AgentBackend` exposes validated `get_session_summary`
  and `list_sessions` reads; strict `create_session`, `resume_session`,
  `attach_session`, and `detach_session` attachment operations; and the
  compatible `prepare_session`, `start_session`, and `end_session` lifecycle
  methods.
- **Direct internal dependencies:** `agents`, `config`, `errors`, `models`, and
  `workspace`.
- **State, resources, and side effects:** Owns Codex subprocesses, temporary
  prompt/output staging, provider-session manifests and process attachment
  leases, the lazily created persistent workspace-global `.lease-claims.lock`
  OS advisory guard that serializes lease-claim and stale-reclaim transactions,
  persisted thread metadata, shared debate/conversation per-thread locks,
  structured Codex process validation, timeout/cancellation taint handling,
  cleanup, and per-instance `atexit` registration.
- **Primary verification/documentation:** `tests/test_codex_backend.py`,
  `tests/test_session_lifecycle.py`, `docs/reference/backend-and-sessions.md`,
  `docs/design/prompts-and-provider.md`, `docs/design/session-lifecycle.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.cli`

- **Source:** `src/story_writing_agents/cli.py`
- **Responsibility:** Parse command-line arguments and dispatch CLI commands.
- **Supported surface:** Script entry point `story_writing_agents.cli:main` for
  both `story` and `story-writing-agents`. `build_parser` and other helpers are
  not package-supported APIs.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`, `config`,
  `errors`, `interactive_controller`, `interactive_terminal`, `models`,
  `orchestrator`, and `workspace`; imports `smoke` lazily for the diagnostic
  command.
- **State, resources, and side effects:** Reads draft files and terminal input;
  writes JSON, plain-text interaction, or errors to standard streams; creates
  backends; constructs the interactive controller after a stdin/stdout TTY
  guard; and runs the async event loop. The controller owns interactive backend
  cleanup after its session-lobby run begins, while the CLI closes a backend if
  construction fails before that ownership transfer.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_cli_context.py`, `tests/test_smoke_cli.py`,
  `docs/reference/cli.md`, and `docs/getting-started.md`.

### `story_writing_agents.cli_context`

- **Source:** `src/story_writing_agents/cli_context.py`
- **Responsibility:** Discover initialized workspaces and manage workspace-local
  CLI selections.
- **Supported surface:** Internal `CliContext`, `resolve_workspace`,
  `load_cli_context`, `select_session`, `resolve_session`,
  `load_conversation_selection`, and `save_conversation_selection`.
- **Direct internal dependencies:** `agents`, `errors`, and `workspace`.
- **State, resources, and side effects:** Reads workspace sentinels and owns
  `agent_states/orchestrator_state/cli_context.json` plus lazy version-1
  per-session selection objects under
  `agent_states/orchestrator_state/conversation_selections/<session_id>.json`,
  all read and atomically written through `Workspace`. Missing conversation
  selection files are read as no selection without creating storage.
- **Primary verification/documentation:** `tests/test_cli_context.py`,
  `docs/reference/cli.md`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/design/session-lifecycle.md`.

### `story_writing_agents.config`

- **Source:** `src/story_writing_agents/config.py`
- **Responsibility:** Validate and resolve cascading runtime configuration.
- **Supported surface:** Internal `ConfigManager`, `SystemDefaults`,
  `AgentConfig`, `ResolvedAgentConfig`, and `KNOWN_AGENT_BACKENDS`.
- **Direct internal dependencies:** `agents`, `errors`, and `workspace`.
- **State, resources, and side effects:** Reads workspace configuration and
  owns no persistence.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `tests/test_prompts.py`, `tests/test_codex_backend.py`,
  `docs/reference/configuration/agent-configuration.md`, and
  `docs/reference/configuration/runtime-overrides.md`.

### `story_writing_agents.conversation_search`

- **Source:** `src/story_writing_agents/conversation_search.py`
- **Responsibility:** Provide provider-neutral deterministic read-only
  conversation search and bounded cited transcript-read contracts.
- **Supported surface:** Package-supported `CONVERSATION_MEMORY_POLICY`,
  `ConversationCitation`, `ConversationMemoryPolicy`,
  `ConversationMemoryService`, `ConversationSearch`, `ConversationSearchHit`,
  `ConversationSearchMatchKind`, `ConversationSearchQuery`,
  `ConversationSearchResult`, `ConversationSearchScope`,
  `ConversationTranscriptCursor`, `ConversationTranscriptPage`, and
  `ReferenceConversationSearch`.
- **Direct internal dependencies:** `conversations` and `errors`.
- **State, resources, and side effects:** Immutable value contracts and fixed
  policy metadata own no mutable state or external resource. Service and
  reference-search instances retain explicitly supplied `ConversationCatalog`
  and `ConversationEventStore` collaborators, plus their validated scope and
  optional search adapter; they perform reads only and have no import-time
  operational side effects.
- **Primary verification/documentation:**
  `tests/conversation_search_contract.py`,
  `tests/test_conversation_search.py`, and
  `docs/reference/conversation-search.md`.

### `story_writing_agents.conversations`

- **Source:** `src/story_writing_agents/conversations.py`
- **Responsibility:** Define and validate provider-neutral subject-conversation
  persistence contracts.
- **Supported surface:** Package-supported
  `CONVERSATION_CONTEXT_POLICY`, `CONVERSATION_EVENT_SCHEMA_VERSION`,
  `CONVERSATION_METADATA_SCHEMA_VERSION`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`,
  `MAX_CONVERSATION_SUBJECT_LENGTH`, `ConversationCatalog`,
  `ConversationContextPolicy`, `ConversationContextWindow`,
  `ConversationEvent`, `ConversationEventAppend`, `ConversationEventKind`,
  `ConversationEventStore`, `ConversationMetadata`, `ConversationStatus`, and
  `normalize_conversation_subject`.
- **Direct internal dependencies:** `agents` and `errors`.
- **State, resources, and side effects:** Immutable value contracts and
  deterministic normalization and validation only; owns no mutable state or
  external resource.
- **Primary verification/documentation:**
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_conversation_event_store.py`,
  `docs/reference/conversation-catalog.md`, and
  `docs/reference/conversation-transcripts.md`.

### `story_writing_agents.directives`

- **Source:** `src/story_writing_agents/directives.py`
- **Responsibility:** Parse exact session directives and apply validated
  configuration overrides.
- **Supported surface:** Package-supported `DirectiveKind`, `RuntimeDirective`,
  `parse_directive`, and `apply_directive`.
- **Direct internal dependencies:** `agents`, `config`, `errors`, and
  `workspace`.
- **State, resources, and side effects:** Persists session overrides through
  `Workspace`.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`
  and `docs/reference/configuration/runtime-overrides.md`.

### `story_writing_agents.errors`

- **Source:** `src/story_writing_agents/errors.py`
- **Responsibility:** Define the expected public exception taxonomy.
- **Supported surface:** Package-supported `StoryWritingAgentsError`,
  `WorkspaceSecurityError`, `WorkspaceFormatError`, `ConfigurationError`,
  `DirectiveError`, `SessionIdError`, `ConversationNotFoundError`,
  `ConversationExistsError`, `ManuscriptExistsError`, `BackendProtocolError`,
  `SessionExistsError`, `SessionNotFoundError`, `SessionInUseError`, and
  `DebateExistsError`.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** None.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_conversation_catalog.py`, `tests/test_session_lifecycle.py`,
  `tests/test_interactive_commands.py`, failure-path tests, and
  `docs/reference/configuration/identifiers-and-errors.md`.

### `story_writing_agents.interactive_commands`

- **Source:** `src/story_writing_agents/interactive_commands.py`
- **Responsibility:** Own the deterministic interactive command language.
- **Supported surface:** Package-supported `ActionKind`, `InteractiveAction`,
  `CommandSpec`, `COMMANDS`, and `parse_interactive_line`, plus module-supported
  `AgentRouteSpec` and `AGENT_ROUTES` at their owning path. `AgentRouteSpec` is
  frozen and slotted; `AGENT_ROUTES` is immutable, follows canonical roster
  order, and owns each route's canonical ID, preferred prompt alias, ordered
  aliases, and advisory-domain copy. Parser helpers and command-alias maps are
  internal; agent parsing and canonical validation derive from the route
  registry. Search and transcript actions extend the frozen, slotted
  `InteractiveAction` with the optional positive `start_sequence` field;
  existing action payloads remain compatible. `/search QUERY` and
  `/transcript ID [COUNT] [--from SEQUENCE]` participate in the same immutable
  help registry and exact parser. The package facade does not re-export the
  route metadata.
- **Direct internal dependencies:** `agents`.
- **State, resources, and side effects:** Immutable command and agent-route
  metadata only; owns no process state and performs no provider, filesystem,
  persistence, environment, or import-time operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_commands.py`,
  `tests/test_public_api_and_cli.py`, and
  `docs/reference/interactive-command-parser.md`.

### `story_writing_agents.interactive_controller`

- **Source:** `src/story_writing_agents/interactive_controller.py`
- **Responsibility:** Coordinate one synchronous interactive
  story-session/conversation shell lifecycle.
- **Supported surface:** Module-supported `InteractiveController`, whose
  constructor accepts explicit workspace, CLI-context, backend, and terminal
  collaborators plus an optional keyword-only `ConversationCatalog`, and whose
  single-use `run_session_lobby` operation returns process status 0 or 2. An
  omitted catalog uses `FilesystemConversationCatalog`. The shell dispatches
  provider-free roster, sticky-target, current-session search, and bounded
  transcript-read actions plus selected-conversation advisory turns while
  retaining the established conversation and story-session commands. Search
  and transcript reads require an attachment but no selected conversation. All
  transition, identity-resolution, routing, result-validation, and rendering
  helpers are private, and the package facade does not re-export this module.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`,
  `conversation_search`, `conversations`, `errors`, `interactive_commands`,
  `interactive_terminal`, `models`, `orchestrator`, and `workspace`.
- **State, resources, and side effects:** Instances retain borrowed workspace,
  CLI-context, backend, and terminal collaborators plus either a borrowed
  injected catalog or a controller-constructed default filesystem catalog. It
  constructs and retains one `StoryRuntime` and one
  `FilesystemConversationEventStore` bound to the resolved catalog. It shares
  that exact event-store instance with the runtime and the attachment-scoped
  `ConversationMemoryService`. The memory service is absent in lobby state,
  constructed for a validated destination before attachment commit, rebound on
  a successful switch, and cleared on end, cleanup, and final close. The
  controller also owns its current attached-session identifier, selected
  conversation metadata, sticky target, current CLI-context snapshot, and
  one-run/closed state. After the run starts, it owns orderly backend
  detach/close coordination while `AgentBackend` retains the actual lease,
  manifest, subprocess, and provider-thread resources. Through public
  collaborators the controller reads and parses terminal input, renders safe
  output, sequences session and nested conversation transitions, performs
  catalog create/list/get/rename/archive and compensating unarchive calls,
  persists selections through `cli_context`, performs active-only search and
  fresh bounded active-or-archived transcript reads through
  `ConversationMemoryService`, and runs one synchronous `StoryRuntime.converse`
  operation per accepted content turn. `StoryRuntime` owns context reads,
  transcript/provider sequencing, validated results, and its async caches and
  locks; the event store, catalog, and `Workspace` retain persistence ownership.
  The controller never calls event-store operations or backend provider
  dispatch directly, performs no consensus or artifact work, and has no
  import-time operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_interactive_conversation.py`, `tests/test_session_lifecycle.py`,
  `tests/test_cli_context.py`, `tests/test_interactive_commands.py`,
  `tests/test_interactive_terminal.py`, and
  `tests/test_interactive_search.py` for real-filesystem shell search and
  transcript-read integration; `docs/design/session-lifecycle.md`,
  `docs/reference/cli.md`, and `docs/reference/interactive-terminal.md` verify
  the current collaborator and lifecycle contracts.

### `story_writing_agents.interactive_terminal`

- **Source:** `src/story_writing_agents/interactive_terminal.py`
- **Responsibility:** Provide deterministic, safe plain-text interaction
  primitives for an interactive terminal.
- **Supported surface:** Module-supported `AttachmentKind`, `ComposeMode`,
  `ConfirmationResult`, `TerminalActionKind`, `TerminalMessageKind`,
  `InteractiveErrorCode`, `TerminalAction`, `InteractiveErrorSpec`,
  `INTERACTIVE_ERROR_SPECS`, `InteractiveError`, `sanitize_terminal_text`, and
  `InteractiveTerminal`. `InteractiveTerminal.request_confirmation` returns a
  typed yes/no/interrupt/EOF result, and `write_session_orientation` accepts an
  optional history count whose absence omits history claims.
  `request_conversation_subject` performs one typed line/interrupt/EOF read;
  `write_conversation_scope` and `write_conversation_list_entry` render bounded
  conversation context and catalog rows, and `write_agent_roster` projects the
  shared route registry and canonical display names into one safe roster block.
  `write_conversation_search_hit` accepts primitive `conversation_handle`,
  `conversation_subject`, `match_kind`, and `snippet` values plus keyword-only
  `archived` and optional citation values;
  `write_conversation_transcript_event` accepts primitive
  `conversation_handle`, `sequence`, `turn_id`, `kind`, `sender`, `recipient`,
  and `content` values and preserves internal, empty, and trailing content lines
  in one output record.
  The message-kind and immutable error registries include conversation
  presentation and the six typed conversation failures; rendering and
  validation helpers are private.
- **Direct internal dependencies:** `agents` and `interactive_commands`.
- **State, resources, and side effects:** Instances retain borrowed synchronous
  line-read and text-write callbacks plus an immutable display width; each
  composition call and subject request own only call-local input state. Methods
  read through the supplied callback and write sanitized, newline-terminated
  plain-text blocks, including adjacent conversation scope, listing rows, and
  the metadata-derived agent roster. Prompt aliases derive from
  `AGENT_ROUTES`; the module owns immutable display metadata, no persistent or
  external resource, and performs no provider, filesystem, persistence,
  environment, or import-time operational side effects. Search-hit and
  transcript-event blocks sanitize controls; transcript content preserves line
  structure with deterministic hanging indentation while remaining one
  callback record.
- **Primary verification/documentation:** `tests/test_interactive_terminal.py`,
  `docs/reference/interactive-terminal.md`, and
  `docs/operations/terminal-safety.md`; `tests/test_interactive_commands.py`
  and `docs/reference/interactive-command-parser.md` verify the consumed
  command registry contract.

### `story_writing_agents.models`

- **Source:** `src/story_writing_agents/models.py`
- **Responsibility:** Define immutable cross-boundary request, reply, state, and
  result contracts.
- **Supported surface:** Package-supported `InputKind`, `Phase`, `Verdict`,
  `DebateStatus`, `SessionLeaseState`, `SessionSummary`, `DebateMessage`,
  `AgentStateEntry`, `PromptFileReference`, `AgentDefinition`, `AgentRequest`,
  `AgentReply`, `ConversationRequest`, `ConversationReply`,
  `ConversationResult`, and `DebateResult`.
- **Direct internal dependencies:** `agents`, `conversations`, and `errors`.
- **State, resources, and side effects:** Value contracts own no process state;
  `PromptFileReference` performs verified filesystem reads.
- **Primary verification/documentation:** `tests/test_prompts.py`,
  `tests/test_codex_backend.py`, `tests/test_conversation_runtime.py`,
  `tests/test_session_lifecycle.py`, orchestration and state tests,
  `docs/reference/agent-contracts.md`, `docs/reference/runtime-api.md`, and
  `docs/reference/backend-and-sessions.md`.

### `story_writing_agents.orchestrator`

- **Source:** `src/story_writing_agents/orchestrator.py`
- **Responsibility:** Coordinate provider-neutral runtime workflows across
  debates and advisory conversation turns.
- **Supported surface:** Package-supported `StoryRuntime` and `RuntimeSession`,
  including `StoryRuntime.converse()` and `RuntimeSession.converse()`, plus
  compatibility aliases `Runtime` and `Session`.
- **Direct internal dependencies:** `agents`, `backend`, `config`, `directives`,
  `conversations`, `errors`, `models`, `prompts`, and `workspace`.
- **State, resources, and side effects:** Retains an injected or default
  `ConversationEventStore` collaborator and owns the in-memory binding cache,
  in-flight bind tasks, binding lock, and per-session/conversation advisory-turn
  locks. It sequences debate persistence and the advisory context-read,
  user-append, provider-call, and reply/error-append flow while the event store,
  `Workspace`, and backend retain their persistence and provider resources.
- **Primary verification/documentation:** `tests/test_conversation_runtime.py`,
  `tests/test_interactive_conversation.py` for retained runtime use across
  separate interactive event loops, `tests/test_orchestration_workflow.py`,
  `tests/test_orchestration_safety.py`, `tests/test_lifecycle_atomicity.py`,
  `tests/test_agent_state.py`, `docs/reference/runtime-api.md`, and
  `docs/design/consensus-workflow.md`.

### `story_writing_agents.prompts`

- **Source:** `src/story_writing_agents/prompts.py`
- **Responsibility:** Assemble content-addressed agent definitions and
  prompt-free per-turn requests.
- **Supported surface:** Internal `PromptAssembler`.
- **Direct internal dependencies:** `agents`, `config`, `models`, and
  `workspace`.
- **State, resources, and side effects:** Reads configuration, verified prompt
  references, and owner-isolated agent history; performs no writes.
- **Primary verification/documentation:** `tests/test_prompts.py`,
  `tests/test_codex_backend.py`, `docs/reference/agent-contracts.md`, and
  `docs/design/prompts-and-provider.md`.

### `story_writing_agents.smoke`

- **Source:** `src/story_writing_agents/smoke.py`
- **Responsibility:** Run the provider-free deterministic CLI diagnostic.
- **Supported surface:** Internal `run_smoke`; no package-supported API.
- **Direct internal dependencies:** `agents`, `models`, `orchestrator`, and
  `workspace`.
- **State, resources, and side effects:** Creates a temporary or explicitly
  retained workspace and exercises normal runtime persistence.
- **Primary verification/documentation:** `tests/test_smoke_cli.py`,
  `docs/guides/smoke-test.md`, and `docs/reference/cli.md`.

### `story_writing_agents.workspace`

- **Source:** `src/story_writing_agents/workspace.py`
- **Responsibility:** Provide the secure workspace filesystem and persistence
  boundary.
- **Supported surface:** Package-supported `FilesystemConversationCatalog`,
  `FilesystemConversationEventStore`, `Workspace`, and
  `initialize_workspace`; topology, serialization, and lock-registry details
  are internal.
- **Direct internal dependencies:** `agents`, `conversations`, `errors`, and
  `models`.
- **State, resources, and side effects:** Owns topology, path and symlink
  security, strict UTF-8 and JSON I/O, overrides, agent and debate state, logs,
  results, per-conversation metadata under
  `discussion_logs/<session_id>/interactive/conversations/<conversation_id>/metadata.json`,
  authoritative visible events under
  `discussion_logs/<session_id>/interactive/conversations/<conversation_id>/transcript.jsonl`,
  manuscript/core-document commits, atomic replacement, and rollback. A
  guarded process-wide weak lock registry serializes operations per
  workspace/session/conversation;
  the story-session lease remains the cross-process same-session write guard.
  Catalog and absent-transcript reads do not create storage. Transcript access
  validates catalog identity, and appends atomically replace the complete file.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `tests/test_agent_state.py`, `tests/test_lifecycle_atomicity.py`,
  `tests/test_public_api_and_cli.py`, `tests/test_conversation_catalog.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_event_store.py`,
  `docs/reference/workspace-api.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`, `docs/design/persistence.md`,
  and `docs/operations/filesystem-safety.md`.

## Consistency Notes

- `interactive_commands` owns canonical command and agent-route metadata, parse
  rules, and typed parsed actions. `interactive_terminal` consumes its public
  `COMMANDS` and `AGENT_ROUTES` metadata plus canonical display names to own
  safe prompt, confirmation, help, roster, status, error, call-local
  composition, subject input, conversation scope, catalog-row, search-hit, and
  transcript-event presentation. It returns typed terminal outcomes without
  dispatching them.
- `interactive_controller` owns interactive shell action mapping, current
  in-memory attachment, selected-conversation and sticky-target state,
  transition and compensation ordering, retained runtime and memory-service
  wiring, and backend detach/close coordination after its one run starts.
  `AgentBackend` retains actual session lease, manifest, subprocess, and
  provider-thread ownership; `ConversationCatalog` retains conversation
  metadata ownership; `cli_context` owns both session and per-session
  conversation-selection persistence; and `interactive_terminal` retains
  synchronous I/O and presentation ownership. Advisory content crosses only
  the public `StoryRuntime.converse` boundary. Provider-free inspection crosses
  only public `ConversationMemoryService.search` and
  `ConversationMemoryService.read_transcript` boundaries; the controller
  performs no direct event-store or backend provider operation and performs no
  consensus or artifact operation.
- The controller wires its resolved `ConversationCatalog` and one retained
  `FilesystemConversationEventStore` into both `StoryRuntime` and the
  attachment-scoped `ConversationMemoryService`, so injected and default
  catalogs remain the same authority for metadata and transcript identity. It
  prepares the destination service before attachment commit, rebinds it only
  with the committed attachment, and clears it on switch, end, cleanup, and
  final close; conversation selection does not rebind it. Search defaults to
  active conversations, while transcript identifiers resolve across active and
  archived conversations and each invocation obtains a fresh bounded page.
  `StoryRuntime` owns the advisory context read, user append, provider call,
  validated result, and reply or sanitized-error append; the controller uses
  only the returned result and performs no duplicate transcript read.
- `Workspace` and `ConversationEventStore` implementations own persistence
  mechanics. `StoryRuntime` sequences debate persistence and, for advisory
  turns, the validated context read, durable user append, one provider call,
  and reply or sanitized-error append.
- `AgentBackend` owns the shared structured Codex subprocess, thread,
  timeout, cancellation, and taint lifecycle; `BoundAgent` is the immutable
  per-definition dispatch handle for debate and one-turn conversation calls.
- `ConversationCatalog` exposes logical identifiers and immutable metadata only;
  `FilesystemConversationCatalog` owns the fixed metadata layout through
  `Workspace` without exposing paths through that protocol.
- `ConversationEventStore` owns visible event append, authoritative read, and
  fixed-policy complete-turn context contracts;
  `FilesystemConversationEventStore` owns the fixed JSONL layout through
  `Workspace` without exposing paths through that protocol.
- `ReferenceConversationSearch` may call the event-store port's full `read`
  operation while enforcing deterministic logical scan and result bounds; it
  owns neither the filesystem layout nor persistence resources.
- The filesystem event adapter's process-wide weak locks serialize one
  conversation in process; the story-session lease remains responsible for
  cross-process same-session write exclusion.
- One `StoryRuntime` serializes a complete advisory operation per
  `(session_id, conversation_id)` while allowing other conversations to
  proceed. This does not widen the event adapter's or story-session lease's
  cross-runtime guarantees.
- Persisted provider-thread keys include the backend and agent ID. In-process
  serialization additionally includes the session ID, so `invoke` and
  `converse` cannot race one session/backend/agent thread.
- `ConversationRequest` restricts the explicit visible payload context to its
  conversation identity. Provider-private thread history remains scoped by
  session, backend, and agent and can therefore span conversations.
- `tests/conversation_event_store_contract.py` is reusable test-only adapter
  conformance support; it is not a production module or supported package API.
- Direct imports of internal configuration, prompt, workspace-constant, or CLI
  helpers in tests do not add them to the supported package API.
