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

Importable production units indexed: 21.

## Current Dependency Direction

The current graph flows from entry points and orchestration toward provider,
configuration, prompt, persistence, and stable contract modules:

```text
__main__ -> cli -> interactive_controller -> agents/backend/cli_context
                 |                         -> conversation_retrieval/conversation_search
                 |                         -> conversations/errors/interactive_commands
                 |                         -> interactive_terminal/models/orchestrator/run_inputs/workspace
                 -> interactive_terminal -> interactive_commands/agents
                 -> run_inputs -> errors
                 -> orchestrator -> conversation_retrieval -> agents/conversation_search
                 |               |                           -> conversations/errors
                 |               -> conversation_search -> conversations/errors
                 |               -> prompts -> config -> workspace
                 |               |         |         -> conversations/models/errors/agents
                 |               |         -> conversations
                 |               |         -> workspace
                 |               -> conversations/workspace/models/agents
                 -> backend -> config/conversation_retrieval/workspace/models/errors/agents
                 -> cli_context -> workspace/errors/agents

smoke -> orchestrator/workspace/models/agents
directives -> config/workspace/errors/agents
interactive_commands -> agents
interactive_terminal -> interactive_commands/agents
story_writing_agents facade -> interactive_commands
                            -> conversation_retrieval -> agents/conversation_search
                            |                          -> conversations/errors
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
- **Supported surface:** Exactly 115 names in `__all__`, grouped by owner:
  `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`, `LEAD_ORCHESTRATOR`,
  `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`, and `EXECUTION_SPECIALIST` from
  `agents`; `AgentBackend` and `BoundAgent` from `backend`; `DirectiveKind`,
  `RuntimeDirective`, `apply_directive`, and `parse_directive` from `directives`;
  `StoryWritingAgentsError`, `WorkspaceSecurityError`, `WorkspaceFormatError`,
  `ConfigurationError`, `DirectiveError`, `SessionIdError`,
  `ConversationNotFoundError`, `ConversationExistsError`,
  `ManuscriptExistsError`, `BackendProtocolError`, `SessionExistsError`,
  `SessionNotFoundError`, `SessionInUseError`, `DebateExistsError`,
  `ProgressObserverError`, `ConversationRetrievalError`,
  `ConversationRetrievalSemanticError`, `ConversationRetrievalBudgetError`, and
  `ConversationRetrievalObserverError` from `errors`;
  `CONVERSATION_CONTEXT_POLICY`,
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
  `ReferenceConversationSearch` from `conversation_search`;
  `CONVERSATION_RETRIEVAL_POLICY`, `ConversationRetrievalAction`,
  `ConversationRetrievalActionHandler`, `ConversationRetrievalActionKind`,
  `ConversationRetrievalAnswerAction`, `ConversationRetrievalObservation`,
  `ConversationRetrievalPolicy`, `ConversationRetrievalProgressEvent`,
  `ConversationRetrievalProgressPhase`, `ConversationRetrievalProgressSink`,
  `ConversationRetrievalReadAction`, `ConversationRetrievalReadObservation`,
  `ConversationRetrievalReply`, `ConversationRetrievalRequest`,
  `ConversationRetrievalResult`, `ConversationRetrievalSearchAction`,
  `ConversationRetrievalSearchItem`, `ConversationRetrievalSearchObservation`,
  `ConversationRetrievalSource`, and `ConversationRetrievalTranscriptEvent`
  from `conversation_retrieval`; `ActionKind`,
  `InteractiveAction`, `CommandSpec`, `COMMANDS`, and
  `parse_interactive_line` from `interactive_commands`; `InputKind`, `Phase`,
  `Verdict`, `DebateStatus`, `DebateContextMode`, `DebateContextRequest`,
  `DebateFailureKind`, `DebateProgressPhase`, `DebateProgressEvent`,
  `DebateProgressSink`, `SessionLeaseState`, `SessionSummary`, `DebateMessage`,
  `AgentStateEntry`, `PromptFileReference`, `AgentDefinition`, `AgentRequest`,
  `AgentReply`, `ConversationRequest`, `ConversationReply`, `ConversationResult`,
  and `DebateResult` from `models`; `StoryRuntime`, `RuntimeSession`, `Runtime`,
  and `Session` from `orchestrator`; and
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`,
  `Workspace`, plus `initialize_workspace` from `workspace`.
  The re-exported `ActionKind` and `InteractiveAction` include additive search
  and transcript cases plus the optional positive `start_sequence` payload;
  their public names and import paths are unchanged.
