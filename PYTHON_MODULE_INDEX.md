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

Importable production units indexed: 33.

## Current Dependency Direction

The current graph flows from entry points and orchestration toward provider,
configuration, prompt, persistence, and stable contract modules:

```text
__main__ -> cli -> interactive_controller -> interactive_controller._core
                 -> interactive_terminal -> interactive_terminal._core
                 -> run_inputs -> errors
                 -> orchestrator -> conversation_retrieval -> agents/conversation_search
                 |               |                           -> conversations/errors
                 |               -> conversation_search -> conversations/errors
                 |               -> prompts -> config -> workspace
                 |               |         |         -> conversations/models/errors/agents
                 |               |         -> conversations
                 |               |         -> workspace
                 |               -> conversations/workspace/models/agents
                 |               -> session_lifecycle
                 -> backend -> config/conversation_retrieval/workspace/models/errors/agents
                 |          -> session_lifecycle
                 -> cli_context -> workspace/errors/agents

interactive_controller._core -> interactive_controller._session_policy
                             -> interactive_controller._conversation_policy
                             -> interactive_controller._runtime_policy
                             -> agents/backend/cli_context
                             -> conversation_retrieval/conversation_search
                             -> conversations/errors/interactive_commands
                             -> interactive_settings/interactive_terminal
                             -> models/orchestrator/run_inputs
                             -> session_lifecycle/workspace
interactive_controller._session_policy -> errors/interactive_terminal/session_lifecycle
interactive_controller._conversation_policy -> conversation_search/conversations
                                            -> errors/interactive_terminal/workspace
interactive_controller._runtime_policy -> conversation_retrieval/conversation_search
                                       -> errors/interactive_terminal/models/workspace

smoke -> agents/conversation_retrieval/conversation_search/conversations/models/orchestrator/session_lifecycle/workspace
directives -> config/workspace/errors/agents
interactive_commands -> agents
interactive_line_editor -> interactive_commands/interactive_terminal
interactive_settings -> agents/config/errors/workspace
interactive_terminal -> interactive_terminal._core
interactive_terminal._core -> agents/interactive_commands
                           -> interactive_terminal._controls
                           -> interactive_terminal._driver
                           -> interactive_terminal._model
                           -> interactive_terminal._render
interactive_terminal._controls -> interactive_terminal._model
interactive_terminal._driver -> interactive_terminal._model
interactive_terminal._render -> interactive_terminal._model
story_writing_agents facade -> interactive_commands
                            -> conversation_retrieval -> agents/conversation_search
                            |                          -> conversations/errors
                            -> conversation_search -> conversations/errors
                            -> session_lifecycle
models -> conversations/agents/errors
session_lifecycle -> (standard library only)
workspace -> conversations -> agents/errors
```

Some modules directly consume lower-level contracts rather than following
every intermediate arrow. The current runtime graph is acyclic.

## Package and Module Entries

### `story_writing_agents`

- **Source:** `src/story_writing_agents/__init__.py`
- **Responsibility:** Provide the intentional package-level API facade.
- **Supported surface:** Exactly 120 names in `__all__`, grouped by owner:
  `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`, `LEAD_ORCHESTRATOR`,
  `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`, and `EXECUTION_SPECIALIST` from
  `agents`; `AgentBackend` and `BoundAgent` from `backend`; `DirectiveKind`,
  `RuntimeDirective`, `apply_directive`, and `parse_directive` from `directives`;
  `StoryWritingAgentsError`, `WorkspaceSecurityError`, `WorkspaceFormatError`,
  `WorkspaceVersionUnsupportedError`, `ConfigurationError`, `DirectiveError`,
  `SessionIdError`,
  `ConversationNotFoundError`, `ConversationExistsError`,
  `ManuscriptExistsError`, `BackendProtocolError`, `SessionExistsError`,
  `SessionNotFoundError`, `SessionPausedError`, `SessionAccessBlockedError`,
  `SessionOwnershipUnknownError`, `DebateExistsError`,
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
  `DebateProgressSink`, `DebateMessage`, `AgentStateEntry`,
  `PromptFileReference`, `AgentDefinition`, `AgentRequest`, `AgentReply`,
  `ConversationRequest`, `ConversationReply`, `ConversationResult`, and
  `DebateResult` from `models`; `SessionDurableState`, `SessionAccessState`,
  `SessionLifecycleState`, and
  `provider_work_is_ready` from `session_lifecycle`; `StoryRuntime`,
  `RuntimeSession`, `Runtime`, and `Session` from `orchestrator`; and
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`,
  `Workspace`, plus `initialize_workspace` from `workspace`.
  The re-exported `ActionKind` and `InteractiveAction` include additive search
  and transcript cases plus the optional positive `start_sequence` payload;
  their public names and import paths are unchanged.
- **Direct internal dependencies:** `agents`, `backend`,
  `conversation_retrieval`, `conversation_search`, `conversations`,
  `directives`, `errors`, `interactive_commands`, `models`, `orchestrator`,
  `session_lifecycle`, and `workspace`.
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
  and own provider-session durable state, process-access claims, provider
  thread persistence, and each backend instance's persisted configuration
  snapshot.
- **Supported surface:** Package-supported `AgentBackend` and `BoundAgent`.
  `BoundAgent` exposes structured debate `invoke` and one-turn conversation
  `converse` operations plus additive bounded
  `converse_with_retrieval(request, handler)`. In addition to provider binding
  and cleanup, `AgentBackend` exposes the exact lifecycle operations
  `get_session_lifecycle(session_id)`, `list_session_lifecycles()`,
  `validate_provider_configuration(session_id)`,
  `acquire_session_access(session_id)`, `release_session_access(session_id)`,
  `create_session_with_access(session_id)`,
  `reopen_session_with_access(session_id)`, and
  `pause_session_and_release_access(session_id)`.
  Unexported package-internal `backend_configuration_snapshot(backend)` exposes
  the same immutable construction-time configuration to the bound runtime
  without exposing the backend's private storage.
- **Direct internal dependencies:** `agents`, `config`,
  `conversation_retrieval`, `errors`, `models`, `session_lifecycle`, and
  `workspace`.
- **State, resources, and side effects:** Owns Codex subprocesses, temporary
  prompt/output staging, exact V2 provider-session manifests at
  `agent_states/orchestrator_state/provider_sessions/<session_id>.json`, exact
  V2 process-access claims at sibling `<session_id>.access.json` paths, the
  lazily created workspace-global `.access-claims.lock` OS advisory guard,
  in-memory claim tokens, persisted `provider_threads`, shared
  debate/conversation/retrieval per-thread locks, and structured Codex process
  and response validation. Manifests contain only `schema_version`,
  `session_id`, `durable_state`, `created_at`, `updated_at`, and
  `provider_threads`; claims contain only `schema_version`, `session_id`,
  `owner_hostname`, `owner_pid`, `claim_token`, and `claimed_at`. The guard
  serializes claim creation and proved-dead local replacement; live local,
  remote, inaccessible, unsupported-platform, and ambiguous ownership is
  handled conservatively, and release removes only the exact locally held
  token. Version-1 manifests, access sidecars, and access guards are rejected
  with `WorkspaceVersionUnsupportedError` without migration, deletion, or
  rewriting. The backend also owns
  timeout/cancellation taint handling, cleanup, and per-instance `atexit`
  registration. One retrieval thread lock covers the complete repeated-call
  action loop and synchronous
  handler invocations. The backend owns the exact Codex action schema, initial
  and continuation payloads, canonical observation framing handoff, provider
  timeout/cancellation behavior, incomplete-history taint, and fixed
  provider-value-free process/response-validation diagnostics. It drains both
  subprocess output streams but discards and never decodes stderr after a
  nonzero Codex exit; it receives no memory service and performs no retrieval
  search or transcript-storage call. Debate requests add the canonical
  included-context event list to provider payloads only when present; absent
  and fresh context requests retain the exact context-free payload. At
  construction it validates and snapshots the complete workspace system and
  per-agent configuration once and retains any non-null explicit model
  separately. Each bound Codex invocation resolves that explicit model over
  its agent model and then the legacy system model, while reasoning resolves
  from the agent and then the legacy system value; provider routes use their
  existing explicit-over-agent-over-Codex precedence. All values remain fixed
  for the backend lifetime. Optional
  `--model` and `--config model_reasoning_effort="LEVEL"` arguments are emitted
  in that order immediately after `exec` and before any resume marker; neither
  value changes provider-thread identity, bindings, fingerprints, manifests,
  or request payloads.
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
  not package-supported APIs. The lifecycle grammar is exactly
  `story session create SESSION`, `story session reopen [SESSION]`, and
  `story session pause [SESSION]`; those actions have no lifecycle `--session`
  option. Omitted reopen/pause targets use `default_session_id`; create and
  reopen save that default only after their ready/access transition succeeds,
  while pause retains it. Lifecycle success is printed after access release as
  sorted, indented JSON containing only `access_state`, `durable_state`, and
  `session_id`.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`, `config`,
  `errors`, `interactive_controller`, `interactive_terminal`, `models`,
  `orchestrator`, `run_inputs`,
  `session_lifecycle`, and `workspace`; imports `smoke` lazily for the
  diagnostic command.