- **Direct internal dependencies:** `agents`, `backend`,
  `conversation_retrieval`, `conversation_search`, `conversations`,
  `directives`, `errors`, `interactive_commands`, `models`, `orchestrator`, and
  `workspace`.
- **State, resources, and side effects:** Owns no state or external resource;
  importing the facade loads its exported modules.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_conversation_runtime.py`, `tests/test_codex_backend.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_session_lifecycle.py`, `tests/test_conversation_event_store.py`,
  `tests/test_interactive_commands.py`, `tests/test_conversation_search.py`,
  `docs/reference/package-api.md`, `docs/reference/runtime-api.md`,
  `docs/reference/conversation-search.md`,
  `docs/reference/conversation-transcripts.md`, and
  `docs/reference/conversation-retrieval.md`.

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
  `converse` operations plus additive bounded
  `converse_with_retrieval(request, handler)`. `AgentBackend` exposes validated
  `get_session_summary` and `list_sessions` reads; strict `create_session`,
  `resume_session`, `attach_session`, and `detach_session` attachment
  operations; and the compatible `prepare_session`, `start_session`, and
  `end_session` lifecycle methods.
- **Direct internal dependencies:** `agents`, `config`,
  `conversation_retrieval`, `errors`, `models`, and `workspace`.
- **State, resources, and side effects:** Owns Codex subprocesses, temporary
  prompt/output staging, provider-session manifests and process attachment
  leases, the lazily created persistent workspace-global `.lease-claims.lock`
  OS advisory guard that serializes lease-claim and stale-reclaim transactions,
  persisted thread metadata, shared debate/conversation/retrieval per-thread
  locks, structured Codex process validation, timeout/cancellation taint
  handling, cleanup, and per-instance `atexit` registration. One retrieval
  thread lock covers the complete repeated-call action loop and synchronous
  handler invocations. The backend owns the exact Codex action schema, initial
  and continuation payloads, canonical observation framing handoff, provider
  timeout/cancellation behavior, incomplete-history taint, and diagnostic
  redaction; it receives no memory service and performs no retrieval search or
  transcript-storage call. Debate requests add the canonical included-context
  event list to provider payloads only when present; legacy and fresh requests
  retain the exact context-free payload, while included event content
  participates in diagnostic redaction.
- **Primary verification/documentation:** `tests/test_codex_backend.py`,
  `tests/test_session_lifecycle.py`, `docs/reference/backend-and-sessions.md`,
  `docs/reference/conversation-retrieval.md`,
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
  `orchestrator`, `run_inputs`, and `workspace`; imports `smoke` lazily for the
  diagnostic command.
- **State, resources, and side effects:** Reads terminal input and delegates
  caller-selected draft-file reads to `run_inputs`; writes JSON, plain-text
  interaction, or errors to standard streams; creates backends; constructs the
  interactive controller after a stdin/stdout TTY guard; and runs the async
  event loop. The controller owns interactive backend cleanup after its
  session-lobby run begins, while the CLI closes a backend if construction
  fails before that ownership transfer.
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

### `story_writing_agents.conversation_retrieval`

- **Source:** `src/story_writing_agents/conversation_retrieval.py`
- **Responsibility:** Define immutable provider-neutral bounded conversation-
  retrieval actions, observations, results, progress, fixed policy, canonical
  untrusted-data framing, and the synchronous runtime/backend handler port.
- **Supported surface:** Module-supported `CONVERSATION_RETRIEVAL_POLICY`,
  `ConversationRetrievalAction`, `ConversationRetrievalActionHandler`,
  `ConversationRetrievalActionKind`, `ConversationRetrievalAnswerAction`,
  `ConversationRetrievalObservation`, `ConversationRetrievalPolicy`,
  `ConversationRetrievalProgressEvent`,
  `ConversationRetrievalProgressPhase`, `ConversationRetrievalProgressSink`,
  `ConversationRetrievalReadAction`, `ConversationRetrievalReadObservation`,
  `ConversationRetrievalReply`, `ConversationRetrievalRequest`,
  `ConversationRetrievalResult`, `ConversationRetrievalSearchAction`,
  `ConversationRetrievalSearchItem`, `ConversationRetrievalSearchObservation`,
  `ConversationRetrievalSource`, `ConversationRetrievalTranscriptEvent`, and
  `serialize_conversation_retrieval_observation`. The package facade exports
  every listed name except the serializer.
- **Direct internal dependencies:** `agents`, `conversation_search`,
  `conversations`, and `errors`.
- **State, resources, and side effects:** Frozen, slotted contracts, immutable
  policy metadata, exact validators, and pure canonical serialization only.
  The module performs no I/O and owns no storage, provider, process, thread,
  filesystem, or mutable runtime state and has no operational import side
  effect.
- **Primary verification/documentation:**
  `tests/test_conversation_retrieval.py`, `tests/test_public_api_and_cli.py`,
  and `docs/reference/conversation-retrieval.md`.

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
  `ReferenceConversationSearch`. `ConversationMemoryService.scope` is an
  additive read-only projection of its immutable authorized session scope.
- **Direct internal dependencies:** `conversations` and `errors`.
- **State, resources, and side effects:** Immutable value contracts and fixed
  policy metadata own no mutable state or external resource. Service and
  reference-search instances retain explicitly supplied `ConversationCatalog`
  and `ConversationEventStore` collaborators, plus their validated scope and
  optional search adapter; the scope property performs no catalog, store, or
  search access. Instances perform reads only and have no import-time
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
  `normalize_conversation_subject`. The canonical
  `select_bounded_conversation_context` and
  `validate_conversation_context_window` helpers are module-supported rather
  than facade-exported. The selector validates one exact chronological
  transcript snapshot and derives its fixed-policy newest suffix of complete
  turns without persistence access.
- **Direct internal dependencies:** `agents` and `errors`.
- **State, resources, and side effects:** Immutable value contracts plus
  deterministic normalization, pure fixed-policy complete-turn context
  selection, and identity-bound context-window validation only; owns no
  mutable state or external resource.
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
  `DebateExistsError`, plus `ProgressObserverError` for synchronous debate
  progress-sink failures; and `ConversationRetrievalError` with fixed-message
  `ConversationRetrievalSemanticError`, `ConversationRetrievalBudgetError`, and
  `ConversationRetrievalObserverError` subclasses for retrieval authorization,
  whole-turn budget, and synchronous progress-observer failures.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** None.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_conversation_catalog.py`, `tests/test_session_lifecycle.py`,
  `tests/test_interactive_commands.py`, failure-path tests, and
  `docs/reference/configuration/identifiers-and-errors.md` plus
  `docs/reference/conversation-retrieval.md` for the retrieval hierarchy.

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
  provider-free global status, attached runtime directives and safe
  session/debate inspection, selected-conversation history, roster,
  sticky-target, current-session search, and bounded transcript-read actions;
  selected-conversation advisory turns; and selected-conversation consensus
  prompt, fresh-prompt, and draft-file actions while retaining the established
  conversation and story-session commands. Search, transcript reads,
  directives, and inspection require an attachment but no selected
  conversation; history and every consensus action require both an attachment
  and an active selection. All transition, identity-resolution, routing,
  directive-result, inspection, trusted debate/retrieval progress and result,
  retrieval-source, and other presentation validation helpers are private, and
  the package facade does not re-export this module.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`,
  `conversation_retrieval`, `conversation_search`, `conversations`, `errors`,
  `interactive_commands`, `interactive_terminal`, `models`, `orchestrator`,
  `run_inputs`, and `workspace`.
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
  conversation metadata, sticky target, current CLI-context snapshot,
  process-local per-session latest accepted STARTED debate-ID map, and
  one-run/closed state. Once its single-use run starts, it constructs and owns
  one private `asyncio.Runner` for the rest of that lifecycle. Repeated
  `Runner.run` calls execute every accepted advisory and consensus coroutine on
  that Runner; controller-local entry/cancellation/join evidence and public
  Runner task inspection distinguish a quiescent pre-entry interruption, a
  completed entered cancellation, and an unjoined or otherwise uncertain
  operation. Final cleanup closes the Runner and its residual asynchronous
  work, detaches any still-known attachment, and then closes the backend. Later
  cleanup stages remain attempted after an earlier failure; ordinary cleanup
  failures force status 2 and one best-effort `cleanup_failed` diagnostic,
  while a cleanup `BaseException` propagates after later stages run and an
  unsuccessfully interrupted Runner close retains its private reference.
  `AgentBackend` retains the actual lease, manifest, subprocess, and
  provider-thread resources. Through public
  collaborators the controller reads and parses terminal input, renders safe
  output, sequences session and nested conversation transitions, performs
  catalog create/list/get/rename/archive and compensating unarchive calls,
  persists selections through `cli_context`, applies exact existing directives
  and reads whitelisted session/debate inspection through `StoryRuntime`,
  performs active-only search and fresh bounded active-or-archived transcript
  reads through `ConversationMemoryService`, reads the authoritative event
  store exactly once for each selected history or status snapshot, derives
  canonical context facts through `select_bounded_conversation_context`, and
  runs one synchronous
  `StoryRuntime.converse_with_retrieval` operation per accepted advisory content
  turn through the retained Runner. It passes the exact attachment-scoped
  service plus a synchronous sink, validates trusted progress/results, and
  renders fixed progress and ordered source copy through existing terminal
  status output. Before advisory coroutine entry, the established fixed
  cancelled/hint copy returns attached only when public Runner task inspection
  proves quiescence; that path calls no provider and mutates no controller or
  persistent state. After entry, returning attached requires the runtime to
  receive `CancelledError` and the operation to join; every other advisory
  cancellation state fails closed. Provider-free search, transcript, and
  selected-history interruption stops further rendering and returns to the
  same prompt without persistence mutation. The controller also delegates draft
  reads to `run_inputs` and submits each accepted consensus action through the
  same Runner. A trusted `INTERRUPTED` progress event, which may be first after
  reservation, is its sole durable-interruption proof; only `STARTED` updates
  the process-local latest-debate map, pre-entry cancellation remains
  provider-free, and an entered cancellation without proof fails closed through
  `operation_interrupted` and `cleanup_failed`. `StoryRuntime` owns context
  capture, retrieval authorization and memory access, transcript/provider and
  consensus sequencing, validated results, artifact durability, and its async
  caches and locks; the event store, catalog, and `Workspace` retain persistence
  ownership. The controller's only direct event-store operation is the public
  read used for fixed selected history/status snapshots; it never appends or
  calls `read_context`, dispatches a backend provider directly, or persists
  retrieval controls/progress/sources, debate context/results, or artifacts,
  and has no import-time operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_interactive_management.py`, `tests/test_interactive_consensus.py`,
  `tests/test_interactive_conversation.py`,
  `tests/test_interactive_cancellation.py`, `tests/test_session_lifecycle.py`,
  `tests/test_cli_context.py`, `tests/test_interactive_commands.py`,
  `tests/test_interactive_terminal.py`, and
  `tests/test_interactive_search.py` for real-filesystem shell search and
  transcript-read integration; `docs/design/session-lifecycle.md`,
  `docs/reference/cli.md`, `docs/reference/interactive-terminal.md`, and
  `docs/reference/conversation-retrieval.md` verify the current collaborator,
  lifecycle, and retrieval-presentation contracts.

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
  The unchanged message-kind registry covers conversation presentation. The
  immutable error registry adds `retrieval_invalid` and
  `retrieval_budget_exhausted` to the six existing typed conversation failures;
  renderers and validation helpers are unchanged and private.
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
  `docs/reference/interactive-terminal.md`,
  `docs/reference/conversation-retrieval.md`, and
  `docs/operations/terminal-safety.md`; `tests/test_interactive_commands.py`
  and `docs/reference/interactive-command-parser.md` verify the consumed
  command registry contract.

### `story_writing_agents.models`

- **Source:** `src/story_writing_agents/models.py`
- **Responsibility:** Define immutable cross-boundary request, reply, state, and
  result contracts.
- **Supported surface:** Package-supported `InputKind`, `Phase`, `Verdict`,
  `DebateStatus`, `DebateContextMode`, `DebateContextRequest`,
  `DebateFailureKind`, `DebateProgressPhase`, `DebateProgressEvent`,
  `DebateProgressSink`, `SessionLeaseState`, `SessionSummary`, `DebateMessage`,
  `AgentStateEntry`, `PromptFileReference`, `AgentDefinition`, `AgentRequest`,
  `AgentReply`, `ConversationRequest`, `ConversationReply`, `ConversationResult`,
  and `DebateResult`. `AgentRequest` carries an additive optional canonical
  `conversation_context` field without changing context-free construction.
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
  including unchanged one-step `converse()`, additive bounded
  `converse_with_retrieval()` with required scoped memory service and optional
  synchronous progress sink, context-aware `run_prompt()` and `run_draft()`,
  and the separate strict `inspect_debate_context()` accessor on both forms,
  plus compatibility aliases `Runtime` and `Session`.
- **Direct internal dependencies:** `agents`, `backend`, `config`,
  `conversation_retrieval`, `conversation_search`, `directives`,
  `conversations`, `errors`, `models`, `prompts`, and `workspace`.