- **State, resources, and side effects:** Reads terminal input and delegates
  caller-selected draft-file reads to `run_inputs`; writes JSON, plain-text
  interaction, or errors to standard streams; creates backends; constructs one
  unified stream-backed terminal through its package-internal factory after a
  stdin/stdout TTY guard; constructs the interactive controller; and runs the
  async event loop. The factory binds main and composition previews plus
  semantic confirmation input explicitly through one terminal driver, which
  retains the existing `input` callback as its unsupported-terminal fallback;
  subject input remains an ordinary read, while composition and confirmation
  use history-free semantic input. The controller owns interactive backend
  cleanup after its session-lobby run begins, while the CLI closes a backend if
  construction fails before that ownership transfer.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_cli_context.py`, `tests/test_smoke_cli.py`, and
  `tests/test_interactive_pty.py` for primary process-level POSIX PTY
  verification; `docs/reference/cli.md` and `docs/getting-started.md`.

### `story_writing_agents.cli_context`

- **Source:** `src/story_writing_agents/cli_context.py`
- **Responsibility:** Discover initialized workspaces and manage workspace-local
  session and conversation defaults.
- **Supported surface:** Internal `CliContext`, `resolve_workspace`,
  `load_cli_context`, `save_default_session`, `resolve_session`,
  `load_default_conversation`, and `save_default_conversation`.
  `resolve_session` accepts only the explicit `option_session` source and then
  the saved default; lifecycle positional resolution belongs to the CLI
  lifecycle dispatcher rather than this generic helper.
- **Direct internal dependencies:** `agents`, `errors`, and `workspace`.
- **State, resources, and side effects:** Reads workspace sentinels and owns
  exact V2 `agent_states/orchestrator_state/cli_context.json` with
  `schema_version` and `default_session_id`, plus exact V2 per-session objects
  under
  `agent_states/orchestrator_state/conversation_defaults/<session_id>.json`
  with `schema_version`, `session_id`, and `default_conversation_id`. Values are
  strictly read and atomically written through `Workspace`; a missing
  conversation-default file means no default and creates no storage. Old CLI
  context schemas and version-1 conversation-choice artifacts are rejected
  with `WorkspaceVersionUnsupportedError` without migration, deletion, or
  rewriting; malformed V2 data raises `WorkspaceFormatError`.
- **Primary verification/documentation:** `tests/test_cli_context.py`,
  `docs/reference/cli.md`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/design/session-lifecycle.md`.

### `story_writing_agents.config`

- **Source:** `src/story_writing_agents/config.py`
- **Responsibility:** Validate and resolve cascading runtime configuration.
- **Supported surface:** Internal `ConfigManager`, `SystemDefaults`,
  `AgentConfig`, `ResolvedAgentConfig`, immutable roster-complete
  `ConfigurationSnapshot`, and `KNOWN_AGENT_BACKENDS`.
  `SystemDefaults` retains its established positional fields and trailing
  optional `codex_model` and `codex_reasoning_effort` fields. `AgentConfig`
  likewise retains its first three positional fields and adds trailing optional
  per-agent `codex_model` and `codex_reasoning_effort` fields. `ConfigManager`
  owns both complete persisted schemas plus the shared safe-model and fixed
  reasoning-effort validators. Package-internal
  `validate_configuration` validates both persisted documents as one complete
  candidate, `load_configuration_snapshot` reads and publishes one immutable
  pair, and `resolve_from_snapshot` applies live runtime and explicit backend
  overrides over that pair. Existing on-demand loaders and `resolve` retain
  their signatures and delegate to the same validation and resolution rules.
- **Direct internal dependencies:** `agents`, `errors`, and `workspace`.
- **State, resources, and side effects:** Reads workspace configuration and
  owns no persistence.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `tests/test_prompts.py`, `tests/test_codex_backend.py`,
  `tests/test_interactive_settings.py`,
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
  `WorkspaceSecurityError`, `WorkspaceFormatError`,
  `WorkspaceVersionUnsupportedError`, `ConfigurationError`, `DirectiveError`,
  `SessionIdError`, `ConversationNotFoundError`, `ConversationExistsError`,
  `ManuscriptExistsError`, `BackendProtocolError`, `SessionExistsError`,
  `SessionNotFoundError`, `SessionPausedError`,
  `SessionAccessBlockedError`, `SessionOwnershipUnknownError`, and
  `DebateExistsError`, plus `ProgressObserverError` for synchronous debate
  progress-sink failures; and `ConversationRetrievalError` with fixed-message
  `ConversationRetrievalSemanticError`, `ConversationRetrievalBudgetError`, and
  `ConversationRetrievalObserverError` subclasses for retrieval authorization,
  whole-turn budget, and synchronous progress-observer failures.
  `WorkspaceVersionUnsupportedError` specializes `WorkspaceFormatError`; all
  three session transition/access failures specialize `BackendProtocolError`.
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
  aliases, and advisory-domain copy. The unlisted package-internal
  `CommandCompletionContext` and `project_command_completion` derive the
  canonical cursor-aware completion grammar, exact replacement spans, stable
  grammar-position identities, and ordered fixed candidates from the same
  command and route metadata without parsing. Agent-valued completion positions
  project exactly each route's preferred prompt alias in roster order, while
  all ordered aliases remain valid parser inputs and fuzzy agent suggestions
  present the preferred alias. Other parser helpers and command-alias maps are
  internal; agent parsing and canonical validation derive from the route
  registry. Search and transcript actions extend the
  frozen, slotted `InteractiveAction` with the optional positive
  `start_sequence` field;
  existing action payloads remain compatible. `/search QUERY` and
  `/transcript ID [COUNT] [--from SEQUENCE]` participate in the same immutable
  help registry and exact parser. Global no-argument `/settings` is represented
  by the additive `SETTINGS` action and the same help/completion grammar.
  Story-session parsing recognizes exactly
  `/session create NAME`, `/session open [NAME]`, `/session pause`, and
  `/session list`, with no old lifecycle verbs or aliases. The package facade
  does not re-export the route metadata.
- **Direct internal dependencies:** `agents`.
- **State, resources, and side effects:** Immutable command and agent-route
  metadata only. Completion projection is pure and call-local; the module owns
  no editor, palette, history, process state, or external resource and performs
  no provider, filesystem, persistence, environment, or import-time
  operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_commands.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_public_api_and_cli.py`, and
  `docs/reference/interactive-command-parser.md`.

### `story_writing_agents.interactive_controller`

- **Source:** `src/story_writing_agents/interactive_controller/__init__.py`
- **Responsibility:** Provide the stable interactive-controller import boundary.
- **Supported surface:** Exactly `InteractiveController` in `__all__`, re-exported
  from `_core` as the same class object. Its canonical `__module__` remains
  `story_writing_agents.interactive_controller`; the project-root package
  facade does not export it or any private controller unit.
- **Direct internal dependencies:** `interactive_controller._core`.
- **State, resources, and side effects:** Owns no runtime state or external
  resource. Import deterministically canonicalizes the defining class's
  `__module__` and performs no filesystem, provider, environment, thread, or
  application-startup operation.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_public_api_and_cli.py`, and `docs/design/overview.md`.

### `story_writing_agents.interactive_controller._core`

- **Source:** `src/story_writing_agents/interactive_controller/_core.py`
- **Responsibility:** Own one synchronous interactive shell's state and
  orchestration lifecycle.