- **State, resources, and side effects:** Retains an injected or default
  `ConversationEventStore` collaborator and owns the in-memory binding cache,
  in-flight bind tasks, binding lock, and per-session/conversation advisory-turn
  locks. For an included consensus request it captures one canonical immutable
  context window before reservation, reuses that exact snapshot for every agent
  phase, and delegates create-only sidecar persistence to `Workspace`; fresh
  mode persists identity with no snapshot and an absent request remains legacy.
  It synchronously emits typed provider-neutral debate progress in lifecycle
  order, makes result and terminal state durable before terminal debate
  progress, preserves typed observer failures, and retains external
  cancellation while best-effort recording interruption. It also sequences
  ordinary debate persistence and unchanged one-step advisory context-read,
  user-append, provider-call, and reply/error-append flow.
  For bounded retrieval it validates the exact current-session memory-service
  scope and synchronous sink before context, provider, store, or user-event
  access; then holds the existing `(session, conversation)` lock across context
  capture, one user append, the complete provider action loop, and one final
  reply or safe runtime-error append. A private synchronous handler owns action
  correlation and ordering, active-only search and cited read authorization,
  fixed cumulative budgets, complete-visible-turn filtering, deep memory-result
  revalidation, canonical framed-observation charging, supplied-source
  resolution, and content-free progress. Cancellation preserves the existing
  transcript outcome and re-raises. The injected memory service owns search and
  transcript reads; the event store, `Workspace`, and backend retain their
  persistence and provider resources. Retrieval creates no debate, artifact,
  catalog, owner-state, or control/progress/source persistence.