- **Supported surface:** Package-internal defining `InteractiveController`,
  re-exported only through `story_writing_agents.interactive_controller`, whose
  constructor accepts explicit workspace, CLI-context, backend, and terminal
  collaborators plus an optional keyword-only `ConversationCatalog`, and whose
  single-use `run_session_lobby` operation returns process status 0 or 2. An
  omitted catalog uses `FilesystemConversationCatalog`. The shell dispatches
  the exact lifecycle commands `/session create`, `/session open`,
  `/session pause`, and `/session list`; provider-free global status; attached
  runtime directives and safe session/debate inspection; current-focus
  conversation history; roster,
  sticky-target, current-session search, and bounded transcript-read actions;
  global attached-or-unattached persistent settings editing;
  current-focus advisory turns; and current-focus consensus
  prompt, fresh-prompt, and draft-file actions while retaining the established
  conversation commands. Search, transcript reads, directives, and inspection
  require an attachment but no current conversation; history and every
  consensus action require both an attachment
  and current focus. `/status` and `/session list` are lifecycle-read-only:
  status renders the canonical session record before separate detail records,
  and list orders rows by newest durable update and then ID without exposing
  owner host, PID, or claim token. Status record order is `Session`,
  `Durable state`, `Process access`, `Default session`, `Current attachment`,
  `Default conversation`, `Current focus`, `Agent focus`,
  `Provider readiness`, and `Next action`; list rows use the lifecycle subset
  through `Default session`, then `Provider readiness` and `Next action`.
  All transition, identity-resolution, routing,
  directive-result, inspection, trusted debate/retrieval progress and result,
  retrieval-source, and other presentation validation helpers are package
  internal, and the project-root facade does not re-export this class or module.
  The controller constructs closed semantic components for the migrated
  high-frequency and information-dense scope, status, inspection, management,
  directive, routing, advisory, retrieval, consensus, focused-history, list,
  search, and transcript records through the terminal facade's unlisted
  `InteractiveSemanticOutput`; it retains that adapter alongside the same
  terminal used for input and terminal-owned semantic writers. Advisory turns
  retain the facade's unlisted `InteractiveAdvisoryProgress` adapter and wrap
  exactly the runtime awaitable instead of emitting persistent routing and
  waiting notices; the controller owns no indicator glyph, cursor control, or
  animation task. It also retains the facade's resource-free committed-main
  cleanup, picker, and cancellable-prompt adapters for the call-local settings
  flow. Only a parsed `/settings` action consumes the correlated cleanup before
  dispatch; the controller owns no cursor sequence or terminal geometry.
  Retrieval results and persisted `agent_reply`
  history/transcript rows use the facade's pure `format_agent_reply_content`
  projection and agent semantic role without changing the stored event or
  non-agent content. Ordered session,
  conversation, search, transcript, and focused-history rows use one positioned
  `ListView` containing one `Record` per established callback, so the controller
  owns result counts and interruption boundaries without any remaining
  `write_status` or specialized dense-row call.
  The settings editor exposes the exact six-row top level Language, Persona,
  Maximum debate rounds, Providers, Save, and Cancel; Providers selects one
  roster agent and then its Provider, Model, or Reasoning effort without
  changing the active backend or runtime snapshot. Editors contain no repeated
  Current/Pending context panel. Explicit Save and Cancel actions each use one
  transient history-free confirmation whose context contains only changed
  Current/Pending properties in stable settings and roster order; each changed
  value has an explicit text cue and warning role, and an unchanged candidate
  has an explicit no-changes notice. Invalid confirmation guidance remains in
  that transient context. No, blank, or nested Escape returns to the settings
  menu, while interrupt or EOF preserves whole-flow cancellation.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`,
  `conversation_retrieval`, `conversation_search`, `conversations`, `errors`,
  `interactive_commands`, `interactive_controller._conversation_policy`,
  `interactive_controller._runtime_policy`,
  `interactive_controller._session_policy`, `interactive_settings`,
  `interactive_terminal`, `models`, `orchestrator`, `run_inputs`,
  `session_lifecycle`, and `workspace`.
- **State, resources, and side effects:** Instances retain borrowed workspace,
  CLI-context, backend, and terminal collaborators plus either a borrowed
  injected catalog or a controller-constructed default filesystem catalog. It
  constructs and retains one `StoryRuntime` and one
  `FilesystemConversationEventStore` bound to the resolved catalog. It shares
  that exact event-store instance with the runtime and the attachment-scoped
  `ConversationMemoryService`. The memory service is absent in lobby state,
  constructed for a validated destination before attachment commit, rebound on
  a successful switch, and cleared on pause, cleanup, and final close. The
  controller also owns its current attached-session identifier, current-focus
  conversation metadata, sticky target, current CLI-context snapshot,
  process-local per-session latest accepted STARTED debate-ID map, and
  one-run/closed state. Once its single-use run starts, it constructs and owns
  one private `asyncio.Runner` for the rest of that lifecycle. Repeated
  `Runner.run` calls execute every accepted advisory and consensus coroutine on
  that Runner; controller-local entry/cancellation/join evidence and public
  Runner task inspection distinguish a quiescent pre-entry interruption, a
  completed entered cancellation, and an unjoined or otherwise uncertain
  operation. Final cleanup closes the Runner and its residual asynchronous
  work, releases any still-known process access, and then closes the backend.
  Later cleanup stages remain attempted after an earlier failure; ordinary
  cleanup failures force status 2 and one best-effort `cleanup_failed` diagnostic,
  while a cleanup `BaseException` propagates after later stages run and an
  unsuccessfully interrupted Runner close retains its private reference.
  `AgentBackend` retains the actual access claim, manifest, subprocess, and
  provider-thread resources. The settings flow retains only one call-local
  immutable baseline and pending candidate, constructs its changed-only
  confirmation context, and delegates exact-byte conflict detection and guarded
  persistence to `interactive_settings`; cancellation or failed saves do not
  mutate the active backend or runtime snapshot. Through public collaborators
  the controller reads and parses terminal input, submits typed presentation
  facts and invokes
  terminal-owned semantic writers, sequences session and nested
  conversation transitions, performs
  configuration validation, pre-read, access acquisition, and correlated
  post-read in that exact order, and compensates only access newly acquired by
  a failed transition. Create/open update the persisted session default only
  after success; pause retains defaults, changes durable state, releases
  access, and returns to the lobby. Quit, EOF, and final cleanup release access
  without pausing. Persisted conversation defaults remain distinct from the
  in-memory current focus.
  The controller also performs
  catalog create/list/get/rename/archive and compensating unarchive calls,
  persists session and conversation defaults through `cli_context`, applies
  exact existing directives and reads whitelisted session/debate inspection
  through `StoryRuntime`,
  performs active-only search and fresh bounded active-or-archived transcript
  reads through `ConversationMemoryService`, reads the authoritative event
  store exactly once for each current-focus history or status snapshot, derives
  canonical context facts through `select_bounded_conversation_context`, and
  runs one synchronous
  `StoryRuntime.converse_with_retrieval` operation per accepted advisory content
  turn through the retained Runner and terminal-owned progress adapter. It
  passes the exact attachment-scoped
  service plus a synchronous sink, validates trusted progress/results, and
  constructs fixed progress and ordered source components through the retained
  semantic-output adapter. Before advisory coroutine entry, the established
  fixed cancelled/hint copy returns attached only when public Runner task
  inspection proves quiescence; that path calls no provider and mutates no
  controller or persistent state. After entry, returning attached requires the
  runtime to receive `CancelledError` and the operation to join; every other
  advisory cancellation state fails closed. Provider-free search, transcript,
  and focused-history interruption stops further rendering and returns to the
  same prompt without persistence mutation. The controller also delegates
  draft reads to `run_inputs` and submits each accepted consensus action
  through the same Runner. A trusted `INTERRUPTED` progress event, which may be
  first after reservation, is its sole durable-interruption proof; only `STARTED` updates
  the process-local latest-debate map, pre-entry cancellation remains
  provider-free, and an entered cancellation without proof fails closed through
  `operation_interrupted` and `cleanup_failed`. `StoryRuntime` owns context
  capture, retrieval authorization and memory access, transcript/provider and
  consensus sequencing, validated results, artifact durability, and its async
  caches and locks; the event store, catalog, and `Workspace` retain persistence
  ownership. The controller's only direct event-store operation is the public
  read used for fixed focused history/status snapshots; it never appends or
  calls `read_context`, dispatches a backend provider directly, or persists
  retrieval controls/progress/sources, debate context/results, or artifacts,
  and has no import-time operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_interactive_management.py`, `tests/test_interactive_consensus.py`,
  `tests/test_interactive_conversation.py`,
  `tests/test_interactive_cancellation.py`, `tests/test_session_lifecycle.py`,
  `tests/test_cli_context.py`, `tests/test_interactive_commands.py`,
  `tests/test_interactive_terminal.py`,
  `tests/test_interactive_search.py` for real-filesystem shell search and
  transcript-read integration, and `tests/test_interactive_pty.py` for primary
  process-level POSIX PTY lifecycle verification;
  `docs/design/session-lifecycle.md`,
  `docs/reference/cli.md`, `docs/reference/interactive-terminal.md`, and
  `docs/reference/conversation-retrieval.md` verify the current collaborator,
  lifecycle, and retrieval-presentation contracts.

### `story_writing_agents.interactive_controller._session_policy`

- **Source:** `src/story_writing_agents/interactive_controller/_session_policy.py`
- **Responsibility:** Interpret lifecycle facts for interactive session
  decisions.
- **Supported surface:** No project-public import. Its exact package-internal
  `__all__` contains immutable `RECOVERABLE_SESSION_ERROR_CODES` plus
  `classify_session_failure`, `next_session_action`,
  `require_correlated_state`, `require_lifecycle_state`,
  `require_ready_access_state`, and `require_released_state`. These pure
  functions validate exact lifecycle identities, transition correlations,
  access release, fixed next-action copy, and safe exception-to-error mapping.
- **Direct internal dependencies:** `errors`, `interactive_terminal`, and
  `session_lifecycle`.
- **State, resources, and side effects:** Immutable policy metadata only; owns
  no process state or resource and performs no I/O, lifecycle transition,
  mutation of supplied values, or import-time operational side effect.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_session_lifecycle.py`, `docs/design/session-lifecycle.md`, and
  `docs/design/overview.md`.

### `story_writing_agents.interactive_controller._conversation_policy`

- **Source:** `src/story_writing_agents/interactive_controller/_conversation_policy.py`
- **Responsibility:** Interpret conversation catalog and memory-service facts.
- **Supported surface:** No project-public import. Its exact package-internal
  `__all__` contains the typed `ConversationCatalogProtocolFailure`,
  `ConversationInputFailure`, and `ConversationResolutionFailure` markers plus
  `classify_conversation_failure`,
  `classify_memory_catalog_or_reference_failure`,
  `classify_search_service_failure`, `classify_transcript_service_failure`,
  `normalize_conversation_input`, `resolve_conversation_reference`,
  `validate_catalog_entries`,
  `validate_catalog_metadata`, `validate_conversation_reference`,
  `validate_search_result_handles`, `validate_transcript_page`, and
  `visible_conversation_handle`. Reference validation remains pure and
  precedes the core-owned catalog read; all collection/result inputs are
  interpreted without mutation or presentation formatting.
- **Direct internal dependencies:** `conversation_search`, `conversations`,
  `errors`, `interactive_terminal`, and `workspace`.
- **State, resources, and side effects:** Immutable minimum-prefix policy and
  export metadata only; owns no catalog, memory service, process state, cache,
  or external resource and performs no filesystem, terminal, provider, or
  import-time operational I/O.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_interactive_conversation.py`,
  `tests/test_interactive_management.py`, `tests/test_interactive_search.py`,
  `docs/reference/interactive-terminal.md`, and `docs/design/overview.md`.

### `story_writing_agents.interactive_controller._runtime_policy`

- **Source:** `src/story_writing_agents/interactive_controller/_runtime_policy.py`
- **Responsibility:** Interpret runtime progress, result, and inspection facts.
- **Supported surface:** No project-public import. Its exact package-internal
  `__all__` contains the frozen, slotted `DebateContextInspectionFacts` and
  `DebateInspectionFacts` contracts plus `classify_advisory_turn_failure`,
  `classify_consensus_execution_failure`, `classify_inspection_failure`,
  `classify_progress_callback_failure`,
  `interactive_code_for_debate_failure`, `validate_consensus_result`,
  `validate_conversation_retrieval_progress`,
  `validate_conversation_retrieval_result`,
  `validate_debate_context_inspection`, `validate_debate_inspection`,
  `validate_debate_progress`, and `validate_session_inspection`. Validators
  reconstruct exact immutable contracts, consume only a previous event or
  immutable progress tuple, and return canonical typed facts. Debate and
  context inspection validators return the whitelisted fact contracts without
  flattening presentation text; the module never renders, appends, retains, or
  calls the runtime.
- **Direct internal dependencies:** `conversation_retrieval`,
  `conversation_search`, `errors`, `interactive_terminal`, `models`, and
  `workspace`.