- **Primary verification/documentation:** `tests/test_conversation_runtime.py`,
  `tests/test_conversation_retrieval.py`, `tests/test_interactive_consensus.py`,
  `tests/test_interactive_conversation.py` for retained runtime use through the
  controller-owned persistent Runner and its single lifecycle event loop,
  `tests/test_orchestration_workflow.py`,
  `tests/test_orchestration_safety.py`, `tests/test_lifecycle_atomicity.py`,
  `tests/test_agent_state.py`, `docs/reference/runtime-api.md`,
  `docs/reference/conversation-retrieval.md`, and
  `docs/design/consensus-workflow.md`.

### `story_writing_agents.prompts`

- **Source:** `src/story_writing_agents/prompts.py`
- **Responsibility:** Assemble content-addressed agent definitions and
  prompt-free per-turn requests.
- **Supported surface:** Internal `PromptAssembler`.
- **Direct internal dependencies:** `agents`, `config`, `conversations`,
  `models`, and `workspace`.
- **State, resources, and side effects:** Reads configuration, verified prompt
  references, and owner-isolated agent history; forwards an optional canonical
  conversation-context object unchanged into each built `AgentRequest` and
  performs no writes.
- **Primary verification/documentation:** `tests/test_prompts.py`,
  `tests/test_codex_backend.py`, `docs/reference/agent-contracts.md`, and
  `docs/design/prompts-and-provider.md`.

### `story_writing_agents.run_inputs`

- **Source:** `src/story_writing_agents/run_inputs.py`
- **Responsibility:** Provide shared strict caller-selected UTF-8 draft-file
  loading.
- **Supported surface:** Module-supported `read_draft_file`; it is intentionally
  not exported by the package facade.
- **Direct internal dependencies:** `errors`.
- **State, resources, and side effects:** Owns no retained state or import-time
  side effect. Each call expands `~` and performs one call-local external file
  read using caller-CWD `Path` semantics; absolute paths, symlinks, and literal
  metacharacters remain allowed, and empty strict-UTF-8 text is returned for the
  runtime caller to validate.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_cli_context.py`, `tests/test_interactive_consensus.py`,
  `docs/reference/cli.md`, and `docs/guides/consensus-runs.md`.

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
  `initialize_workspace`. `Workspace.inspect_debate_context` is the strict,
  separate context-sidecar inspection operation; topology, serialization, and
  lock-registry details are internal.
- **Direct internal dependencies:** `agents`, `conversations`, `errors`, and
  `models`.
- **State, resources, and side effects:** Owns topology, path and symlink
  security, strict UTF-8 and JSON I/O, overrides, agent and debate state, logs,
  results, per-conversation metadata under
  `discussion_logs/<session_id>/interactive/conversations/<conversation_id>/metadata.json`,
  authoritative visible events under
  `discussion_logs/<session_id>/interactive/conversations/<conversation_id>/transcript.jsonl`,
  immutable debate-context sidecars under
  `discussion_logs/<session_id>/<debate_id>.context.json`, manuscript/core-document
  commits, atomic replacement, and rollback. Context sidecars use an exact
  identity-bound version-1 schema, require a non-null canonical snapshot for
  included mode and a null snapshot for fresh mode, reject malformed or
  colliding files, and return `None` when absent for legacy debates. A
  guarded process-wide weak lock registry serializes operations per
  workspace/session/conversation;
  the story-session lease remains the cross-process same-session write guard.
  Catalog and absent-transcript reads do not create storage. Transcript access
  validates catalog identity, and appends atomically replace the complete file.
  `FilesystemConversationEventStore.read_context` retains its event lock across
  catalog validation and one authoritative transcript read, then delegates the
  pure fixed-policy selection to `conversations`.
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
  transcript-event presentation. Issue #21 adds two immutable retrieval error
  entries without changing message kinds, rendering, or dependencies. The
  terminal returns typed outcomes without dispatching them.
- `interactive_controller` owns interactive shell action mapping, current
  in-memory attachment, selected-conversation and sticky-target state,
  process-local per-session latest accepted STARTED debate IDs, transition and
  compensation ordering, retained runtime and memory-service wiring, one
  private lifecycle `asyncio.Runner`, controller-local interruption/progress
  proof, and ordered Runner-close, known-detach, and backend-close coordination
  after its one run starts.
  `AgentBackend` retains actual session lease, manifest, subprocess, and
  provider-thread ownership; `ConversationCatalog` retains conversation
  metadata ownership; `cli_context` owns both session and per-session
  conversation-selection persistence; and `interactive_terminal` retains
  synchronous I/O and presentation ownership. Interactive advisory content
  crosses only the public `StoryRuntime.converse_with_retrieval` boundary with
  the exact attachment-scoped memory service and a synchronous progress sink;
  repeated interactive advisory and consensus operations use the controller's
  one Runner, while unchanged direct Python one-step calls retain
  `StoryRuntime.converse` and noninteractive loop ownership remains outside the
  controller. Before an advisory coroutine enters, fixed attached cancellation
  requires public Runner quiescence proof and performs no provider or state/
  persistence mutation; after entry, it requires runtime `CancelledError` and
  operation join, while every uncertain state fails closed.
  Provider-free `/search` and `/transcript` inspection crosses only public
  `ConversationMemoryService.search` and
  `ConversationMemoryService.read_transcript` boundaries. Provider-free
  selected `/history` and the transcript portion of `/status` each cross one
  public `FilesystemConversationEventStore.read` boundary and derive context
  facts from that exact immutable tuple through the pure module-supported
  `select_bounded_conversation_context`; the controller never appends or calls
  event-store `read_context`. Ctrl-C during provider-free search, transcript,
  or history rendering stops further output without mutating those boundaries.
  Directives and safe inspection cross only public
  `StoryRuntime` methods. The controller performs no direct backend provider,
  context/result, retrieval-control, or artifact persistence.
  Selected-conversation consensus crosses only `StoryRuntime.run_prompt` or
  `run_draft`; draft-file bytes cross the internal
  `run_inputs.read_draft_file` boundary.
- The controller wires its resolved `ConversationCatalog` and one retained
  `FilesystemConversationEventStore` into both `StoryRuntime` and the
  attachment-scoped `ConversationMemoryService`, so injected and default
  catalogs remain the same authority for metadata and transcript identity. It
  prepares the destination service before attachment commit, rebinds it only
  with the committed attachment, and clears it on switch, end, cleanup, and
  final close; conversation selection does not rebind it. Search defaults to
  active conversations, while transcript identifiers resolve across active and
  archived conversations for explicit provider-free terminal reads. For agent
  retrieval, `StoryRuntime` first validates that service's exact current-session
  scope, forces active-only search, authorizes each read through a citation
  minted by an earlier same-turn search, and owns all memory-service calls. It
  returns trusted ordered sources; the controller alone renders their subject
  and stable conversation/turn/sequence/kind fields. Source and progress output
  is terminal-only and never enters the transcript.
- `conversation_retrieval` owns only immutable provider-neutral contracts,
  fixed policy, exact validators, canonical untrusted observation framing, and
  the synchronous `ConversationRetrievalActionHandler` protocol. Runtime owns
  the implementing handler and memory authority; backend consumes the protocol
  and never imports orchestration or a memory/storage port. This inversion keeps
  the graph acyclic and prevents private runtime reach-through.
- `Workspace` and `ConversationEventStore` implementations own persistence
  mechanics. `Workspace` exclusively owns the strict immutable debate-context
  sidecar schema and I/O. `StoryRuntime` captures included context once before
  reservation and sequences sidecar/result/state durability and typed debate
  progress. It sequences one-step advisory turns as before. For retrieval it
  holds the existing conversation lock across validated context capture, one
  durable user append, all provider/actions/memory calls, and exactly one final
  reply or sanitized runtime-error append. Retrieval actions, observations,
  progress, and sources have no persistence schema or artifact.
- `AgentBackend` owns the shared structured Codex subprocess, thread,
  per-call timeout, cancellation, diagnostic redaction, and taint lifecycle;
  `BoundAgent` is the immutable per-definition dispatch handle for debate,
  one-step conversation, and retrieval calls. The backend owns retrieval
  provider schemas, initial/continuation payloads, repeated calls, and framing
  handoff, while runtime owns every semantic, scope, budget, and memory decision.
- `ConversationCatalog` exposes logical identifiers and immutable metadata only;
  `FilesystemConversationCatalog` owns the fixed metadata layout through
  `Workspace` without exposing paths through that protocol.
- `ConversationEventStore` owns visible event append, authoritative read, and
  fixed-policy complete-turn context contracts. The provider-neutral
  `conversations.select_bounded_conversation_context` function owns pure
  validation and fixed-policy selection from one immutable authoritative
  snapshot; `FilesystemConversationEventStore` owns the fixed JSONL layout,
  locks, catalog validation, and transcript I/O through `Workspace`, and its
  locked `read_context` delegates only that pure selection.
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
  serialization additionally includes the session ID. A retrieval turn takes
  the runtime `(session, conversation)` lock before the backend
  `(session, backend, agent)` lock, and the backend holds its lock across the
  complete repeated-call loop and every handler invocation. Thus `invoke`,
  `converse`, and `converse_with_retrieval` cannot interleave one provider
  thread even when operations target different conversations.
- Existing `ConversationRequest`, `ConversationReply`, `ConversationResult`,
  `BoundAgent.converse`, and positional runtime/session `converse` contracts are
  unchanged. Retrieval uses separate additive request/reply/result/action types
  and methods. Existing debate and one-step provider payloads remain unchanged;
  provider-private thread history remains scoped by session, backend, and agent
  and can therefore span conversations.
- `models` and `prompts` retain their prior responsibilities, imports, public
  surfaces, and persistence/provider schemas. The context-selector extraction
  changes no persistence schema, dependency direction, event-store protocol,
  or package-facade export. Retrieval adds no dependency, migration, provider
  tool, streaming, retry, whole-turn deadline, cross-session/archive authority,
  or direct provider storage edge.
- `tests/conversation_event_store_contract.py` is reusable test-only adapter
  conformance support; it is not a production module or supported package API.
- Direct imports of internal configuration, prompt, workspace-constant, or CLI
  helpers in tests do not add them to the supported package API.