- **State, resources, and side effects:** Export metadata only; owns no runtime,
  progress collection, controller state, cache, or external resource and
  performs no terminal, filesystem, provider, lifecycle, or import-time
  operational I/O.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_interactive_consensus.py`,
  `tests/test_interactive_cancellation.py`,
  `tests/test_interactive_search.py`,
  `docs/reference/conversation-retrieval.md`, and `docs/design/overview.md`.

### `story_writing_agents.interactive_line_editor`

- **Source:** `src/story_writing_agents/interactive_line_editor.py`
- **Responsibility:** Preserve the standalone contextual line-editor
  compatibility surface over the unified driver.
- **Supported surface:** Module-supported frozen, slotted
  `SlashCommandSuggestion`, pure `slash_command_suggestions`, and
  `InteractiveMainLineEditor`. Suggestions project canonical tokens and aliases
  directly from `interactive_commands.COMMANDS` in registry order, retain each
  alias's canonical identity, exclude the three main-unavailable composition
  tokens, and match exact case-sensitive whole-buffer prefixes without parsing.
  `InteractiveMainLineEditor.read_line` returns one unchanged Python Unicode
  code-point buffer or raises the established interrupt/EOF events. Unexported
  project-internal `composition_slash_command_suggestions` projects only
  `/send` then `/cancel` from exact whole-buffer prefixes, while
  `bind_suggestion_reader` binds an explicit provider to an editor without
  exposing private members to consumers. Public `read_line` uses the driver's
  shared history-aware selectable main-command route while preserving the
  legacy `slash_command_suggestions` projection, and the bound contextual
  reader remains history-free and nonselectable. The public `__all__` remains
  exact.
- **Direct internal dependencies:** `interactive_commands` and
  `interactive_terminal`.
- **State, resources, and side effects:** The facade retains one unified raw
  driver but contains no raw-mode, key-decoding, cursor, width, write, flush, or
  restoration implementation. Construction performs no stream access or
  terminal mutation. That driver owns one bounded process-local main-input
  history store for the editor lifetime; public `read_line` records successful
  exact nonblank commits, while the internal binding supplies a provider to a
  history-free contextual read without retaining it. The unified driver
  preserves exact fallback, selectable-palette, editing, interrupt, EOF,
  output-failure, and restoration behavior. The history is not persisted, and
  the facade performs no provider, filesystem, persistence, subprocess,
  network, or import-time operational side effect.
- **Primary verification/documentation:** `tests/test_interactive_line_editor.py`,
  `tests/test_interactive_terminal.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_public_api_and_cli.py`, and `tests/test_interactive_pty.py`;
  `docs/reference/interactive-terminal.md`, `docs/reference/cli.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`.

### `story_writing_agents.interactive_settings`

- **Source:** `src/story_writing_agents/interactive_settings.py`
- **Responsibility:** Own immutable interactive-settings candidates and guarded
  persistence.
- **Supported surface:** Package-internal frozen/slotted `PersonaCatalogEntry`,
  `SettingsBaseline`, and `SettingsState`; fixed `SettingsErrorKind` and
  redacted `SettingsError`; the exact ordered `SETTINGS_LANGUAGES` and
  `SETTINGS_REASONING_EFFORTS`; `load_settings_state`, `update_language`,
  `update_persona`, `update_max_debate_rounds`, `update_agent_backend`,
  `update_agent_codex_model`, `update_agent_codex_reasoning_effort`, and
  `save_settings_state`. The module has no package-facade export.
- **Direct internal dependencies:** `agents`, `config`, `errors`, and
  `workspace`.
- **State, resources, and side effects:** Loading captures the exact strict-
  UTF-8 bytes of both persisted configuration documents, one fully validated
  immutable configuration snapshot, and a safe lexical persona catalog with
  top-level-template precedence. Pure update operations replace only the
  pending immutable candidate, including one agent's provider, model, or
  reasoning value at a time. Legacy system model/reasoning values remain
  unchanged fallback data rather than editable settings. Saving completely
  validates that candidate,
  rereads both documents and the catalog for exact stale-state detection, and
  performs no write when the candidate is unchanged. Changed documents are
  serialized canonically and atomically replaced through `Workspace` in system-
  then-agent order; any attempted targets are restored from exact baseline text
  in reverse order after a write failure, all restorations are attempted, and a
  restoration failure produces the fixed rollback category. The module owns no
  terminal, controller, backend, runtime, provider, network, subprocess, or
  import-time operational resource.
- **Primary verification/documentation:** `tests/test_interactive_settings.py`,
  `docs/design/interactive-cli-design-system.md`,
  `docs/reference/configuration/agent-configuration.md`, and
  `docs/operations/filesystem-safety.md`.

### `story_writing_agents.interactive_terminal`

- **Source:** `src/story_writing_agents/interactive_terminal/__init__.py`
- **Responsibility:** Provide the stable interactive-terminal import and
  construction boundary.
- **Supported surface:** Package-supported `AttachmentKind`, `ComposeMode`,
  `ConfirmationResult`, `TerminalActionKind`, `TerminalMessageKind`,
  `InteractiveErrorCode`, `TerminalAction`, `InteractiveErrorSpec`,
  `INTERACTIVE_ERROR_SPECS`, `InteractiveError`, `sanitize_terminal_text`, and
  `InteractiveTerminal` in the exact established `__all__` order. The facade
  exports the defining objects without wrapper subclasses and preserves their
  canonical `story_writing_agents.interactive_terminal` identities. The
  unlisted `InteractiveSemanticOutput` binds one terminal's semantic write
  capability for controller use, and the existing `Field`, `ListView`,
  `Message`, `Notice`, `Panel`, `Record`, `Section`, `SemanticRole`, and
  `ViewState` contracts are likewise facade-bound but omitted from `__all__`.
  These bindings support count-aware dense semantic views without changing the
  package-supported surface. The adapter and contracts add no project-root
  export or raw-resource ownership. Unlisted package-internal
  `format_agent_reply_content` shares the pure strict-JSON agent-reply
  presentation projection with controller-owned semantic records without
  becoming a supported public export. Its
  unlisted package-internal `build_stream_terminal` factory is the sole real-CLI
  construction boundary and binds trusted semantic input rendering to the sole
  raw driver without exposing renderer or driver details. Unlisted
  `build_contextual_terminal` preserves the completed contextual-callback seam.
  Unlisted `MainInputHistoryState`,
  `MainInputControlState`, `append_main_input_history`, and
  `transition_main_input` bindings expose the internal pure history seam to the
  standalone compatibility facade without adding root-package exports.
  Unlisted `transition_main_command_input` and `read_main_command_line`
  bindings provide the shared selectable main-command control and driver
  adapters without exposing a terminal leaf. Public
  construction adapts `read_line` to deterministic plain semantic composition
  and confirmation prompts, while internal construction binds distinct
  ordinary, history-aware main, and history-free semantic composition and
  confirmation readers without prompt inspection. Unlisted
  `InteractivePickerInput`, `Choice`, `Prompt`, `ControlOutcome`, and
  `ControlOutcomeKind` bindings provide one resource-free generic picker input
  boundary without exposing opaque choice identities, terminal leaves, or a
  new package-supported API. Unlisted `InteractivePromptInput` binds one
  resource-free semantic cancellable-line capability for nested settings input;
  callback and stream factories respectively provide deterministic plain and
  sole-driver-backed implementations without changing the exact public
  `__all__`. Both unlisted input adapters accept an opt-in call-local transient
  lifecycle used by settings: capable stream construction delegates display
  clearing to the sole driver, while callback construction omits synthetic
  picker final frames it cannot erase. Their default route remains unchanged.
  Unlisted `InteractiveMainCommitCleanup` binds a resource-free, one-shot
  committed-main display cleanup capability for controller use. Callback
  construction binds a no-op, while stream construction delegates correlated
  row ownership and fixed cursor cleanup to the sole driver; neither the class
  nor its operation expands the exact public `__all__` or `InteractiveTerminal`
  method surface.
  Unlisted `InteractiveAdvisoryProgress` and `AdvisoryProgressError` bind one
  advisory-only async progress lifecycle through the same facade: callback and
  incapable terminals await silently, while stream construction injects the
  pure fixed-glyph renderer into the sole driver. They do not expand the public
  `__all__` or expose terminal leaves to the controller.
- **Direct internal dependencies:** `interactive_terminal._core`.
- **State, resources, and side effects:** Owns no terminal resource or mutable
  process state. Import deterministically canonicalizes supported public object
  module identities and performs no stream, capability, filesystem, provider,
  persistence, subprocess, network, or application-startup operation.
- **Primary verification/documentation:** `tests/test_interactive_terminal.py`,
  `tests/test_interactive_line_editor.py`, `tests/test_public_api_and_cli.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_views.py`, `tests/test_interactive_pty.py`,
  `docs/design/overview.md`,
  `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`; `tests/test_interactive_commands.py`
  and `docs/reference/interactive-command-parser.md` verify the consumed
  command registry contract.

### `story_writing_agents.interactive_terminal._model`

- **Source:** `src/story_writing_agents/interactive_terminal/_model.py`
- **Responsibility:** Define immutable semantic terminal contracts.
- **Supported surface:** Package-internal semantic roles, explicit view states,
  capability profiles, normalized keys, control outcomes, terminal
  capabilities, line-input state, immutable `ComposerState`, bounded
  `MainInputHistoryState`, correlated `MainInputControlState`,
  `SlashCommandSuggestion`, internal `CommandCompletionCandidate`,
  `CommandCompletionProjection`, `MainCommandPaletteState`, and correlated
  `MainCommandControlState`, internal `PickerControlState` and bounded
  `PickerViewport` transfer values, the internal rejected control outcome, the
  closed
  `Prompt`, `Panel`, `Section`, `Field`, `Notice`, `Message`, `Record`,
  `ListView`, `SuggestionPalette`, and `Picker` component vocabulary, shared
  `Choice`, `RenderedFrame`, internal `InputPromptView` and
  `RenderedInputFrame` transfer contracts outside the closed component
  vocabulary, terminal-text sanitization, and deterministic physical-cell
  measurement.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Immutable values and pure validation
  only. Main-input values correlate one editable line, navigation position,
  saved draft, and overlay flag and bound stored entries to 100 without
  retaining or persisting them. Command-completion values correlate grammar
  position, replacement literal and span, highlighted candidate, pre-change
  buffer/cursor snapshot, and restored-state latch without owning projection or
  I/O. Picker-control values retain one validated semantic picker and its
  call-local window origin, while viewport values carry one contiguous
  at-most-seven-choice slice; stable choice identities remain internal
  correlation keys. Composer values retain exact accepted lines
  and derive joined content and code-point counts without mutation. Models
  create no ANSI and own no callbacks, streams, rendering calculation, runtime,
  provider, filesystem, or storage handles; viewport and rendered-input
  transfer values only carry pure precomputed bounds or renderer-produced
  trusted/plain projections and perform no operational I/O.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_views.py`, and
  `docs/design/interactive-cli-design-system.md`; focused component, renderer,
  control, capability, and compatibility coverage belongs to the interactive
  terminal test suites.

### `story_writing_agents.interactive_terminal._controls`

- **Source:** `src/story_writing_agents/interactive_terminal/_controls.py`
- **Responsibility:** Apply pure interactive-control transitions.
- **Supported surface:** Package-internal `transition_line_input` for existing
  insertion, deletion, cursor, commit, interrupt, and EOF semantics, plus
  `transition_prompt_input`, which reuses those line semantics while returning
  a distinct cancel outcome for Escape without history or completion, plus
  `project_slash_suggestions` for exact registry-supplied prefix projection and
  `project_composition_suggestions` for fixed ordered `/send`, `/cancel`
  preview projection. `append_main_input_history` retains exact nonblank
  commits with oldest-first eviction at 100, while `transition_main_input`
  performs pure newest/older/newer history navigation, draft restoration, and
  overlay-open arrow suppression. `project_main_suggestion_palette` turns one
  correlated completion projection into the existing semantic palette, while
  `transition_main_command_input` owns palette open/filter/highlight/clamp,
  Tab replacement and snapshot, Escape restoration and latch, and the exact
  open-palette versus closed-history arrow routing. `transition_composer_line`
  applies exact `/send`, `/cancel`, `/say /send`, and `/say /cancel` semantics
  to immutable composer state, while `transition_confirmation_answer`
  classifies canonical yes/no/default answers and explicit rejection.
  `initialize_picker`, `reconcile_picker`, `transition_picker`,
  `transition_picker_fallback`, and `project_picker_viewport` own the pure
  generic single-select lifecycle: deterministic enabled highlight selection,
  stable-identity refresh, disabled-row skipping with clamped navigation,
  minimal at-most-seven-row window movement, exact numbered or blank fallback
  commit, `/cancel`, and one semantic rejected-attempt notice. Choice identities
  are correlation-only and never become presentation text. Composition
  suggestions remain preview-only and nonselectable.
- **Direct internal dependencies:** `interactive_terminal._model`.
- **State, resources, and side effects:** Pure value projections only. The
  functions return new immutable history/control values and retain no store;
  they own no parser, persistence, runtime, provider, stream, terminal mode,
  cursor resource, filesystem, process, or network resource.
- **Primary verification/documentation:**
  `tests/test_interactive_line_editor.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/reference/interactive-command-parser.md`.

### `story_writing_agents.interactive_terminal._render`

- **Source:** `src/story_writing_agents/interactive_terminal/_render.py`
- **Responsibility:** Render semantic terminal components safely.
- **Supported surface:** Package-internal deterministic responsive component
  rendering for structured plain and fixed-theme ANSI profiles, exact removal
  of the allowed decorative SGR vocabulary, semantic state labels, physical-cell
  wrapping, narrow-layout stacking, explicit empty/selection cues, and trusted
  semantic input rendering with equivalent plain and styled projections.
  `render_picker_viewport` renders at most seven numbered live choices with
  absolute position/range facts, simultaneous selected and highlighted cues,
  and an ordinal/label-only selected-outside-window summary;
  `render_picker_fallback` renders the complete numbered list and semantic input
  prompt. Both keep opaque identities internal and preserve equivalent
  sanitized plain and fixed-ANSI meaning. `format_agent_reply_content`
  recognizes only a complete strict JSON object or array, rejects duplicate
  object keys and non-finite constants, and preserves key order and Unicode in
  a two-space-indented projection; malformed JSON, scalars, embedded prose, and
  every other string return exactly unchanged. The fixed `AGENT` role is bold
  terminal-default foreground with the same plain meaning and local reset.
  `render_advisory_progress_frame` projects the fixed one-cell `◐`, `◓`,
  `◑`, and `◒` cycle without ANSI, cursor controls, dynamic content, or
  state.
- **Direct internal dependencies:** `interactive_terminal._model`.
- **State, resources, and side effects:** Pure projections only. Dynamic text
  is sanitized before wrapping or fixed styling; the module writes and flushes
  no stream, emits no OSC, query, background, dim, blink, or cursor sequence,
  and owns no environment, terminal, runtime, provider, filesystem, or storage
  state.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_views.py`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`; focused rich/plain/narrow/wide coverage
  belongs to the interactive terminal test suites.

### `story_writing_agents.interactive_terminal._driver`

- **Source:** `src/story_writing_agents/interactive_terminal/_driver.py`
- **Responsibility:** Own stream-backed terminal interaction.
- **Supported surface:** Package-internal `RawTerminalDriver` and
  `detect_terminal_capabilities`. The driver accepts pure line, main-input, and
  history-append collaborators at construction and explicit suggestion or
  completion/palette collaborators per live read. `read_main_line` preserves
  the history-aware compatibility route; package-internal supported
  `read_main_command_line` performs selectable history-aware main editing,
  `read_live_line` remains the history-free contextual compatibility route,
  `read_input_line` accepts an injected trusted semantic renderer for
  history-free composition and confirmation, and `read_line` preserves ordinary
  fallback. Supported package-internal `read_prompt` accepts an injected
  trusted semantic renderer and pure transition for one cancellable, history-
  free settings line; exact Escape cancels in raw or line-fallback mode, and
  interrupt and EOF remain distinct outcomes. Supported package-internal
  `read_picker` accepts pure call-local
  transitions, viewport projection, and live/fallback renderers for a dedicated
  generic single-select lifecycle that does not reuse editable-line state. The
  driver provides one-write output, redraws on the full immutable main-command
  or picker control state, and normalizes established editing and picker keys.
  Supported package-internal `clear_main_commit_display` consumes at most one
  live main submission correlated only after restoration and successful history
  append; it clears the exact committed rows without adding a line break.
  Its optional per-read transient flag clears capable picker and cancellable
  prompt rows on completion or cancellation before returning the typed outcome;
  line fallback cannot erase and instead omits the duplicate picker final
  frame. The default retains the established final-frame behavior. Supported
  package-internal `run_advisory_progress` accepts one awaitable and injected
  fixed-glyph renderer, awaits silently without raw cursor capability, and on a
  capable stream joins the operation with one indicator task. Internal
  `AdvisoryProgressError` distinguishes indicator failure from provider failure
  for controller cleanup.
- **Direct internal dependencies:** `interactive_terminal._model`.
- **State, resources, and side effects:** Sole owner of borrowed input/output
  streams, independent capability checks, detected width, normalized physical
  keys, raw POSIX attributes, functional cursor controls, live redraw writes,
  flushes, and exact restoration. It retains exactly one bounded process-local
  main-input history store per driver instance; each main read resets only its
  call-local navigation state, and a successful fallback commit or restored
  live commit is appended once. It also retains at most one nonpersistent live
  main-display cleanup token; fallback reads create none, every intervening
  input or output invalidates it, and only its explicit one-shot operation emits
  the fixed functional controls. The same read loop owns call-local one-byte
  incremental decoding and the fixed 50-millisecond Escape readiness lookahead,
  so available ordinary bytes are reprocessed without a second reader or sleep
  loop. Completion, prompt-control, and palette collaborators are call-local and
  retain no prompt mode. Composition, confirmation, and ordinary reads
  never navigate or mutate that store. Cancellable prompt and picker input
  likewise never consult or mutate history or completion state; their live
  paths reuse the sole driver's decoding, raw-mode, cursor, write, flush, and
  restoration lifecycle. Picker input observes width again for each redraw,
  and its safe line fallback renders the complete numbered choices. Semantic
  input resolves capability and
  width once per read, uses the plain projection for fallback and cursor
  measurement, and emits the trusted styled projection only on its live path.
  Construction performs no stream access or terminal mutation, and history is
  never persisted. During one advisory operation the same driver alone retains
  the active frame and renderer, creates and joins the call-local animation
  task on the controller's existing event loop, serializes persistent writes by
  clearing and redrawing the indicator, cancels and joins the peer task after a
  worker or operation failure, and clears all indicator state before returning.
  It creates no OS thread or import-time task. Missing, uncertain, non-TTY,
  non-POSIX, or `TERM=dumb` capability safely uses ordinary line input, while
  `NO_COLOR` affects decorative SGR only.
- **Primary verification/documentation:**
  `tests/test_interactive_line_editor.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_pty.py`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`.

### `story_writing_agents.interactive_terminal._core`

- **Source:** `src/story_writing_agents/interactive_terminal/_core.py`
- **Responsibility:** Coordinate public interactive-terminal operations.
- **Supported surface:** Defines every supported terminal enum, frozen/slotted
  action and error value, immutable error registry, and `InteractiveTerminal`
  re-exported by the package facade. Constructor and public method signatures,
  validation, typed input outcomes, established plain record contents/order,
  error redaction/caps, composition behavior, callback granularity, and fixed
  callback-width semantics remain compatible. Its package-internal stream
  factory coordinates the driver, controls, command metadata, and current
  capability profile. Lobby, attached-session, and conversation-scope
  orientation plus specialized run, agent, typed-error, help, agent-roster,
  conversation-list, search-hit, and transcript-event writers construct
  semantic components; `InteractiveSemanticOutput` binds the same private
  component writer so controller-owned components share the identical
  render-and-one-write path. The specialized agent writer applies the pure
  strict-JSON reply projection before constructing its semantic message, and
  the core binds that formatter through the facade for controller-owned
  retrieval and persisted reply records. Composition opening, rejection, send,
  and discard events plus invalid-confirmation hints use the semantic component
  path; the generic status writer has no remaining internal composer call site.
  The core
  constructs ordered structured composition and confirmation input views and
  maps their pure control outcomes without changing public result contracts.
  Its package-internal command-completion adapter translates canonical command
  contexts into terminal completion models, and its unlisted facade adapter
  supplies the pure selectable control projection plus existing semantic
  palette renderer to the driver.
  The internal contextual factory preserves explicit ordinary/main/composition
  callback routing through deterministic plain semantic adapters, while the
  real stream factory binds the selectable main-command route, both preview
  providers, semantic composition and confirmation input, plus the pure
  main-input transitions to one driver. Resource-free `InteractivePickerInput`
  validates and initializes one generic picker, then delegates only its
  immutable control state; callback construction binds deterministic plain
  complete-list picker input, and stream construction binds the same adapter to
  the sole driver's dedicated picker lifecycle with pure control and rendering
  collaborators. Resource-free `InteractivePromptInput` constructs one existing
  `InputPromptView` and returns typed commit, nested-cancel, interrupt, or EOF;
  callback and stream construction bind it to plain or driver-owned input
  without adding a component kind or raw resource owner. Both adapters accept a
  validated opt-in transient flag and pass it only to collaborators that support
  that lifecycle; default calls retain the established picker final frame and
  prompt behavior, while transient callback pickers omit the duplicate final
  frame. Resource-free `InteractiveAdvisoryProgress` binds the terminal's
  advisory awaitable wrapper: callback construction remains visually silent,
  and stream construction injects the pure fixed-glyph renderer into the sole
  driver without exposing capabilities or streams.
  Resource-free `InteractiveMainCommitCleanup` binds only the terminal's private
  cleanup callback: callback terminals use a no-op and the real stream factory
  binds the sole driver's correlated one-shot operation. The supported public
  terminal class and facade exports remain unchanged.
- **Direct internal dependencies:** `agents`, `interactive_commands`,
  `interactive_terminal._controls`, `interactive_terminal._driver`,
  `interactive_terminal._model`, and `interactive_terminal._render`.
- **State, resources, and side effects:** Callback-constructed instances retain
  borrowed synchronous read/write callbacks, deterministic plain capabilities,
  and an explicit width but inspect no stream and acquire no resource.
  Stream-factory instances delegate all raw terminal state, width detection,
  keys, writes, flushes, restoration, readiness, incremental decoding, and the
  bounded process-local main-input history store, optional correlated
  committed-main cleanup token, plus advisory indicator task and cleanup to one
  driver. Main completion,
  palette, and composition providers plus semantic
  input renderers are selected per call and never retained by that driver;
  composition, confirmation, cancellable prompt, and picker input are history-
  free, while subject input remains ordinary. Callback-constructed terminals
  and the core itself own no history.
  The semantic adapter retains only the
  terminal's bound component capability and acquires no stream or raw resource.
  There is no runtime, filesystem, persistence, subprocess, network, or
  import-time operational I/O.
- **Primary verification/documentation:** `tests/test_interactive_terminal.py`,
  `tests/test_interactive_line_editor.py`, `tests/test_public_api_and_cli.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_views.py`, `tests/test_interactive_pty.py`,
  `docs/design/overview.md`,
  `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`.

### `story_writing_agents.models`

- **Source:** `src/story_writing_agents/models.py`
- **Responsibility:** Define immutable cross-boundary request, reply, state, and
  result contracts.
- **Supported surface:** Package-supported `InputKind`, `Phase`, `Verdict`,
  `DebateStatus`, `DebateContextMode`, `DebateContextRequest`,
  `DebateFailureKind`, `DebateProgressPhase`, `DebateProgressEvent`,
  `DebateProgressSink`, `DebateMessage`, `AgentStateEntry`,
  `PromptFileReference`, `AgentDefinition`, `AgentRequest`, `AgentReply`,
  `ConversationRequest`, `ConversationReply`, `ConversationResult`, and
  `DebateResult`. `AgentRequest` carries an optional canonical
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
  `conversations`, `errors`, `models`, `prompts`, `session_lifecycle`, and
  `workspace`.
- **State, resources, and side effects:** Retains an injected or default
  `ConversationEventStore` collaborator and owns the in-memory binding cache,
  in-flight bind tasks, binding lock, and per-session/conversation advisory-turn
  locks. A backend-bound runtime retains that backend's immutable persisted
  configuration snapshot for its lifetime, shares it with prompt assembly, and
  uses its round limit; session runtime overrides and explicit call overrides
  remain live and higher precedence. Backend-free construction retains the
  established on-demand configuration path. For an included consensus request
  it captures one canonical immutable
  context window before reservation, reuses that exact snapshot for every agent
  phase, and delegates create-only sidecar persistence to `Workspace`; fresh
  mode persists identity with no snapshot and an absent request remains
  context-free.
  It synchronously emits typed provider-neutral debate progress in lifecycle
  order, makes result and terminal state durable before terminal debate
  progress, preserves typed observer failures, and retains external
  cancellation while best-effort recording interruption. It also sequences
  ordinary debate persistence and unchanged one-step advisory context-read,
  user-append, provider-call, and reply/error-append flow.
  Before any provider-bound workflow, the runtime requires exact synchronous
  backend lifecycle hooks and performs provider-configuration validation,
  lifecycle pre-read, access acquisition, and lifecycle post-read in that
  order. It validates exact session/durable/timestamp correlation and pure
  provider readiness, compensating only a claim newly acquired by a failed
  sequence; it has no fallback lifecycle path.
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
- **Supported surface:** Internal `PromptAssembler`, whose optional keyword-only
  `configuration_snapshot` accepts one immutable persisted snapshot without
  changing the default on-demand construction path.
- **Direct internal dependencies:** `agents`, `config`, `conversations`,
  `models`, and `workspace`.
- **State, resources, and side effects:** Reads verified prompt references and
  owner-isolated agent history. With no injected snapshot it resolves persisted
  configuration on demand; with a snapshot it reuses those persisted values but
  still loads session runtime overrides for every definition. It forwards an
  optional canonical conversation-context object unchanged into each built
  `AgentRequest` and performs no writes.
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

### `story_writing_agents.session_lifecycle`

- **Source:** `src/story_writing_agents/session_lifecycle.py`
- **Responsibility:** Own the provider-neutral story-session lifecycle contract
  boundary.
- **Supported surface:** Module-supported `SessionDurableState`,
  `SessionAccessState`, `SessionLifecycleState`, and
  `provider_work_is_ready`, all also exported by the package facade.
  `SessionDurableState` is exactly `absent`, `ready`, or `paused`;
  `SessionAccessState` is exactly `claimable`, `held_here`,
  `blocked_by_live_local_owner`, or `ownership_unknown`.
  `SessionLifecycleState` is a frozen, slotted, keyword-only value with fields
  in exact order: `session_id`, `durable_state`, `access_state`,
  `owner_hostname`, `owner_pid`, `created_at`, and `updated_at`.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** The value validates an exact safe
  session ID and enum instances. Owner hostname/PID are absent or present
  together; every non-claimable state carries them. `absent` always means
  claimable access with no owner or timestamps. Persisted `ready`/`paused`
  values require offset-aware `created_at` and `updated_at`, with update not
  before creation. `provider_work_is_ready` returns true only for exact
  provider-configuration readiness plus `ready` and `held_here`. Validation and
  readiness are pure: the module performs no I/O, acquires no access, owns no
  storage, process, provider, thread, filesystem, or mutable runtime state, and
  has no operational import side effect.
- **Primary verification/documentation:** `tests/test_session_lifecycle.py`,
  `tests/test_public_api_and_cli.py`,
  `docs/reference/backend-and-sessions.md`, and
  `docs/design/session-lifecycle.md`.

### `story_writing_agents.smoke`

- **Source:** `src/story_writing_agents/smoke.py`
- **Responsibility:** Run the provider-free deterministic CLI diagnostic across
  subject catalog, transcript, search, retrieval, persisted conversation
  continuity, and included-context consensus behavior.
- **Supported surface:** Internal `run_smoke`; no package-supported API.
- **Direct internal dependencies:** `agents`, `conversation_retrieval`,
  `conversation_search`, `conversations`, `models`, `orchestrator`,
  `session_lifecycle`, and `workspace`.
- **State, resources, and side effects:** Creates a unique temporary or
  explicitly retained workspace diagnostic with a session directive, two
  conversation metadata and transcript sets, bounded search, transcript-read,
  and retrieval activity, trusted in-memory nonpersistent retrieval progress,
  an immutable included-context sidecar, debate and agent state, a result, and
  a manuscript; verifies core-document bytes. Its provider-free backend
  implements the exact lifecycle hooks and returns correlated ready/access
  facts so runtime preparation exercises the canonical contract. Temporary
  mode removes the workspace after normal return, while explicit mode retains
  plaintext diagnostics.
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
  workspace/session/conversation. The fixed initialized topology includes
  `agent_states/orchestrator_state/conversation_defaults`; new workspaces also
  receive the canonical five-key system-default document with null Codex model
  and reasoning values, while existing system-default bytes are never migrated
  or rewritten by initialization. `cli_context` owns the schema stored in the
  conversation-default directory. The backend-owned story-session
  process-access claim remains the cross-process same-session write guard.
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

- `Workspace` initializes the canonical five-key system-default document but
  never rewrites an existing one. `ConfigManager` owns full system and per-agent
  schema validation and the shared Codex model/reasoning validators plus
  immutable complete snapshots. Each `AgentBackend` validates and snapshots
  those values once at construction and alone projects the per-bound-agent
  invocation settings: a non-null explicit model precedes the per-agent and
  legacy system model, while per-agent reasoning precedes the legacy system
  value. Provider routing retains explicit-over-agent-over-Codex precedence.
  Model and reasoning values do not enter bindings, fingerprints, thread keys,
  lifecycle artifacts, request payloads, or active-backend refresh. A
  backend-bound `StoryRuntime` and its `PromptAssembler` reuse that same
  construction-time snapshot for provider routes, language, persona, and round
  defaults while session and explicit overrides remain live; settings saves
  affect only a later normal lifecycle.
- `interactive_settings` alone owns one call-local exact-byte configuration
  baseline, immutable pending candidate, safe persona catalog, complete
  candidate validation, exact stale-state detection, deterministic system-then-
  agent writes, and continued reverse-order exact-text rollback. The controller
  owns focus and semantic interaction only and reaches this unit directly; the
  settings unit has no controller, terminal, backend, runtime, or parser edge.
- `interactive_commands` owns canonical command and agent-route metadata, parse
  rules, typed parsed actions, and the pure cursor-aware completion grammar and
  replacement spans derived from that same metadata. Agent-valued completion
  and fuzzy guidance expose only preferred prompt aliases in roster order while
  parsing retains every accepted route alias. `interactive_line_editor`
  preserves its legacy main suggestion projection while delegating actual main
  reads to the shared selectable terminal route; `_controls` owns the fixed
  ordered `/send`, `/cancel` composition projection. The facade delegates all
  raw terminal behavior to the unified terminal driver and creates no registry
  or parser edge. The
  `interactive_terminal` package keeps its established public facade while
  `_core` consumes public `COMMANDS` and `AGENT_ROUTES` metadata plus canonical
  display names to coordinate safe presentation and explicit ordinary, main,
  composition, and confirmation reader routing. `_model`, `_controls`, and
  `_render` remain pure. `_render` alone recognizes complete strict JSON
  object/array agent replies for two-space Unicode-preserving presentation and
  owns the bold terminal-default `AGENT` decoration; terminal `_core` and
  controller facade consumers apply that projection only at presentation, so
  provider results and stored conversation events remain unchanged. `_driver`
  alone owns raw streams, capabilities,
  functional cursor
  controls, width, key decoding, writes, flushes, restoration, and one bounded
  process-local main-input history store, fixed 50-millisecond Escape readiness
  lookahead, and call-local incremental decoder. For advisory progress it also
  exclusively owns the active glyph state, operation/animation task joining,
  cursor clear/redraw writes, and cleanup; `_render` supplies only the pure
  injected four-glyph projection, and incapable or callback terminals remain
  visually silent. It receives completion,
  palette, preview, and trusted semantic input collaborators only per read,
  resets navigation for each main read, and keeps composition and confirmation
  reads history-free. Cancellable settings prompt input is also history- and
  completion-free: `_controls` owns its pure Escape outcome, `_core` binds the
  resource-free facade adapter, and the sole driver owns its raw/fallback input
  and restoration. Settings alone opts into transient picker/prompt reads: the
  driver clears capable live rows before returning and callback/line fallback
  omits only duplicate picker final frames. Explicit Save and Cancel
  confirmations alone carry the controller-owned changed-only Current/Pending
  context, including plain-text changed cues and any invalid-answer hint;
  ordinary editing and whole-flow cancellation emit no snapshot. Nested Escape
  returns without committing, and default non-settings control behavior is
  unchanged. The same driver owns the one-shot committed-main display token:
  only the facade adapter for a parsed `/settings` action consumes it, every
  intervening input/output invalidates it, and capable cleanup adds no blank
  line while fallback cleanup is a no-op. The driver also owns the dedicated
  picker lifecycle, observes picker width on each redraw, and keeps picker input
  separate from editable line, history, and completion state. `_controls`
  supplies immutable history,
  selectable palette, composer, confirmation, and generic picker transitions:
  open-palette Up and Down
  move the clamped highlight, while closed-palette Up and Down remain history
  keys; picker Up and Down skip disabled choices and clamp within their own
  at-most-seven-row viewport. Opaque choice identities remain internal
  correlation keys and are never rendered. Composition preview remains
  nonselectable. The real CLI constructs one
  stream-backed terminal rather than injecting a second editor. The terminal
  facade also
  exposes an unlisted typed semantic-output adapter and closed component
  bindings, including `ListView`, for package consumers without expanding
  `__all__`; controller code imports only that facade, never a terminal leaf.
  The same facade exposes unlisted advisory progress and error bindings: the
  controller brackets exactly one advisory runtime awaitable without owning
  animation mechanics, and the driver removes the indicator before result,
  cancellation, or failure presentation. Persistent advisory routing and
  waiting records are intentionally absent.
  Counted session, conversation, search, transcript, and history output uses
  one positioned one-record list and one component write for each established
  row callback, preserving partial-output and cancellation boundaries.
  Composer lifecycle records and invalid-confirmation hints now use one semantic
  component write each, with zero internal composer calls to the generic status
  writer; the controller owns none. The controller still receives only
  completed terminal outcomes, alone passes exact lines to the parser once, and
  neither it nor parser or storage code owns or persists command history.
- `interactive_controller` is the stable one-class import boundary and owns no
  shell state or resource. Its `_core` owns interactive shell action mapping,
  current in-memory attachment, current-focus conversation and sticky-target state,
  the resource-free facade-only settings cleanup/picker/prompt interaction and
  its call-local pending candidate and confirmation diff,
  process-local per-session latest accepted STARTED debate IDs, transition and
  compensation ordering, retained runtime and memory-service wiring, one
  private lifecycle `asyncio.Runner`, controller-local interruption/progress
  proof, and ordered Runner-close, known-access-release, and backend-close
  coordination after its one run starts.
  `AgentBackend` retains actual session access claims, manifests, subprocesses,
  and provider-thread ownership; `ConversationCatalog` retains conversation
  metadata ownership; `cli_context` owns both session and per-session
  conversation-default persistence; and `interactive_terminal` retains
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
  The `_session_policy`, `_conversation_policy`, and `_runtime_policy` leaves
  interpret immutable collaborator facts and safe failure categories without
  retaining state, performing I/O, formatting presentation text, mutating
  progress collections, importing `_core`, or depending on one another.
  `_runtime_policy` returns typed debate and context inspection facts;
  `_core` alone constructs their semantic presentation and focused-history
  context accounting and commits canonical results to controller-owned process
  state, while the terminal remains the rendering and emission owner.
  Provider-free `/search` and `/transcript` inspection crosses only public
  `ConversationMemoryService.search` and
  `ConversationMemoryService.read_transcript` boundaries. Provider-free
  focused `/history` and the transcript portion of `/status` each cross one
  public `FilesystemConversationEventStore.read` boundary and derive context
  facts from that exact immutable tuple through the pure module-supported
  `select_bounded_conversation_context`; the controller never appends or calls
  event-store `read_context`. Ctrl-C during provider-free search, transcript,
  or history rendering stops further output without mutating those boundaries.
  Directives and safe inspection cross only public
  `StoryRuntime` methods. The controller performs no direct backend provider,
  context/result, retrieval-control, or artifact persistence.
  Current-focus conversation consensus crosses only `StoryRuntime.run_prompt`
  or `run_draft`; draft-file bytes cross the internal
  `run_inputs.read_draft_file` boundary.
- The controller wires its resolved `ConversationCatalog` and one retained
  `FilesystemConversationEventStore` into both `StoryRuntime` and the
  attachment-scoped `ConversationMemoryService`, so injected and default
  catalogs remain the same authority for metadata and transcript identity. It
  prepares the destination service before attachment commit, rebinds it only
  with the committed attachment, and clears it on switch, pause, cleanup, and
  final close; changing current focus does not rebind it. Search defaults to
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
  per-call timeout, cancellation, and taint lifecycle; it drains both subprocess
  streams, discards nonzero-exit stderr without decoding it, and uses fixed
  provider-value-free process/response-validation diagnostics.
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
  conversation in process; the backend-owned story-session access claim remains
  responsible for cross-process same-session write exclusion.
- One `StoryRuntime` serializes a complete advisory operation per
  `(session_id, conversation_id)` while allowing other conversations to
  proceed. This does not widen the event adapter's or story-session claim's
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
- `session_lifecycle` owns only the immutable provider-neutral durable/access
  facts, their invariants, and the pure provider-readiness query. `AgentBackend`
  owns V2 manifest, claim, claim-token, guard, liveness classification,
  provider-thread, and provider resources. Guarded claim, proved-dead reclaim,
  and exact-token release transactions preserve changed or foreign claims.
  `StoryRuntime` owns provider-bound lifecycle preparation through exact
  validation, pre-read, acquisition, post-read, correlation, and new-claim
  compensation. The controller owns interactive attachment/default/focus
  state and presentation; `cli_context` owns V2 session and conversation
  defaults. No lifecycle alias, projection, old callable, fallback, migration,
  automatic deletion, or second lifecycle abstraction remains. Version-1
  lifecycle/default artifacts are unsupported inputs and are never rewritten.
- `tests/conversation_event_store_contract.py` is reusable test-only adapter
  conformance support; it is not a production module or supported package API.
- Direct imports of internal configuration, prompt, workspace-constant, or CLI
  helpers in tests do not add them to the supported package API.
