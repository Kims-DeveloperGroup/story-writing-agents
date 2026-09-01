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

Importable production units indexed: 97.

Direct internal dependency edges indexed: 360.

Directed internal dependency cycles indexed: 0.

## Current Dependency Direction

The current graph flows from entry points and orchestration toward provider,
configuration, prompt, persistence, and stable contract modules:

```text
__main__ -> cli -> conversation_rag_cli
                 -> conversation_storage.contracts/migration/selection
                 -> interactive_controller -> interactive_controller._core
                 -> interactive_terminal -> interactive_terminal._core
                 -> interactive_auth -> interactive_terminal/provider_auth
                 -> interactive_settings -> agents/config/errors/workspace
                 -> provider_auth -> (standard library only)
                 -> provider_catalog -> (standard library only)
                 -> debug_log -> agents/provider_adapter/workspace
                 -> orchestrator -> backend/config/conversations/debug_log/directives
                 |               -> conversation_rag_runtime
                 |               -> conversation_storage.composition/contracts
                 |               -> errors/prompts/workspace
                 |               -> writing_artifacts/repository/runtime_references
                 -> backend -> agent_context/agent_context.service
                 |          -> agent_exchanges/agent_exchanges.capture
                 |          -> antigravity_adapter -> agent_context/models
                 |          |                        -> provider_adapter
                 |          |                        -> provider_process
                 |          -> codex_adapter -> agent_context/agents/config
                 |          |                 -> errors/models
                 |          |                 -> provider_adapter/provider_process
                 |          -> config/conversation_retrieval/debug_log
                 |          -> interactive_settings
                 |          -> workspace/models/errors/agents
                 |          -> provider_adapter/provider_error_artifacts/provider_process
                 |          -> session_lifecycle/writing_artifacts.read_actions
                 -> cli_context -> workspace/errors/agents

interactive_controller._core -> interactive_controller._session_policy
                             -> interactive_controller._conversation_policy
                             -> interactive_controller._exchange_policy
                             -> interactive_controller._exchange_viewer
                             -> interactive_controller._runtime_policy
                             -> agent_exchanges/agents/backend/cli_context/debug_log
                             -> conversation_search
                             -> conversation_storage.contracts/selection
                             -> conversations/errors/interactive_commands
                             -> interactive_auth/interactive_settings/interactive_terminal
                             -> orchestrator/provider_adapter/provider_auth
                             -> provider_catalog/session_lifecycle/workspace
interactive_controller._session_policy -> errors/interactive_terminal/session_lifecycle
interactive_controller._conversation_policy -> conversation_search/conversations
                                            -> errors/interactive_terminal/workspace
interactive_controller._exchange_policy -> agent_exchanges/agents/models
interactive_controller._exchange_viewer -> agent_exchanges
                                        -> interactive_terminal._external_view
                                        -> jsonl-viewer (required exact-pinned base package, lazy import)
interactive_controller._runtime_policy -> errors/interactive_terminal/models/workspace

smoke -> agents/conversation_search/conversations/debug_log/orchestrator/workspace
debug_log -> agents/provider_adapter/workspace
directives -> config/workspace/errors/agents
interactive_commands -> agents
interactive_auth -> interactive_terminal/provider_auth
interactive_line_editor -> interactive_commands/interactive_terminal
interactive_settings -> agents/config/errors/workspace
interactive_terminal -> interactive_terminal._core
interactive_terminal._core -> agents/interactive_commands
                           -> interactive_terminal._controls
                           -> interactive_terminal._driver
                           -> interactive_terminal._external_view
                           -> interactive_terminal._model
                           -> interactive_terminal._reference_activation
                           -> interactive_terminal._render
                           -> interactive_terminal._viewer_controls
                           -> interactive_terminal._viewer_model
                           -> interactive_terminal._viewer_render
interactive_terminal._controls -> interactive_terminal._model
interactive_terminal._driver -> interactive_terminal._input_lifecycle
                             -> interactive_terminal._external_view
                             -> interactive_terminal._model
                             -> interactive_terminal._reference_activation
                             -> interactive_terminal._viewer_model
interactive_terminal._reference_activation -> interactive_terminal._model
                                           -> writing_artifacts
interactive_terminal._render -> interactive_terminal._model
interactive_terminal._viewer_controls -> interactive_terminal._model
                                      -> interactive_terminal._viewer_model
interactive_terminal._viewer_render -> interactive_terminal._model
                                    -> interactive_terminal._viewer_model
conversation_literal_search -> (standard library only)
conversation_rag -> (standard library only)
conversation_rag_local -> conversation_literal_search/conversation_rag
conversation_rag_store -> conversation_rag/workspace
generic_rag_adapter -> conversation_rag
                    -> generic-rag 0.1.0 (optional external package)
conversation_rag_composition -> conversation_rag
                             -> conversation_rag_local/conversation_rag_runtime
                             -> conversation_rag_store/workspace
                             -> generic_rag_adapter (deferred until composition)
conversation_rag_runtime -> conversation_literal_search/conversation_rag
                         -> conversation_retrieval/conversation_search
                         -> conversation_storage.contracts/conversations
conversation_rag_cli -> config/conversation_rag/conversation_rag_composition
                     -> conversation_rag_local/conversation_rag_runtime
                     -> conversation_rag_store/conversation_retrieval
                     -> conversation_search/conversations
                     -> conversation_storage.contracts/selection
                     -> errors/workspace
story_writing_agents facade -> interactive_commands
                            -> conversation_retrieval
                            |  -> agents/conversation_rag/conversation_search
                            |  -> conversations/errors/models
                            -> conversation_search -> conversation_literal_search
                            |                      -> conversations/errors
                            -> session_lifecycle
conversation_storage -> conversation_storage.archive/composition/contracts
                     -> conversation_storage.filesystem_archive
conversation_storage.archive -> conversations/errors
conversation_storage.composition -> conversation_search
                                 -> conversation_storage.contracts
                                 -> conversation_storage.filesystem
                                 -> workspace.core
conversation_storage.contracts -> conversation_search/conversations/errors
conversation_storage.filesystem -> conversations/errors/workspace.core
conversation_storage.filesystem_archive -> conversation_storage.archive/contracts
                                        -> conversation_storage.filesystem
                                        -> conversations/errors/workspace.core
conversation_storage.migration -> conversation_storage.archive/composition/contracts
                               -> conversation_storage.filesystem_archive/sqlite
                               -> errors/workspace.core
conversation_storage.provisioning -> conversation_storage.contracts/sqlite/workspace.core
conversation_storage.selection -> config/workspace.core
                               -> conversation_storage.archive/composition/contracts
                               -> conversation_storage.filesystem_archive/provisioning/sqlite
conversation_storage.sqlite_database -> workspace.core
conversation_storage.sqlite_search_index -> (standard library only)
conversation_storage.sqlite -> conversation_literal_search/conversation_search
                            -> conversation_storage.archive/composition/contracts
                            -> conversations/errors/workspace.core
                            -> conversation_storage.sqlite_database
                            -> conversation_storage.sqlite_search_index
orchestrator -> agent_context.actions/manifest/planning/service/sources
             -> conversation_rag_runtime
             -> conversation_storage.composition/contracts
models -> conversations/agents/errors
agent_context -> agent_context.accounting
agent_context.accounting -> (standard library only)
agent_context.actions -> agent_context.manifest/planning
agent_context.ledger -> agent_context.accounting/manifest/planning
                     -> workspace
agent_context.manifest -> (standard library only)
agent_context.planning -> agent_context.manifest
agent_context.service -> agent_context.accounting/actions/ledger
                      -> agent_context.manifest/planning/sources
                      -> workspace
agent_context.sources -> agent_context.actions/manifest
                      -> conversations/errors/models/workspace
                      -> writing_artifacts.filesystem_sources
                      -> writing_artifacts.references/repository
agent_exchanges -> agent_exchanges.contracts/storage
agent_exchanges.capture -> agent_exchanges.contracts/storage
                        -> provider_adapter/workspace
agent_exchanges.contracts -> agents/errors
agent_exchanges.storage -> agents/agent_exchanges.contracts/workspace
provider_adapter -> agent_context/models
provider_error_artifacts -> agents/workspace
provider_auth -> (standard library only)
provider_catalog -> (standard library only)
provider_process -> (standard library only)
session_lifecycle -> (standard library only)
conversations -> conversations.catalog/context/events
conversations.catalog -> errors
conversations.context -> conversations.events/errors
conversations.events -> agents/errors
workspace -> conversation_storage.filesystem/workspace.core
workspace.core -> agents/conversations/errors/models/writing_artifacts
writing_artifacts -> writing_artifacts.authorization/catalog/directives/read_actions/references
writing_artifacts.authorization -> writing_artifacts.catalog/references
writing_artifacts.directives -> writing_artifacts.references
writing_artifacts.read_actions -> writing_artifacts.authorization/references
writing_artifacts.runtime_references -> writing_artifacts.authorization/directives
                                     -> writing_artifacts.read_actions/references/repository
writing_artifacts.references -> writing_artifacts.catalog
writing_artifacts._reference_evidence -> writing_artifacts._storage/authorization
writing_artifacts.filesystem_sources -> errors/writing_artifacts._storage
                                     -> writing_artifacts.catalog/references
writing_artifacts.repository -> agents/errors/writing_artifacts._reference_evidence
                             -> writing_artifacts._storage/authorization/catalog
                             -> writing_artifacts.filesystem_sources/read_actions/references
writing_artifacts._storage -> errors
```

Some modules directly consume lower-level contracts rather than following
every intermediate arrow. The current runtime graph is acyclic.

## Package and Module Entries

### `story_writing_agents`

- **Source:** `src/story_writing_agents/__init__.py`
- **Responsibility:** Provide the intentional package-level API facade.
- **Supported surface:** Exactly these 118 names in canonical `__all__` order:
  `AGENT_SPECS`, `COMMANDS`, `ActionKind`, `AgentBackend`, `AgentDefinition`,
  `AgentReply`, `AgentRequest`, `AgentStateEntry`, `BackendProtocolError`,
  `BoundAgent`, `CHARACTER_CUSTODIAN`, `CONVERSATION_MEMORY_POLICY`,
  `CONVERSATION_CONTEXT_POLICY`, `CONVERSATION_EVENT_SCHEMA_VERSION`,
  `CONVERSATION_METADATA_SCHEMA_VERSION`, `CONVERSATION_RETRIEVAL_POLICY`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`, `ConfigurationError`,
  `CommandSpec`, `ConversationCitation`, `ConversationCatalog`,
  `ConversationContextPolicy`, `ConversationContextWindow`,
  `ConversationEvent`, `ConversationEventAppend`, `ConversationEventKind`,
  `ConversationEventStore`, `ConversationExistsError`, `ConversationMetadata`,
  `ConversationMemoryPolicy`, `ConversationMemoryService`,
  `ConversationNotFoundError`, `ConversationRetrievalAction`,
  `ConversationRetrievalActionHandler`, `ConversationRetrievalActionKind`,
  `ConversationRetrievalAnswerAction`, `ConversationRetrievalBudgetError`,
  `ConversationRetrievalError`, `ConversationRetrievalObservation`,
  `ConversationRetrievalObserverError`, `ConversationRetrievalPolicy`,
  `ConversationRetrievalProgressEvent`, `ConversationRetrievalProgressPhase`,
  `ConversationRetrievalProgressSink`, `ConversationRetrievalReadAction`,
  `ConversationRetrievalReadObservation`, `ConversationRetrievalReply`,
  `ConversationRetrievalRequest`, `ConversationRetrievalResult`,
  `ConversationRetrievalSearchAction`, `ConversationRetrievalSearchItem`,
  `ConversationRetrievalSearchObservation`,
  `ConversationRetrievalSemanticError`, `ConversationRetrievalSource`,
  `ConversationRetrievalTranscriptEvent`, `ConversationReply`,
  `ConversationRequest`, `ConversationResult`, `ConversationSearch`,
  `ConversationSearchHit`, `ConversationSearchMatchKind`,
  `ConversationSearchQuery`, `ConversationSearchResult`,
  `ConversationSearchScope`, `ConversationStatus`,
  `ConversationTranscriptCursor`, `ConversationTranscriptPage`,
  `DebateContextMode`, `DebateContextRequest`, `DebateFailureKind`,
  `DebateMessage`, `DebateProgressEvent`, `DebateProgressPhase`,
  `DebateProgressSink`, `DebateResult`, `DebateStatus`, `DebateExistsError`,
  `DirectiveError`, `DirectiveKind`, `EXECUTION_SPECIALIST`,
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`,
  `InputKind`, `InteractiveAction`, `LEAD_ORCHESTRATOR`,
  `MAX_CONVERSATION_SUBJECT_LENGTH`, `ManuscriptExistsError`, `PLOT_ARCHITECT`,
  `Phase`, `PromptFileReference`, `ProgressObserverError`, `ROSTER`,
  `ReferenceConversationSearch`, `RuntimeDirective`, `RuntimeSession`,
  `SPECIALIST_IDS`, `SessionAccessBlockedError`, `SessionAccessState`,
  `SessionDurableState`, `SessionExistsError`, `SessionIdError`,
  `SessionLifecycleState`, `SessionNotFoundError`,
  `SessionOwnershipUnknownError`, `SessionPausedError`, `StoryRuntime`,
  `StoryWritingAgentsError`, `Verdict`, `Workspace`, `WorkspaceFormatError`,
  `WorkspaceSecurityError`, `WorkspaceVersionUnsupportedError`,
  `apply_directive`, `initialize_workspace`, `normalize_conversation_subject`,
  `parse_directive`, `parse_interactive_line`, and `provider_work_is_ready`.
  It does not import or export the removed compatibility aliases `Runtime` or
  `Session`. Schema-v2 `Collaboration*` contracts remain supported only by the
  direct `conversations` facade pending the separate active-v2 cutover.
- **Direct internal dependencies:** `agents`, `backend`,
  `conversation_retrieval`, `conversation_search`, `conversations`,
  `directives`, `errors`, `interactive_commands`, `models`,
  `orchestrator`, `session_lifecycle`, and `workspace`.
- **State, resources, and side effects:** Owns no state or external resource;
  importing the facade loads its exported modules.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `docs/reference/runtime-api.md`, `docs/reference/agent-contracts.md`, and
  `docs/reference/cli.md` verify the supported facade.

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
  `docs/concepts.md`, `docs/guides/lead-agent.md`, and
  `docs/reference/configuration/agent-configuration.md`.

### `story_writing_agents.agent_exchanges`

- **Source:** `src/story_writing_agents/agent_exchanges/__init__.py`
- **Responsibility:** Provide the supported bounded agent-exchange storage
  facade.
- **Supported surface:** No project-root export. The package facade exposes
  exactly `AGENT_EXCHANGE_SCHEMA_VERSION`,
  `AGENT_EXCHANGE_CONTENT_MAX_BYTES`, `AGENT_EXCHANGE_TOTAL_MAX_BYTES`,
  `AgentExchangeProvider`, `AgentExchangeRequestType`, `AgentExchangeOutcome`,
  `AgentExchangeScopeKind`, `AgentExchangeFailure`,
  `AgentExchangeStorageError`, `AgentExchangeRecordInput`,
  `AgentExchangeAppendReceipt`, `AgentExchangeInventoryItem`,
  `AgentExchangeSelection`, `AgentExchangeSnapshot`, and
  `WorkspaceAgentExchangeStore`.
- **Direct internal dependencies:** `agent_exchanges.contracts` and
  `agent_exchanges.storage`.
- **State, resources, and side effects:** Re-exports immutable contracts and
  the I/O-free store constructor only. Importing creates no filesystem state,
  lock, descriptor, thread, subprocess, or provider interaction. The separate
  direct-module capture lifecycle is intentionally not imported or re-exported.
- **Primary verification/documentation:**
  `tests/test_agent_exchange_contracts.py`,
  `tests/test_agent_exchange_capture.py`,
  `tests/test_agent_exchange_storage.py`,
  `docs/design/persistence.md#agent-exchange-persistence`,
  `docs/operations/filesystem-safety.md#agent-exchange-filesystem-safety`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.agent_exchanges.contracts`

- **Source:** `src/story_writing_agents/agent_exchanges/contracts.py`
- **Responsibility:** Define the immutable durable agent-exchange schema and
  classifiable failure language.
- **Supported surface:** The package facade supports schema version 1, the
  262,144-byte per-content bound, the 16,777,216-byte managed-JSONL bound, the
  exact provider/request/outcome/scope/failure enums, fixed safe
  `AgentExchangeStorageError`, and frozen slotted input, append-receipt,
  inventory, selection, and snapshot values. Input accepts only session,
  scope, canonical agent, provider, exchange, request-type, and strict-UTF-8
  content fields; it has no arbitrary metadata seam. Append receipts expose
  content-free full and retained UTF-8 byte and Python Unicode code-point
  counts, truncation state, and the full-content SHA-256. Content-bearing input
  and snapshot bytes are excluded from representations.
- **Direct internal dependencies:** `agents` and `errors`.
- **State, resources, and side effects:** Owns no mutable state or external
  resource. Importing compiles bounded validation patterns and creates enum and
  immutable-contract definitions without I/O.
- **Primary verification/documentation:**
  `tests/test_agent_exchange_contracts.py`,
  `tests/test_agent_exchange_capture.py`,
  `tests/test_agent_exchange_storage.py`,
  `docs/design/persistence.md#agent-exchange-persistence`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.agent_exchanges.capture`

- **Source:** `src/story_writing_agents/agent_exchanges/capture.py`
- **Responsibility:** Coordinate fail-open recording for one physical provider
  exchange.
- **Supported surface:** No project-root or `agent_exchanges`-facade-supported
  API. The direct-module package-internal `__all__` exposes exactly
  `WorkspaceAgentExchangeCapture`, `AgentExchangeCaptureLifecycle`, and
  `AgentExchangeTerminalCode`. The workspace capture's I/O-free constructor
  binds the durable store; `begin(...)` requires exact session, conversation or
  debate scope, canonical agent, provider, and `ProviderInvocation` values and
  records the request before returning a shared `exchange_<uuidhex>` lifecycle.
  That lifecycle admits at most one exact `ProviderResult` response and one
  closed accepted, rejected, provider-failure, cancelled, or post-response
  runtime-failure terminal attempt. The 13 terminal codes are fixed enums; no
  arbitrary exception, diagnostic, metadata, or provider-native stream enters
  the contract.
- **Direct internal dependencies:** `agent_exchanges.contracts`,
  `agent_exchanges.storage`, `provider_adapter`, and `workspace`.
- **State, resources, and side effects:** Import and construction perform no
  filesystem work. A successful begin assigns one transient UUID correlation,
  appends canonical compact JSON for the exact invocation schema, payload,
  prior thread, and call kind, and returns a lifecycle retaining that exact
  identity plus a private thread lock and state. A completed response projects
  only response text, result thread, exact-or-null usage, and exact-or-null
  transport bytes; a terminal projects only outcome and fixed code. Response
  and terminal state advances before append, so duplicate or failed attempts
  are never retried. Every begin, response, or terminal serialization,
  validation, or storage `BaseException` is fail-open; a failed begin returns a
  disabled lifecycle. The retained
  `WorkspaceAgentExchangeStore` remains the sole filesystem, locking, rotation,
  pruning, sequence, timestamp, truncation, and snapshot owner.
- **Primary verification/documentation:**
  `tests/test_agent_exchange_capture.py`,
  `tests/test_agent_exchange_contracts.py`,
  `tests/test_agent_context_execution.py`, `tests/test_codex_backend.py`, and
  `tests/test_antigravity_parity.py`.

### `story_writing_agents.agent_exchanges.storage`

- **Source:** `src/story_writing_agents/agent_exchanges/storage.py`
- **Responsibility:** Own secure bounded append and snapshot lifecycles for
  workspace agent-exchange JSONL.
- **Supported surface:** The package facade exposes only
  `WorkspaceAgentExchangeStore`. Construction performs no I/O;
  `append(record)` durably assigns global and per-agent sequences plus a UTC
  millisecond timestamp, `inventory(session_id)` returns content-free exact
  scope selections, and `snapshot(selection)` returns immutable canonical
  strict-UTF-8 JSONL bytes without a writable path or handle. Storage failures
  cross the boundary only as fixed `AgentExchangeFailure` codes and prose;
  `agent_exchanges.capture` owns fail-open submission policy.
- **Direct internal dependencies:** `agents`, `agent_exchanges.contracts`, and
  `workspace`.
- **State, resources, and side effects:** Import and construction perform no
  filesystem work. Mutations exclusively own
  `debug_logs/agent_exchanges/`, its independent
  `.agent_exchanges.lock`, canonical global and per-session/per-agent durable
  high-water state, private session directories, and active plus seven rotated
  two-MiB agent JSONL segments. Each content value retains the longest complete
  UTF-8 prefix through 262,144 bytes, full and retained UTF-8 byte and Python
  Unicode code-point counts, truncation state, and the full SHA-256. A weak
  per-workspace process lock and bounded
  cross-process advisory lock serialize append, rotation, newest-record global
  pruning, inventory, and snapshot reads. The owner borrows only `Workspace`'s
  identity-bound root open/reverification seam; all owned traversal and
  mutation is descriptor-relative, no-follow, exact-name, mode-, identity-,
  type-, link-, size-, schema-, and canonical-serialization checked. Durable
  global and stream high-water reservation precedes append, so failed calls may
  leave gaps but cannot reuse a sequence. Partial record suffixes are rolled
  back, complete records rotate before overflow, and only validated managed
  JSONL records are pruned by global workspace sequence to the 16-MiB cap.
  Absent readers create nothing. The module never imports, scans, validates,
  prunes, migrates, or counts sibling `provider_error_responses` or
  `runtime.jsonl` state and owns no provider, viewer, terminal, or CLI behavior.
- **Primary verification/documentation:**
  `tests/test_agent_exchange_storage.py`,
  `docs/design/persistence.md#agent-exchange-persistence`,
  `docs/operations/filesystem-safety.md#agent-exchange-filesystem-safety`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.agent_context`

- **Source:** `src/story_writing_agents/agent_context/__init__.py`
- **Responsibility:** Provide the internal provider-neutral agent-context
  facade.
- **Supported surface:** No project-root-supported API. The package facade
  exposes exactly 11 package-internal accounting contracts:
  `ContextComponentBytes`, `ContextComponentKind`, `JsonPath`,
  `ProviderCallAccounting`, `ProviderCallKind`, `ProviderCallState`,
  `ProviderJsonEncodingError`, `ProviderPayloadAccounting`, `ProviderUsage`,
  `account_provider_payload`, and `canonical_provider_json`. This is the
  facade's complete exposed surface. The package-internal `actions`, `ledger`,
  `manifest`, `planning`, `service`, and `sources` modules intentionally remain
  direct-module contracts and are not re-exported here.
- **Direct internal dependencies:** `agent_context.accounting`.
- **State, resources, and side effects:** Re-exports immutable values and pure
  functions only. Importing performs no I/O and creates no mutable state or
  external resource.
- **Primary verification/documentation:**
  `tests/test_agent_context_accounting.py`, `tests/test_codex_backend.py`,
  `tests/test_antigravity_adapter.py`, `tests/test_antigravity_parity.py`, and
  `docs/design/prompts-and-provider.md`.

### `story_writing_agents.agent_context.accounting`

- **Source:** `src/story_writing_agents/agent_context/accounting.py`
- **Responsibility:** Canonically encode and account for exact provider model
  input without retaining its content.
- **Supported surface:** No project-root-supported API. The owning package
  exposes immutable component, payload, physical-call, state, and exact-usage
  values; the stable call/component vocabularies; content-free
  `ProviderJsonEncodingError`; and pure `canonical_provider_json` and
  `account_provider_payload`. The compact insertion-ordered encoder emits
  strict UTF-8 JSON with `ensure_ascii=False`; schema and payload component
  counts partition every emitted byte exactly. Binding components permit
  distinct content-free core-document path identities. Raw mandatory-reference
  bytes are a validated non-additive subset, and missing or malformed provider
  token metadata is never estimated.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Owns no mutable module state or
  external resource. Encoding and accounting use call-local memory only;
  returned payload bytes remain content-bearing caller-owned transport input,
  while every retained accounting value contains only kinds, safe identities,
  counts, state, and exact optional usage. Importing performs no I/O.
- **Primary verification/documentation:**
  `tests/test_agent_context_accounting.py`,
  `tests/test_codex_backend.py`, `tests/test_antigravity_adapter.py`,
  `tests/test_antigravity_parity.py`, and
  `docs/design/prompts-and-provider.md`.

### `story_writing_agents.agent_context.actions`

- **Source:** `src/story_writing_agents/agent_context/actions.py`
- **Responsibility:** Define the immutable agent-directed context action and
  observation language.
- **Supported surface:** No project-root or `agent_context`-facade-supported
  API. The direct-module package-internal `__all__` exposes
  `CONTEXT_ACTION_POLICY`, `ContextAction`, `ContextActionCorrelation`,
  `ContextActionError`, `ContextActionErrorCode`, `ContextActionKind`,
  `ContextActionObservation`, `ContextActionPolicy`, `ContextAnswerAction`,
  `ContextAnswerAuthorization`, `ContextEvidenceReceipt`, `ContextReadAction`,
  `ContextReadObservation`, `ContextSearchAction`, `ContextSearchHit`,
  `ContextSearchObservation`, `ContextSearchQuery`, `ContextSourceBoundary`,
  `ContextSourceBoundaryKind`, `ContextSourceEvidence`, `ContextSourceHandle`,
  and `serialize_context_action_observation`. It owns exact invocation
  correlation, canonical source-kind-constrained literal queries whose optional
  writing-artifact directory prefix uses the reference-source path namespace
  and must be shared by every query in one SEARCH action, tagged
  whole/line/turn/record boundaries, ordered actions and observations,
  content-free evidence receipts, repr-hidden excerpts, fixed limits and error
  codes, exact source/authority/boundary evidence matrices, repr-hidden answer
  payloads, and terminal answer authorizations bound to the manifest, ordered
  completed evidence, used subset, answer/result digest, and unchanged pending
  reference facts. Established direct observation serialization retains its
  complete delimited object frames. Contextual serialization projects complete
  tagged provider evidence into compact positional JSON frames: query order;
  numeric alias and canonical source identity, kind, revision, digest, and
  UTF-8 size; exact boundary identity, digest, and size; safe authority and
  locator provenance; a fresh invocation handle; and snippet/truncation or
  exact read content. It collapses only an explicit revision/source-digest
  equality, while complete correlation and receipt facts remain on the
  validated in-process values.
- **Direct internal dependencies:** `agent_context.manifest` and
  `agent_context.planning`.
- **State, resources, and side effects:** Owns one frozen default policy and
  pure immutable values only. Validation and serialization use call-local
  memory; importing performs no I/O, entropy, registration, or mutation.
- **Primary verification/documentation:**
  `tests/test_agent_context_actions.py`,
  `docs/design/agent-context.md`; module-inventory, dependency,
  facade, and import-boundary assertions across
  `tests/test_rag_module_and_packaging_boundary.py`,
  `tests/test_session_lifecycle.py`, and
  `tests/test_interactive_controller.py`.

### `story_writing_agents.agent_context.sources`

- **Source:** `src/story_writing_agents/agent_context/sources.py`
- **Responsibility:** Adapt the fixed authorized owner domains into bounded
  canonical context-source snapshots.
- **Supported surface:** No project-root or `agent_context`-facade-supported
  API. The direct-module package-internal `__all__` exposes
  `CONTEXT_SOURCE_POLICY`, `ActiveDebateSourceAdapter`,
  `ContextSourceDescriptor`, `ContextSourceError`, `ContextSourceErrorCode`,
  `ContextSourcePolicy`, `ContextSourceRecord`, `ContextSourceRegistry`,
  `ContextSourceSearchMatch`, `ContextSourceSnapshot`,
  `FocusedConversationSourceAdapter`, `OwnAgentStateSourceAdapter`, and
  `WritingArtifactSourceAdapter`. The non-facade package-internal
  `writing_artifact_source_id(path)` helper is the single canonical source-ID
  projection consumed by runtime composition. The exact four-slot registry
  binds canonical writing artifacts, focused same-session conversation
  authority, one active public debate, and the invoking agent's own session
  state. The conversation adapter defaults to one focused conversation; its
  explicit retrieval mode inventories all active conversations in the same
  session through the borrowed public catalog/event-store ports, while its
  captured-window mode projects one already-validated immutable focused
  context without widening authority. It owns content-free
  identities and revisions, exact physical artifact lines, canonical structured
  conversation/debate/owner-state bodies, and repr-hidden strict-UTF-8 captures.
  Active-debate records expose canonical public state first and durable messages
  afterward, keeping state independently searchable/readable even when the
  whole source exceeds the whole-handle cap. The module also owns record
  boundaries, deterministic NFKC/casefold literal search, bounded artifact
  context expansion, scan accounting, capture/re-read drift detection, and
  fixed source limits and failures.
- **Direct internal dependencies:** `agent_context.actions`,
  `agent_context.manifest`, `conversations`, `errors`, `models`, `workspace`,
  `writing_artifacts.filesystem_sources`, `writing_artifacts.references`, and
  `writing_artifacts.repository`.
- **State, resources, and side effects:** Adapters retain only borrowed public
  owner collaborators and immutable scope identities. The registry owns no
  cache, descriptor, lock, or file handle. Inventory performs stable
  list/capture/re-list validation; capture double-reads mutable owners, while
  captured immutable conversation windows are identity-validated once; search
  prechecks manifest byte facts, acquires sources in canonical manifest order,
  omits over-limit records without minting handles, and revalidates every
  touched source after selection. All source bodies and normalization buffers
  remain call-local. Importing performs no I/O.
- **Primary verification/documentation:**
  `tests/test_agent_context_sources.py`,
  `tests/test_agent_context_runtime_adoption.py`,
  `docs/design/agent-context.md`; module-inventory, dependency,
  facade, and import-boundary assertions across
  `tests/test_rag_module_and_packaging_boundary.py`,
  `tests/test_session_lifecycle.py`, and
  `tests/test_interactive_controller.py`.

### `story_writing_agents.agent_context.service`

- **Source:** `src/story_writing_agents/agent_context/service.py`
- **Responsibility:** Coordinate workspace-scoped provider-neutral contextual
  invocation lifecycles.
- **Supported surface:** No project-root or `agent_context`-facade-supported
  API. The direct-module package-internal `__all__` exposes
  `AgentContextService`, `ContextExecutionError`,
  `ContextExecutionErrorCode`, `ContextExecutionEvent`,
  `ContextExecutionEventKind`, `ContextExecutionEventSink`,
  `ContextInvocationBinding`,
  `ContextInvocationRequest`, `ContextInvocationResult`,
  `ContextInvocationSession`, `ContextInvocationState`, `ContextProvider`,
  `ContextProviderCallFinalizer`, `ContextProviderCallRequest`,
  `ContextProviderCallResult`, `ContextProviderFailureFacts`,
  `ContextProviderTerminalCode`, `ContextReferencePreludeRequest`,
  `ContextReferencePreludeResult`, `ContextReadReceipt`, `ContextScope`,
  `ContextSourceClassification`, `context_provider_failure_facts`, and
  `record_context_provider_failure`. `AgentContextService` derives the exact
  workspace identity from the public canonical Workspace root, composes a
  manifest and transient material from the four authorized source adapters,
  plans against the scoped ledger checkpoint, reserves INCOMPLETE before any
  physical provider call, and drives the finite SEARCH/READ/ANSWER loop through
  the service-owned `ContextProvider` inversion. Every substantive call uses
  one provider-neutral compact wire-v5 schema and a full-correlation-derived
  URL-safe token in the fixed four-field response envelope. Initial payloads
  carry compact current input plus only applicable candidate, named P2 document,
  record-patch, tombstone, and mandatory-reference sections; continuations carry
  only the exact untrusted observation. A later-round debate candidate remains
  full text unless a named base/target record pointer or maximal Unicode-code-
  point single splice reconstructs the exact latest P0 candidate from retained,
  resolved provider state and its complete digest/size facts; the service emits
  an operation only when its canonical JSON is strictly smaller than the full
  candidate. Candidate duplicate resolution precedes alias-keyed record-patch
  upsert and typed tombstone removal, while the durable ledger remains
  content-free. The per-call `q` tuple is the
  authoritative phase, dynamically available action, route-specific searchable
  kind, remaining-limit, and result-shape contract, and the strict decoder and
  session enforce that same tuple and canonicalize accepted structured results
  into the existing internal answer string before domain validation. SEARCH
  decodes one optional action-scoped artifact prefix, projects it across every
  internal query, filters the authorized manifest, and performs one shared
  bounded registry scan. Numeric source/record aliases and compact structured
  records avoid eager universal-catalog replay, while SEARCH/READ observations
  carry complete tagged source identity, kind, revision, digest, size,
  boundary, safe provenance, handle, and snippet/content evidence. No universal
  catalog or action-index-zero read handle is exposed:
  READ becomes available only for a fresh live handle returned by SEARCH in the
  current invocation, including zero-hit and consumed-handle suppression. A
  composite execution fingerprint binds the wire version and every planning,
  action, and source policy value to ledger and native-thread compatibility.
  Every bootstrap explicitly taints any prior contextual thread and starts a
  new native thread; incompatible checkpoints are stale and never resumed,
  while the pure planner still receives its planning-policy fingerprint. The
  synchronized call-local session retains exact reconstructed action
  correlation, route authorization, fresh HMAC search handles and safe
  locators, bounded counters, atomic one-use reads, delivery receipts, final
  source revalidation, and answer authorization. Mandatory-reference
  reservations stay
  separate from source search/read, use the unchanged provider prelude before
  the substantive action budget, and must complete before verified ledger
  commit. The two failure-fact helpers attach and recover only validated
  content-free physical call facts without replacing the original exception.
  Each substantive raw result carries one required, synchronous, repr-hidden,
  comparison-excluded `ContextProviderCallFinalizer`; the service invokes it
  exactly once after semantic acceptance or rejection, cancellation, or a
  closed post-response runtime failure. Search/read acceptance follows complete
  observation construction and lifecycle-event delivery; terminal-answer
  acceptance precedes post-commit observer delivery, and finalizer
  `BaseException` never replaces contextual state or control flow. The
  service-owned protocol keeps capture adaptation in `backend`; this module
  imports no exchange contract or store.
  Its supported direct-module-only `ContextExecutionEventKind`, frozen
  `ContextExecutionEvent`, and synchronous `ContextExecutionEventSink` expose
  fixed lifecycle outcomes with exact scope and operation identity, required
  phase/round correlation for debate events, and kind-specific optional
  content-free planning, action, mandatory-reference, provider-token,
  transcript, observation, and truncation facts. Nonapplicable facts are
  `None`; terminal provider input tokens are summed only when every submitted
  physical call supplies exact usage, and otherwise remain unavailable.
  Neither the `agent_context` facade nor the project-root facade re-exports
  these contracts. The
  optional synchronous answer validator receives the exact authorized answer
  and authorization after final source revalidation but before reference
  completion and READY commit, so invalid domain output cannot advance the
  checkpoint. `ContextSourceClassification` is strict-presence by default; its
  additive exact-bool `allow_absent` field is valid only for P2 hard
  constraints. The service still treats source inventory as authoritative,
  rejects every unmatched strict classification, and omits only an unmatched
  classification carrying that explicit conditional-presence fact.
- **Direct internal dependencies:** `agent_context.accounting`,
  `agent_context.actions`, `agent_context.ledger`, `agent_context.manifest`,
  `agent_context.planning`, `agent_context.sources`, and `workspace`.
- **State, resources, and side effects:** A workspace-scoped service retains
  immutable planning/action/source policies, one ledger collaborator, the
  content-free workspace digest, and process-local async locks keyed by exact
  scope/provider/definition/policy identity. Each invocation creates bounded
  transient manifests, source materials, provider payload mappings, call
  accounting, one current borrowed call finalizer, and an invocation session
  whose 32-byte HMAC secret, handle table, counters, and receipts are cleared
  on answer or failure. Source I/O is
  delegated to the injected registry adapters, ledger persistence remains
  ledger-owned, and provider transport/native-thread storage remains delegated
  through `ContextProvider`. Every physical call is appended in order; only a
  revalidated and synchronously domain-validated terminal answer commits READY.
  Typed lifecycle delivery is synchronous and inline. Observer failure before
  authority aborts and remains the primary invocation failure; failure while
  receiving `CHECKPOINT_ADVANCED` propagates after READY commit without
  rollback or a false not-advanced event; terminal delivery failure during an
  independent runtime failure is suppressed. Cancellation, timeout, malformed
  action,
  source/reference or answer-validation failure, exhaustion, or ambiguous
  submitted continuation taints the attempt and, where required, the native
  continuation. Importing performs no I/O, entropy acquisition, registration,
  or mutation; service construction performs no filesystem mutation.
- **Primary verification/documentation:**
  `tests/test_agent_context_service.py`,
  `tests/test_agent_context_execution.py`,
  `tests/test_agent_exchange_capture.py`,
  `tests/test_agent_context_runtime_adoption.py`,
  `tests/test_codex_backend.py`,
  `tests/test_antigravity_adapter.py`, `tests/test_antigravity_parity.py`,
  `docs/audits/issue-380-agent-context-conformance.md`,
  `docs/design/agent-context.md`, `docs/guides/agent-context.md`, and
  `docs/guides/agent-context-progress.md`;
  module-inventory, dependency, facade, and import-boundary assertions across
  `tests/test_rag_module_and_packaging_boundary.py`,
  `tests/test_session_lifecycle.py`, and
  `tests/test_interactive_controller.py`.

### `story_writing_agents.agent_context.ledger`

- **Source:** `src/story_writing_agents/agent_context/ledger.py`
- **Responsibility:** Persist and reconstruct one scoped, content-free
  agent-context checkpoint together with its exact planning and physical-call
  accounting.
- **Supported surface:** No project-root or `agent_context`-facade-supported
  API. The direct-module package-internal `__all__` exposes
  `CONTEXT_LEDGER_SCHEMA_VERSION`,
  `MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES`, `MAX_CONTEXT_LEDGER_SOURCES`,
  `MAX_CONTEXT_LEDGER_PROVIDER_CALLS`,
  `CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS`, `ContextLedger`, `ContextLedgerKey`,
  `ContextLedgerRecordState`, `ContextLedgerAttemptOutcome`,
  `ContextLedgerAttemptPlan`, `ContextLedgerProviderCall`,
  `ContextLedgerAttempt`, `ContextLedgerReadyCheckpoint`,
  `ContextLedgerRecord`, `ContextLedgerInspection`,
  `ContextLedgerCommitAuthorization`, `ContextLedgerError`,
  `ContextLedgerErrorCode`, `context_checkpoint_proposal_digest`, and
  `context_ledger_scope_key`. `ContextLedger` owns `inspect`,
  `reserve_attempt`, `append_provider_call`, `taint_attempt`, `commit_attempt`,
  and `invalidate` transitions with optimistic generation checks and exact
  idempotent replay. Its schema-v2 READY checkpoint durably retains the
  content-free universal source catalog, stable source aliases, per-source
  record aliases and high-water marks, canonical record cursors, exact proposal
  digest, and physical-call accounting. Codec validation, size/count limits,
  and digest reconstruction map malformed, duplicate, non-reusable, or
  inconsistent alias/cursor state to corruption without loading source bodies.
- **Direct internal dependencies:** `agent_context.accounting`,
  `agent_context.manifest`, `agent_context.planning`, and `workspace`.
- **State, resources, and side effects:** Sole owner of lazy schema-v2 records
  under `agent_states/orchestrator_state/context_ledger/v2/`, hash-only record
  names, a weak in-process writer registry, descriptor-relative traversal,
  `0600` POSIX advisory lock files, and same-directory atomic replacement with
  file and directory synchronization. Importing, construction, and a missing
  inspection perform no I/O mutation. Readers remain lock-free and use bounded
  verification and retry to return one complete predecessor or successor
  across atomic replacement. Prior schema-v1 records are intentionally neither
  read nor migrated. Stored values contain no prompts, bodies,
  excerpts, outputs, transcripts, exception text, reference descriptors, or
  projected units.
- **Primary verification/documentation:** `tests/test_agent_context_ledger.py`
  verifies the exact direct-module surface, immutable values and codecs,
  lifecycle and commit authorization, restart and corruption mapping,
  content-free privacy, limits, storage security and durability, lock-free and
  serialized concurrency, scope isolation, facade stability, and dependency
  graph. `docs/design/agent-context.md` documents the current checkpoint and
  ledger authority model.
  `tests/test_rag_module_and_packaging_boundary.py` verifies the exact four
  internal dependency edges; `tests/test_writing_artifacts.py` and
  `tests/test_interactive_pty.py` verify that internal ledger records remain
  excluded from canonical artifact discovery and the `$`/`$$` picker.

### `story_writing_agents.agent_context.manifest`

- **Source:** `src/story_writing_agents/agent_context/manifest.py`
- **Responsibility:** Define immutable content-free context-source manifests
  and their canonical classification and ordering rules.
- **Supported surface:** No project-root or `agent_context`-facade-supported
  API. The direct-module package-internal `__all__` exposes
  `ContextAuthorityKind`, `ContextManifest`, `ContextManifestEntry`,
  `ContextManifestError`, `ContextManifestErrorCode`, `ContextPriority`,
  `ContextProvenance`, `ContextScope`, `ContextSemanticChannel`,
  `ContextSourceKind`, `ContextSubjectKind`,
  `context_manifest_entry_key`, `validate_context_digest`, and
  `validate_context_identity`. These contracts validate bounded content-free
  identities, strict source/channel/priority/authority/searchability mappings,
  subject scope, provenance, lowercase SHA-256 identities, exact byte counts,
  unique source IDs, and locale-independent canonical order. Mutable writing
  artifacts remain P2/P3 sources rather than stable P1 definitions.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Owns frozen metadata values only.
  Validation and normalization use call-local memory; importing performs no
  I/O and creates no mutable state or external resource.
- **Primary verification/documentation:**
  `tests/test_agent_context_manifest.py` verifies the exact direct-module
  surface, immutable value and identity contracts, exhaustive classification,
  authority and searchability matrices, canonical ordering, privacy,
  standard-library-only imports, and compilation;
  `docs/design/agent-context.md` documents the content-free manifest boundary.

### `story_writing_agents.agent_context.planning`

- **Source:** `src/story_writing_agents/agent_context/planning.py`
- **Responsibility:** Produce deterministic budgeted bootstrap or delta
  projections from validated context manifests.
- **Supported surface:** No project-root or `agent_context`-facade-supported
  API. The direct-module package-internal `__all__` exposes
  `CONTEXT_CHECKPOINT_SCHEMA_VERSION`,
  `DEFAULT_CONTEXT_PLANNING_POLICY`,
  `MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES`,
  `ContextBootstrapReason`, `ContextBudgetAccounting`,
  `ContextCheckpointProposal`,
  `ContextCheckpointSource`, `ContextCheckpointState`,
  `ContextCheckpointView`, `ContextDecisionAccounting`,
  `ContextDecisionKind`, `ContextDeliveryMode`, `ContextMaterial`,
  `ContextPlanningConditions`, `ContextPlanningError`,
  `ContextPlanningErrorCode`, `ContextPlanningPolicy`,
  `ContextProjectedUnit`, `ContextProjection`,
  `ContextProjectionAccounting`, `ContextReferenceBudgetUsage`,
  `ContextSearchableAccounting`, `ContextSourceBudgetUsage`,
  `ContextSourceDecision`, `MandatoryReferenceReservation`, and
  `plan_context_projection`. The planner preserves exact P0 and mandatory
  reference treatment, bootstraps stable-context changes, suppresses only
  compatible unchanged non-P0 sources, deduplicates eligible same-channel
  P2/P3 material only after byte equality, atomically requires hard
  constraints, and applies deterministic class-budget first-fit-with-skip to
  other searchable sources. The default P3 budget is zero, making optional
  artifacts and own-agent history SEARCH-first while P2 working constraints
  remain eager. Unlisted package-internal record contracts keep pre-planning
  canonical cursors alias-free, pair accepted delivery records with positive
  stable aliases, and retain independent source and per-source record alias
  epochs/high-water marks. Pure planning preserves aliases for retained record
  identities, never reuses removed aliases, emits exact record patches and
  record/source/delivery tombstones, and resets alias epochs only for a new
  bootstrap thread. Content-free accounting partitions every source decision,
  exact unique/logical bytes, cap usage, reference reservations, and checkpoint
  proposal metadata.
- **Direct internal dependencies:** `agent_context.manifest`.
- **State, resources, and side effects:** Owns a frozen default policy and pure
  planning contracts only. Transient strict-UTF-8 bodies are caller-owned,
  immutable, hidden from representation, and retained only in returned
  projected units and record patches; no body enters errors, accounting,
  manifests, references, or checkpoint proposals. Planning performs no I/O,
  persistence, provider work, source authorization, clock/environment access,
  caching, or mutation.
- **Primary verification/documentation:**
  `tests/test_agent_context_planning.py` verifies the exact direct-module
  surface, policy, material and checkpoint validation, exhaustive decision and
  accounting invariants, bootstrap/delta precedence, deterministic packing,
  deduplication and collision handling, reference/proposal correlation,
  privacy, replay, facade/import boundaries, and no-I/O guarantees;
  `docs/design/agent-context.md` documents the current planning model.

### `story_writing_agents.backend`

- **Source:** `src/story_writing_agents/backend.py`
- **Responsibility:** Coordinate provider adapters under the provider-neutral
  session, lifecycle, retrieval, and reply contracts.
- **Supported surface:** Package-supported `AgentBackend` and `BoundAgent`.
  `BoundAgent` exposes structured debate `invoke` and one-turn conversation
  `converse` operations plus additive bounded
  `converse_with_retrieval(request, handler)`. Each operation also accepts the
  optional keyword-only, synchronous `reference_read` coordinator; omitting it
  preserves the exact established provider payload, schema, call count, and
  repository-I/O-free path. In addition to provider binding and cleanup,
  `AgentBackend` exposes the exact lifecycle operations
  `get_session_lifecycle(session_id)`, `list_session_lifecycles()`,
  `validate_provider_configuration(session_id)`,
  `acquire_session_access(session_id)`, `release_session_access(session_id)`,
  `create_session_with_access(session_id)`,
  `reopen_session_with_access(session_id)`, and
  `pause_session_and_release_access(session_id)`.
  The class also exposes exact active-roster execution-profile inspection and
  confirmation/thread-status reads. Package-internal
  `BackendRuntimeConfigurationCandidate` plus the backend's stage, validate,
  commit, discard, and recovery-reporting seams coordinate one quiescent hot
  activation without adding a project-root export.
  Unexported package-internal `backend_configuration_snapshot(backend)` exposes
  the same immutable construction-time configuration to the bound runtime
  without exposing the backend's private storage; unexported package-internal
  `release_bound_agent(bound_agent)` requests exact binding retirement without
  exposing or mutating backend collections. `BoundAgent.context_provider()` is
  the synchronous package-internal contextual transport capability, with the
  original private spelling retained as a compatibility delegate. Its pure
  `decode_context_conversation_answer`, `decode_context_retrieval_answer`, and
  `decode_context_agent_answer` helpers reuse the backend's canonical domain
  decoders and enforce exact request correlation before orchestration can
  authorize a contextual checkpoint.
- **Direct internal dependencies:** `agent_context`, `agent_context.service`,
  the supported `agent_exchanges` facade, the internal
  `agent_exchanges.capture` module, `agents`, `antigravity_adapter`,
  `codex_adapter`, `config`,
  `conversation_retrieval`, `debug_log`, `errors`, `interactive_settings`,
  `models`, `provider_adapter`, `provider_error_artifacts`, `provider_process`,
  `session_lifecycle`, and
  `workspace`, plus the provider-neutral `writing_artifacts.read_actions`
  boundary.
- **State, resources, and side effects:** Owns exact V2 provider-session
  manifests at
  `agent_states/orchestrator_state/provider_sessions/<session_id>.json`, exact
  V2 process-access claims at sibling `<session_id>.access.json` paths, the
  lazily created workspace-global `.access-claims.lock` OS advisory guard,
  in-memory claim tokens, persisted `provider_threads`, shared
  debate/conversation/retrieval per-thread locks, distinct contextual-thread
  locks, adapter routing, and structured domain-response validation. Manifests
  contain only `schema_version`,
  `session_id`, `durable_state`, `created_at`, `updated_at`, and
  `provider_threads`; claims contain only `schema_version`, `session_id`,
  `owner_hostname`, `owner_pid`, `claim_token`, and `claimed_at`. The guard
  serializes claim creation and proved-dead local replacement; live local,
  remote, inaccessible, unsupported-platform, and ambiguous ownership is
  handled conservatively, and release removes only the exact locally held
  token. Version-1 manifests, access sidecars, and access guards are rejected
  with `WorkspaceVersionUnsupportedError` without migration, deletion, or
  rewriting. The backend also owns
  timeout/cancellation taint handling, provider cleanup coordination, and
  per-instance `atexit`
  registration. Each concrete `BoundAgent` privately owns synchronized
  usable/active/retired/released state and an active-call count spanning its
  `invoke`, `converse`, and `converse_with_retrieval` operations. Retirement
  rejects later calls but retains its immutable prompt staging through every
  already-entered call, and the final exit asks the selected adapter to release
  that exact binding once. Codex removes exact ephemeral staging idempotently;
  Antigravity retains provider-native definition state. Binding validation
  failure removes provisional Codex staging before propagation.
  Backend close is terminal and idempotent: it retires all concrete bindings
  immediately, then defers access-claim release, in-memory thread-lock cleanup,
  and remaining adapter cleanup until no active binding can use them. One
  retrieval thread lock covers the complete repeated-call action loop and
  synchronous handler invocations. For the legacy conversation-retrieval path,
  the backend owns the exact provider-neutral action schemas, initial and
  continuation payloads, canonical observation framing handoff, provider
  timeout/cancellation behavior, incomplete-history taint, and fixed
  provider-labelled, provider-value-free process/response-validation
  diagnostics. It drains both
  subprocess output streams through its adapters but discards and never decodes
  stderr after a nonzero provider exit; it receives no memory service and
  performs no retrieval search or transcript-storage call. Only after an
  adapter returns a completed `ProviderResult`, rejection of its exact
  `response_text` by the shared structured decoder with `BackendProtocolError`,
  `JSONDecodeError`, or `UnicodeError` submits that text, provider, canonical
  agent, session, and the applicable debate or conversation correlation to the
  private `provider_error_artifacts` owner. The submission catches
  `BaseException`, so artifact-storage failure cannot replace the original
  decoding exception or change established thread taint, restart, cleanup,
  cancellation, output, exit, or retry behavior. Pre-result/provider-invocation
  failures, requests, prompts, accepted replies, stderr or raw provider streams,
  credentials, authentication output, and provider-native history remain
  ineligible; response bodies are neither rendered nor sent through
  `debug_log`. Independently, the backend retains one construction-time,
  I/O-free `WorkspaceAgentExchangeCapture` and invokes it only at the two
  physical structured-call seams. Each exactly correlated conversation or
  debate call records its canonical schema, payload, prior thread, and call
  kind before adapter invocation; a valid completed `ProviderResult` is
  submitted immediately after adapter return and before accounting observation,
  decoding, rejected-response artifact submission, or thread persistence. The
  same successfully begun exchange identity then receives exactly one closed
  accepted, rejected, provider-failure, cancelled, or post-response
  runtime-failure terminal attempt. Legacy outcomes finalize locally;
  substantive context results carry
  a service-owned finalizer adapter until the contextual action lifecycle knows
  its outcome, while mandatory-reference preludes finalize in the backend after
  decode, authorization, handling, and context construction. Missing or
  ambiguous legacy correlation disables capture, and every capture call catches
  `BaseException` without changing provider execution, accounting, thread,
  taint, decoding, artifact, reference, cancellation, or exception semantics.
  The capture sees only provider-neutral invocation/result fields and fixed
  terminal codes, never stderr, stdout, native NDJSON, authentication,
  credentials, environment values, or arbitrary exception text; its package
  remains the sole exchange schema, state, and storage owner. For a referenced
  logical invocation, the
  same thread lock first runs one strict content-free read-action prelude. The
  provider must echo the exact correlation and complete ordered path/range
  batch under a request-specific output schema before the synchronous
  coordinator can expose bytes. The backend frames those bytes once, in order,
  as `untrusted_writing_artifact_context` only on the next substantive provider
  payload, closes the transient result, and returns the reply only after
  result-received and acceptance callbacks succeed. Direct and debate work add
  one physical prelude call; retrieval places that prelude outside its unchanged
  one-to-five-call action budget and supplies the frame only to retrieval call
  one. An attempted authorized conversation-RAG preflight adds one exact
  `rag_context` object only to retrieval call one, frames its excerpts as
  untrusted story data, and permits their deterministic `g1` through `g20`
  citation identities in the existing answer action. Continuations omit that
  context while retaining its citation authorization through the provider
  thread. A request with no RAG attempt preserves the exact established
  payload keys and retrieval rules. Malformed, mismatched, broadened, partial,
  repeated, provider, drift,
  workflow, interruption, and cancellation failures terminate with content-free
  outcomes; any failure after a persisted successful prelude best-effort taints
  the provider thread without replacing the primary exception. When a request
  carries `story_session_context`, debate, advisory, and retrieval initial
  payloads serialize that exact bounded envelope and omit their legacy owner-
  state/focused-context projections; omission preserves the established payload
  for compatibility construction. Operation-specific input, candidates,
  messages, and retrieval controls remain separate payload fields. For every
  physical provider-call boundary, the backend classifies the existing
  schema and payload values without changing their shape, identifies raw
  mandatory-reference bodies separately from their descriptors, and combines
  the adapter-owned binding snapshot with canonical schema/payload accounting.
  Its optional private, call-local accounting sink receives content-free
  prepared or submitted records for successful, failed, and cancelled calls;
  sink absence or failure cannot change provider, decoding, thread, retrieval,
  or reference behavior, and no accounting record is persisted. Adapter-owned
  exact transport-byte and recognized usage facts complete a submitted record;
  unavailable usage remains absent and is never estimated. At the contextual
  boundary, a binding-scoped adapter implements the service-owned
  `ContextProvider` protocol without giving the backend planner, ledger, or
  source authority. It retains the bound provider for the complete lifecycle,
  runs the existing exact reference prelude when requested, builds one
  `ProviderInvocation` from each service-supplied schema/payload/component
  mapping, passes its single canonical `payload_json` to the selected adapter,
  and returns raw response, thread identity, and exact content-free accounting
  for service-owned action decoding and ledger transitions. Contextual native
  continuations are isolated from legacy `provider_threads` beneath
  `agent_states/orchestrator_state/provider_sessions/.contextual/v1/<session-id>/<scope-hash>.json`;
  their key binds the complete scope, provider, definition fingerprint, and
  policy fingerprint, and their content-free schema stores only that key,
  thread identity, taint, and update facts. Missing, malformed, mismatched, or
  tainted records yield bootstrap rather than implicit reuse. Submitted
  cancellation, timeout, malformed result, continuation mismatch, and failed
  reference completion preserve fixed failure facts and taint the exact
  contextual continuation without mutating the legacy thread slot. At
  construction it validates and snapshots the complete workspace system and
  per-agent configuration, reconciles the settings-owned durable provider-route
  generations, and solely resolves the roster's authoritative immutable
  provider/model/effort values and per-value sources. It passes those exact
  profiles to both adapters, so command construction and displayed/logged
  identity consume the same values rather than independently repeating
  precedence. Null model or effort remains an explicit provider-default fact.
  It preserves compatibility delegates for previously test-observed private
  Codex helpers.
  The Antigravity settings are additive keyword-only constructor inputs.
  Launch overrides retain precedence for the backend lifetime. A staged hot
  configuration owns isolated preflighted adapters and cannot commit unless its
  base generation is still active and all bindings are quiescent. Commit swaps
  the complete immutable configuration/profile/adapter generation in memory,
  retires the former generation, and closes it only after its final binding is
  released; discard closes only an uncommitted candidate. Same-provider
  model/effort changes remain eligible to resume the existing native thread.
  Every provider change advances the settings-owned route generation, which the
  prompt fingerprint uses to select a fresh target-provider thread even when
  switching back to a previously used provider; other backend instances retain
  their captured generation and remain unchanged. Successful logical calls
  alone confirm an active profile and record whether its provider thread was
  fresh, reused, or fresh after a switch and whether bounded context was
  rehydrated. Each backend retains one I/O-free workspace-bound
  debug-log handle but begins emitting only after the existing complete
  configuration snapshot loads successfully. It best-effort records only fixed
  configuration, execution-profile application/transition/confirmation,
  binding, provider-invocation, session-lifecycle, and cleanup facts or safe
  failures. Only after complete successful backend construction, it emits one
  `startup`-correlated schema-v2 `execution_profile_applied` active snapshot for
  every canonical agent in roster order, carrying the exact active profile and
  route generation without fabricating prior values. Later hot activation emits
  old/new applied transitions only for changed profiles. Profile events carry
  exact sanitized provider/model/effort values and sources through the
  `debug_log` seam; unchanged, cancelled, or failed hot activation emits no
  successful transition. Lifecycle diagnostics cover
  complete enumeration and
  the create, reopen, and pause boundaries, including acquisition, rejection,
  durable mutation, release, and final lifecycle reads without changing their
  established cleanup or exception order. Backend-private trace and error
  guards catch diagnostic `BaseException` failures. Apart from the explicit
  sanitized execution-profile values, diagnostic calls never persist provider
  stderr, payloads, replies, other configuration values, credentials, or
  access-claim owner/token facts and never replace the established result,
  exception, or cancellation flow; `debug_log` retains ownership of its files,
  locks, schema, and rotation.
- **Primary verification/documentation:**
  `tests/test_agent_context_execution.py`,
  `tests/test_agent_exchange_capture.py`,
  `tests/test_agent_context_runtime_adoption.py`, `tests/test_codex_backend.py`,
  `tests/test_prompts.py`,
  `tests/test_conversation_retrieval.py`,
  `tests/test_antigravity_adapter.py`, `tests/test_antigravity_parity.py`,
  `tests/test_session_lifecycle.py`, `docs/reference/backend-and-sessions.md`,
  `docs/guides/antigravity.md`, `docs/guides/lead-agent.md`,
  `docs/reference/conversation-retrieval.md`,
  `docs/design/prompts-and-provider.md`, `docs/design/session-lifecycle.md`, and
  `docs/operations/privacy-and-provider.md`, plus
  `docs/operations/recovery-and-limitations.md` for bounded ephemeral-resource
  cleanup and process-exit limits.

### `story_writing_agents.antigravity_adapter`

- **Source:** `src/story_writing_agents/antigravity_adapter.py`
- **Responsibility:** Adapt immutable agent bindings and structured turns to the
  Antigravity CLI protocol.
- **Supported surface:** No package-supported API. Package-internal
  `AntigravityAdapter` implements the private provider-adapter contract.
- **Direct internal dependencies:** `agent_context`, `models`,
  `provider_adapter`, and `provider_process`.
- **State, resources, and side effects:** Owns the Antigravity executable,
  defensive immutable copies of explicit/system/per-agent model and effort
  primitives, bound-agent selection, exact explicit-over-agent-over-system-
  over-provider-default precedence, isolated provider state, command
  construction, headless capability check, NDJSON stream validation, and
  provider conversation-ID protocol. When supplied by the backend, the
  roster-filtered immutable execution profiles are the exact model/effort
  source for `--model` and `--effort`; the older private precedence path remains
  a compatibility fallback for direct adapter construction. Per-binding release
  deliberately retains provider-native definition and conversation state for
  later backend instances.
  Direct binding constructs the established deterministic rules bytes and exact
  same-snapshot content-free component partition for binding structure, role,
  persona, language, each canonical core-document path, and provider
  constraints. A definition's internal `provider_managed_context` marker instead
  selects the compact contextual rules: the same role/persona/language facts
  plus the symmetric wire-v5 grammar for untrusted evidence, action-scoped
  prefix search, the per-call `q` contract, retained-state candidate
  reconstruction, alias-keyed patch upsert, and typed tombstone removal, with
  no bound core-document bytes. `agent_context.service` and
  `agent_context.actions` retain the inner protocol semantics; Codex and
  Antigravity stage equivalent contextual instructions while their direct
  definitions remain byte-exact. Antigravity retains those rules beneath
  `agent_states/orchestrator_state/provider_sessions/.antigravity/definitions/<agent>/<fingerprint>/AGENTS.md`.
  Before definition writes or provider launch, the adapter sequentially
  prepares its owned `state_root`, definitions chain, sessions/runtime chain,
  and provider/workspace directories. On supported systems it best-effort
  requests owner-only directory modes and rejects detected links or
  non-directories in those chains. It does not harden the backend-owned
  containing parent, provider-created files, or capability-probe scratch.
  Provider-native retention remains unchanged, and this hardening does not
  claim application access control or descriptor-anchored, race-proof
  containment.
  Binding and per-turn workspace staging install canonical rules bytes through
  an exclusively created same-directory temporary requested as `0600`, verify
  that temporary's regular singly linked identity, and atomically replace the
  target. Existing staged or retained-workspace `AGENTS.md` targets are
  accepted only when missing or regular and singly linked; detected symbolic
  links, non-regular entries, and multiply linked files are preserved and
  rejected. Invocation also reads the staged source through a descriptor
  verified as regular, singly linked, and identity-matched to its no-follow
  pathname before installation. These checks prevent final-leaf redirection;
  an unsafe invocation fails before provider launch without tainting compatible
  history. Parent traversal remains pathname-based rather than descriptor-
  anchored, so these rules-file protections likewise do not claim application
  access control or race-proof containment.
  Invocation uses the shared canonical encoder for its output schema and
  payload, then accounts for the exact provider-specific NDJSON stdin bytes
  before creating any new per-session runtime tree; encoding failure therefore
  creates no new provider or workspace directory. Process creation distinguishes
  prepared from submitted failures without replacing established diagnostics,
  and only recognized nonnegative terminal usage counters become exact usage;
  missing or malformed counters remain unavailable. After serialization, it
  retains provider-native state and isolated cwd beneath
  `agent_states/orchestrator_state/provider_sessions/.antigravity/sessions/<session>/<agent>/<fingerprint>/provider`
  and sibling `workspace`, copies only the bound rules into that workspace,
  sends the story request as one NDJSON stdin event, and delegates bounded
  `agy` subprocess ownership to `provider_process`. Provider preflight runs a
  bounded, provider-free `agy --help` capability check with an isolated
  `--gemini_dir`. Its fixed `state_root/capability-probe` scratch directory is
  best-effort removed after every capability check and is not retained
  provider-native state. `close()` intentionally retains definitions, session
  workspaces, and provider-native conversation state for later backend
  instances. Importing performs no I/O.
- **Primary verification/documentation:** `tests/test_antigravity_adapter.py`
  and `tests/test_antigravity_parity.py`; shared adapter and subprocess
  contracts are also verified by `tests/test_codex_backend.py`.
  `docs/guides/antigravity.md` is the dedicated setup, transport, lifecycle,
  retention, and provider-free verification guide.

### `story_writing_agents.codex_adapter`

- **Source:** `src/story_writing_agents/codex_adapter.py`
- **Responsibility:** Adapt immutable agent bindings and structured turns to the
  Codex CLI protocol.
- **Supported surface:** No package-supported API. Package-internal
  `CodexAdapter` implements the private provider-adapter contract.
- **Direct internal dependencies:** `agent_context`, `agents`, `config`,
  `errors`, `models`, `provider_adapter`, and `provider_process`.
- **State, resources, and side effects:** Owns construction-time Codex
  executable/model/reasoning settings, isolated binding directories and their
  generated `AGENTS.md` files plus exact same-snapshot content-free component
  partitions, invocation-scoped schema/final-response directories, exact Codex
  argv construction, JSONL event and thread parsing, and provider-specific safe
  process diagnostics. Direct binding partitions distinguish structure, role,
  persona, language, every canonical core-document path, and provider
  constraints without changing their established bytes. A definition's
  internal `provider_managed_context` marker instead selects the compact
  contextual binding shared semantically with Antigravity: role, persona,
  language, and the symmetric wire-v5 grammar for untrusted evidence,
  action-scoped prefix search, the authoritative per-call `q` contract,
  retained-state candidate reconstruction, alias-keyed patch upsert, and typed
  tombstone removal, with no bound core-document bytes.
  `agent_context.service` and `agent_context.actions` retain the inner protocol
  semantics, and direct-definition bytes remain unchanged.
  Partitions are retained only with their corresponding ephemeral staging
  directory.
  Invocation uses shared canonical schema and payload bytes, distinguishes
  prepared from submitted failures, reports exact stdin bytes, and accepts
  usage only from one recognized `turn.completed` event with nonnegative exact
  counters. Missing or malformed usage remains unavailable. When supplied by
  the backend,
  the roster-filtered immutable execution profiles are the exact model/effort
  source for command flags; the older private resolution path remains a
  compatibility fallback for direct adapter construction. Exact per-binding
  release removes only that binding's tracked ephemeral staging idempotently; close
  removes any remaining tracked bindings. Binding and invocation read
  allowlisted definition files, create and remove temporary files, and request
  subprocess execution through `provider_process`; `close()` removes all
  retained binding directories idempotently. Optional `--model` and `--config
  model_reasoning_effort="LEVEL"` arguments retain their exact order after
  `exec` and before any resume marker. Importing performs no I/O.
- **Primary verification/documentation:** `tests/test_codex_backend.py`,
  `docs/reference/backend-and-sessions.md`,
  `docs/design/prompts-and-provider.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.provider_adapter`

- **Source:** `src/story_writing_agents/provider_adapter.py`
- **Responsibility:** Define the private provider substitution contract.
- **Supported surface:** No package-supported API. Package-internal frozen
  `ExecutionValueSource` and `AgentExecutionProfile` define the immutable
  provider/model/effort identity and authoritative source of every effective
  value. Frozen `ProviderBinding` carries an exact binding-component snapshot;
  `ProviderInvocation` retains compatibility mappings while hiding those
  content-bearing mappings and canonical payload bytes from representation and
  exposes prepared content-free accounting;
  `ProviderResult` additively carries optional exact usage and transport-byte
  facts without changing value equality. `ProviderFailureAccounting`,
  `record_provider_failure_accounting`, and `provider_failure_accounting`
  provide the coordinator and concrete adapters one validated package-internal
  prepared/submitted failure seam. `ProviderInvocationError` and the
  `ProviderAdapter` protocol retain their existing substitution and taint
  contracts, including exact idempotent `release(binding)` retirement.
- **Direct internal dependencies:** `agent_context` and `models`.
- **State, resources, and side effects:** Owns no mutable module state or
  external resource. Construction creates only immutable mappings, canonical
  call-local bytes, and content-free accounting; failure helpers attach only
  validated content-free facts to the current exception. Importing performs no
  I/O.
- **Primary verification/documentation:**
  `tests/test_agent_context_accounting.py`, `tests/test_codex_backend.py`,
  `tests/test_antigravity_adapter.py`, `tests/test_antigravity_parity.py`, and
  `docs/design/overview.md`.

### `story_writing_agents.provider_error_artifacts`

- **Source:** `src/story_writing_agents/provider_error_artifacts.py`
- **Responsibility:** Persist bounded rejected provider responses as private
  workspace artifacts.
- **Supported surface:** No package-supported or project-root API. The
  module-supported internal surface is `WorkspaceProviderErrorArtifacts`, its
  I/O-free `directory` projection, the exact keyword-only
  `record_rejected_response(...)` seam, and the fixed path, schema-category,
  body/file/retention bounds, and lock-timeout constants listed in the module's
  `__all__`. `backend` is the sole production consumer of the recording seam.
- **Direct internal dependencies:** `agents` and `workspace`.
- **State, resources, and side effects:** Import and construction perform no
  filesystem work. One storage call validates an exact `codex` or
  `antigravity` provider, canonical agent and session identities, at most one
  debate/conversation correlation, the fixed `structured_response_rejected`
  category, and strict-UTF-8 provider-controlled plaintext. Schema-v1 compact
  UTF-8 JSON records retain the exact body up to 262,144 bytes at a complete
  code-point boundary plus full-response byte count and SHA-256; one serialized
  record is limited to 2,097,152 bytes. Complete timestamp-and-UUID artifacts
  live under `debug_logs/provider_error_responses/`, with at most 32 files and
  16,777,216 serialized bytes. The owner holds a weakly registered
  per-workspace process lock and makes a bounded 100-millisecond attempt on the
  independent `.lock` advisory guard. It borrows `Workspace`'s identity-bound
  root open/reverification seam, then holds and reverifies the root,
  `debug_logs`, and artifact-directory descriptors. All namespace operations
  are descriptor-relative and no-follow; directories must retain exact spelling
  and identity, and leaf files must be regular, singly linked, size-bounded,
  stably readable, strict-UTF-8, duplicate-free canonical JSON with the exact
  managed schema. Unknown, malformed, aliased, symlinked, hard-linked,
  nonregular, oversized, replaced, or concurrently changed state fails closed
  without pruning that state. Under the acquired guard, the owner removes only
  a verified stale fixed `.pending`, synchronizes a new exclusive pending file,
  validates existing complete artifacts before oldest-first pruning, and uses
  the platform's native atomic no-replace rename before final directory
  synchronization and validation. Unsupported safe primitives or native
  no-replace publication fail before using a weaker fallback; bounded lock
  contention drops the attempted artifact. Storage failures and process-control
  exceptions propagate to the caller, no redaction or terminal rendering occurs,
  and ordinary deletion is not secure erasure.
- **Primary verification/documentation:**
  `tests/test_provider_error_artifacts.py`,
  `docs/operations/privacy-and-provider.md#rejected-provider-response-artifacts`, and
  `docs/reference/configuration/session-storage.md#rejected-provider-response-storage`.

### `story_writing_agents.provider_process`

- **Source:** `src/story_writing_agents/provider_process.py`
- **Responsibility:** Execute bounded asynchronous provider subprocesses.
- **Supported surface:** No package-supported API. Package-internal immutable
  process request/result values, value-free launch/timeout failures,
  `run_process`, and `stop_process` are consumed by provider adapters; the
  backend retains only a private compatibility delegate for process stopping.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** `run_process` launches one child with
  piped stdin/stdout/stderr, drains both streams concurrently through
  `communicate`, and enforces the supplied timeout. On timeout, cancellation,
  or any other post-launch communication failure, it stops a still-running
  child by requesting termination, waiting up to two seconds, then killing and
  joining when necessary; completed children are not signaled. Optional cwd
  and environment are explicit request values. The module owns no persistent
  or import-time state and emits no provider-specific diagnostic.
- **Primary verification/documentation:** `tests/test_codex_backend.py`,
  `tests/test_antigravity_parity.py`,
  `docs/reference/backend-and-sessions.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.provider_catalog`

- **Source:** `src/story_writing_agents/provider_catalog.py`
- **Responsibility:** Discover sanitized native-provider model and effort
  catalogs within bounded synchronous subprocess lifecycles.
- **Supported surface:** No package-supported API. Package-internal frozen,
  slotted `ProviderEffort`, `ProviderModel`, `ProviderCatalog`, and
  `ProviderCatalogDiscovery` projections; the exact `ProviderCatalogState`
  vocabulary `ready`, `unavailable`, `timed_out`, `command_failed`,
  `malformed`, `empty`, `oversized`, and `failed`; and
  `ProviderCatalogService` support CLI/controller composition without entering
  the project-root facade.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Construction validates and retains
  one existing workspace-root directory, exact Codex and Antigravity executable
  commands, and an injectable process factory without starting a process.
  Every explicit discovery is fresh and launches exact `codex debug models
  --bundled` or `agy models` arguments in that workspace root with inherited
  environment and PATH, `shell=False`, discarded stdin/stderr, and piped stdout.
  The module alone owns the discovery child, bounded reader thread, five-second
  deadline, one-MiB retained-byte ceiling, and terminate/wait/kill/wait plus
  close/join cleanup. It strictly decodes and projects provider output into
  ordered validated immutable values, then discards the raw bytes. Fixed states
  replace command diagnostics; no output, stderr, return code, cache, retry,
  persistence, credential, provider turn, or import-time resource crosses the
  boundary. Python process-control exceptions are re-raised after cleanup.
- **Primary verification/documentation:** `tests/test_provider_catalog.py`,
  `tests/test_interactive_controller.py`, `tests/test_public_api_and_cli.py`,
  `docs/design/interactive-cli-design-system.md`,
  `docs/reference/interactive-terminal.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/guides/antigravity.md`.

### `story_writing_agents.provider_auth`

- **Source:** `src/story_writing_agents/provider_auth.py`
- **Responsibility:** Own native provider authentication status and login
  process lifecycles.
- **Supported surface:** No package-supported API. Package-internal immutable
  `ProviderAuthSpec`, `ProviderAuthStatus`, `ProviderAuthSnapshot`, and
  `ProviderLoginResult` values, the exact `ProviderAuthState` vocabulary
  `authenticated`, `unauthenticated`, `unavailable`, and `indeterminate`, and
  `ProviderAuthService` support interactive composition without entering the
  project-root facade.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Construction validates and retains one
  existing workspace-root directory, exact Codex and Antigravity executable
  commands, and an injectable process factory without performing I/O. Status
  probes run exact `codex login status` and `agy models` commands synchronously
  in that workspace root with inherited environment and PATH, `shell=False`,
  discarded standard streams, and a fixed five-second bound; output is never
  captured, parsed, retained, returned, or logged. Native login runs exact
  `codex login` or bare `agy` in the same CWD with inherited real terminal
  streams and environment. While that exact child remains open, the service
  waits in bounded intervals and runs the same silent selected-provider status
  probe; a fresh authenticated state boundedly stops and reaps the still-open
  child before returning control. If the child exits first, the service runs
  one final fresh probe. It also performs bounded terminate/kill/reap cleanup
  after Python-side failure or interruption, and only a probe's exact
  authenticated state reports success. It owns no credential, token, account,
  URL, browser, keyring, configuration, workspace persistence, provider turn,
  or import-time resource.
- **Primary verification/documentation:** `tests/test_provider_auth.py` verifies
  the native authentication contract, `tests/test_interactive_pty.py` verifies
  exact-child and terminal reclamation for both providers, and
  `tests/test_public_api_and_cli.py` verifies the unchanged package facade.
  `docs/operations/privacy-and-provider.md` documents the native authentication
  process and credential boundary.

### `story_writing_agents.cli`

- **Source:** `src/story_writing_agents/cli.py`
- **Responsibility:** Parse and dispatch the retained CLI management,
  inspection, storage, provider-free RAG, smoke, and interactive-shell commands.
- **Supported surface:** Script entry point `story_writing_agents.cli:main`
  for both console aliases. The grammar contains `chat`, `init`,
  `directive`, `inspect`, `session`, `storage`, `rag`, and `smoke`;
  the former `run` parser, flags, input resolver, dispatch, and result
  presentation are absent. Backend-override parsing remains shared by retained
  commands.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`,
  `config`, `conversation_rag_cli`, `conversation_storage.contracts`,
  `conversation_storage.migration`, `conversation_storage.selection`,
  `debug_log`, `errors`, `interactive_auth`,
  `interactive_controller`, `interactive_settings`,
  `interactive_terminal`, `orchestrator`, `provider_auth`,
  `provider_catalog`, `session_lifecycle`, `smoke`, and `workspace`.
- **State, resources, and side effects:** Argument parsing is resource-free;
  retained dispatch delegates state and resources to the selected owner.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`
  and command-specific CLI tests verify the retained grammar and dispatch.

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
  rewriting; malformed V2 data raises `WorkspaceFormatError`. Workspace
  discovery requires initialized configuration, agent prompts, and directory
  sentinels but does not require agent-authored core artifacts to exist.
- **Primary verification/documentation:** `tests/test_cli_context.py`,
  `docs/reference/cli.md`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/design/session-lifecycle.md`.

### `story_writing_agents.config`

- **Source:** `src/story_writing_agents/config.py`
- **Responsibility:** Validate, resolve, and publish complete runtime
  configuration.
- **Supported surface:** Internal `ConfigManager`, `SystemDefaults`,
  `AgentConfig`, `ResolvedAgentConfig`, immutable roster-complete
  `ConfigurationSnapshot`, exact `KNOWN_AGENT_BACKENDS` value
  `("codex", "antigravity")`, and independent exact
  `KNOWN_CONVERSATION_STORAGE_BACKENDS` value `("filesystem", "sqlite")`
  plus independent exact `KNOWN_CONVERSATION_RAG_PROVIDERS` value
  `("local",)`;
  legacy `gemini` values receive fixed migration guidance. Package-internal
  `ConfigurationBaseline`, `ConfigurationCommitFailure`, and
  `ConfigurationCommitError` define one exact-byte complete-configuration
  compare-and-set boundary. `SystemDefaults` retains all established positional
  fields, keeps optional nullable `conversation_storage_backend` after the
  optional workspace `default_backend`, and appends strict
  `show_message_details=False`, nullable `conversation_rag_provider`, and
  `conversation_rag_automatic=False` in that order; old positional construction
  remains valid.
  `AgentConfig` retains its seven-field order while allowing its leading
  backend route to be null. `ConfigManager` owns both complete persisted schemas
  plus provider-specific safe-model validators, an additive forward-compatible
  lowercase Codex effort-token validator, and the exact Antigravity
  low/medium/high effort validator, together with the independent exact
  filesystem/SQLite storage validator, strict message-details boolean
  validator, independent nullable allowlisted local
  RAG-provider validation, and exact automatic boolean validation. Automatic
  retrieval requires the local provider. Provider-catalog membership is not a
  persisted-schema requirement. Missing legacy storage selection is interpreted
  as filesystem when projecting a saved canonical document; missing message
  details, missing or null RAG provider, and missing automatic policy load as
  false/null/false without a rewrite, while missing default and agent backend
  keys remain null. Package-internal
  `validate_configuration` validates both persisted documents as one complete
  candidate, `load_configuration_baseline` captures exact bytes and one
  immutable value, `commit_configuration` validates and compare-and-sets both
  complete documents, `load_configuration_snapshot` projects the baseline's
  value, and `resolve_from_snapshot` applies live runtime values while resolving
  providers in exact CLI override, explicit agent route, workspace default,
  then Codex order. Existing on-demand loaders and `resolve` retain their
  signatures and delegate to the same validation and resolution rules.
- **Direct internal dependencies:** `agents`, `errors`, and `workspace`.
- **State, resources, and side effects:** Owns strict-UTF-8 reads, complete
  schema validation, deterministic projection of canonical twelve-key system
  defaults and roster-complete agent configuration, and the configuration CAS
  lifecycle. Commit first compares both exact baseline byte strings, writes
  only changed complete documents through `Workspace` in system-then-agent
  order, and attempts reverse-order restoration of every attempted target on
  failure. Validation, conflict, write, and rollback failures are fixed and
  content-free. It owns no conversation-storage readiness, installer,
  provisioning, parity, activation, provider, or import-time resource.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `tests/test_prompts.py`, `tests/test_codex_backend.py`,
  `tests/test_interactive_settings.py`,
  `docs/reference/configuration/agent-configuration.md`, and
  `docs/reference/configuration/runtime-overrides.md`.

### `story_writing_agents.conversation_rag_cli`

- **Source:** `src/story_writing_agents/conversation_rag_cli.py`
- **Responsibility:** Own fixed local conversation-RAG command requests,
  workflows, privacy projection, and schema-v1 reports independently of
  argparse and interactive control.
- **Supported surface:** Exactly seven direct-module names in `__all__`:
  `CONVERSATION_RAG_CLI_SCHEMA_VERSION`, `ConversationRagCliFailure`,
  `ConversationRagCliOperation`, `ConversationRagCliReport`,
  `ConversationRagCliRequest`, `ConversationRagCliState`, and
  `dispatch_conversation_rag`. Operations are exactly status, enable, rebuild,
  search, disable, and purge; states are disabled, unavailable, missing, ready,
  stale, failed, corrupt, and incompatible. Frozen requests bind workspace- or
  session-scoped arguments, validate those scopes and the exact provider
  through their established owners, reject blank or invalid-UTF-8 queries
  before dispatch, hide valid search queries from representations, and require
  explicit confirmation only for rebuild/purge. Frozen reports retain one
  stable compact sorted JSON key set, capped and correlated authoritative/
  indexed/purge counts, content-free compatibility/configuration/failure facts,
  exact checkpoint tokens, rebuild need, and optional retrieval facts. Their
  construction enforces operation-specific success/failure payloads. Search
  reports alone may carry bounded authorized excerpt text plus revalidated
  citation identities, event correlation, source identities, and half-open
  ranges; they omit query text, subjects, provider scores/candidates, paths,
  credentials, exception text, and generation identities. No name is
  re-exported by the 118-name package-root facade.
- **Direct internal dependencies:** `config`, `conversation_rag`,
  `conversation_rag_composition`, `conversation_rag_local`,
  `conversation_rag_runtime`, `conversation_rag_store`,
  `conversation_retrieval`, `conversation_search`,
  `conversation_storage.contracts`, `conversation_storage.selection`,
  `conversations`, `errors`, and `workspace`.
- **State, resources, and side effects:** The module retains no process-global
  state or resource and performs no import-time I/O, dependency probe, provider
  construction, or dynamic discovery. Status loads validated configuration,
  asks selection for an optimistically stable read-only active conversation-
  storage bundle, probes local-package compatibility without service
  construction, and double-checks both the verified derived-store snapshot and
  runtime's full optimistic authoritative/indexed observation. A successful
  non-ready observation is a completed exit-zero report. Enable validates and
  probes the exact local provider before the config-owned complete-document
  CAS and preserves an existing valid automatic policy. Disable atomically
  writes provider null/automatic false without touching derived data. Confirmed
  rebuild/search consume an explicitly injected active authoritative service
  bundle and the composition-owned local service/store/state bundle; direct
  search uses the runtime's fixed budgets and returns only re-authorized
  excerpts. Confirmed purge delegates exact session containment and deletion
  to `conversation_rag_store`; disable never purges. Ordinary failures map to
  fixed content-free report categories with nonzero exit, while
  `KeyboardInterrupt` and `SystemExit` propagate. The module owns no argparse,
  controller, interactive, provider/network, SQLite implementation,
  authoritative storage, config serialization, or derived-store resource.
- **Primary verification/documentation:** Primary command-domain coverage is in
  `tests/test_conversation_rag_cli.py`; related boundary and owner coverage is
  in `tests/test_public_api_and_cli.py`,
  `tests/test_conversation_rag_runtime.py`,
  `tests/test_conversation_rag_composition.py`,
  `tests/test_conversation_rag_store.py`, and
  `tests/test_workspace_and_config.py`; current design references are
  `docs/guides/conversation-rag.md`, `docs/reference/cli.md`, and
  `docs/reference/configuration/agent-configuration.md`.

### `story_writing_agents.conversation_rag`

- **Source:** `src/story_writing_agents/conversation_rag.py`
- **Responsibility:** Define provider-neutral immutable conversation-RAG
  contracts and fixed work policy.
- **Supported surface:** Exactly 35 names in the direct-module `__all__`:
  `CONVERSATION_RAG_POLICY`, `ConversationRagPolicy`,
  `ConversationRagOutcome`, `ConversationRagReason`,
  `ConversationRagProjectionStateAvailability`,
  `ConversationRagProjectionStatus`, `ConversationRagProjectionOutcome`,
  `ConversationRagProjectionFailureStage`, `ConversationRagDocumentKey`,
  `ConversationRagDocumentIdentity`, `ConversationRagSourceDocument`,
  `ConversationRagSourceReference`, `ConversationRagEmbeddingIdentity`,
  `ConversationRagEmbeddingVector`, `ConversationRagProviderCandidate`,
  `ConversationRagVectorRecord`,
  `ConversationRagProjectionCheckpointEntry`,
  `ConversationRagProjectionCheckpoint`, `ConversationRagProjectionState`,
  `ConversationRagProjectionRequest`, `ConversationRagProjectionResult`,
  `ConversationRagRetrievalRequest`, `ConversationRagCandidate`,
  `ConversationRagRetrievalResult`, `ConversationRagEmbedder`,
  `ConversationRagVectorIndexWriter`,
  `ConversationRagVectorIndexResetter`,
  `ConversationRagVectorIndexReader`, `ConversationRagLexicalRetriever`,
  `ConversationRagCollaborators`, `ConversationRagService`,
  `ConversationRagError`, `ConversationRagContractError`,
  `ConversationRagStateError`, and `ConversationRagOperationError`.
  These names are supported only from
  `story_writing_agents.conversation_rag`; none is re-exported by the
  118-name package-root facade.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Standard-library-only frozen,
  slotted values, exact enums, runtime-checkable caller-owned collaborator
  protocols, fixed content-free failures, and one immutable policy constant.
  The policy fixes candidate, conversation, event, fragment, overlap, and
  embedding-batch limits at 20, 256, 10,000, 800, 80, and 32 respectively.
  Source and provider-candidate values may carry caller-authorized or
  non-authoritative text, but public retrieval candidates carry only stable
  source references and contiguous ranks. The module acquires no collaborator,
  owns no mutable state or external resource, performs no I/O, and has no
  operational import side effect.
- **Primary verification/documentation:** `tests/test_conversation_rag.py`,
  `tests/test_rag_module_and_packaging_boundary.py`,
  `docs/reference/package-api.md`, `docs/guides/conversation-rag.md`, and
  `docs/design/overview.md`.

### `story_writing_agents.conversation_rag_local`

- **Source:** `src/story_writing_agents/conversation_rag_local.py`
- **Responsibility:** Provide the fixed, deterministic, network-free local
  conversation-RAG embedding and vector-ranking baseline.
- **Supported surface:** Exactly five direct-module names in `__all__`:
  `LOCAL_CONVERSATION_RAG_DIMENSIONS`, `LOCAL_CONVERSATION_RAG_MODEL_ID`,
  `LOCAL_CONVERSATION_RAG_PROVIDER_ID`, `LocalConversationRagEmbedder`, and
  `LocalConversationRagVectorRanker`. The constants bind provider `local`,
  model `story-conversation-rag-local-hash-v1`, and 256 dimensions. These names
  are not re-exported by the package-root facade.
- **Direct internal dependencies:** `conversation_literal_search` and
  `conversation_rag`.
- **State, resources, and side effects:** Owns only immutable identity metadata
  and pure operations. Embedding first maps code points unassigned in the
  standard library's frozen Unicode 3.2 database to U+FFFD, then delegates the
  shared version-1 NFKC/casefold/category-C/whitespace normalization and hashes
  whole-token plus boundary-padded code-point 1/2/3-gram features through the
  fixed length-framed SHA-256 domain. Integer accumulation and `fsum`-based L2
  normalization produce deterministic finite positive-zero vectors. Ranking
  fully scans at most 65,536 exact 256-coordinate records, removes exact
  duplicates, rejects conflicting source identities, retains only positive
  scores, and sorts by descending score plus the complete stable source key
  before the request's at-most-20 limit. The intentionally weak baseline makes
  no semantic-quality guarantee. It owns no filesystem, workspace, provider,
  network, subprocess, environment, credential, clock, random, or lifecycle
  resource and performs no import-time I/O.
- **Primary verification/documentation:** `tests/test_conversation_rag_local.py`,
  `tests/test_rag_module_and_packaging_boundary.py`,
  and `docs/design/overview.md`.

### `story_writing_agents.conversation_rag_store`

- **Source:** `src/story_writing_agents/conversation_rag_store.py`
- **Responsibility:** Own identity-bound disposable conversation-RAG generation
  persistence, verification, publication, inspection, and purge lifecycles.
- **Supported surface:** Exactly eight direct-module names in `__all__`:
  `CONVERSATION_RAG_STORE_SCHEMA_ID`, `ConversationRagStoreStatus`,
  `ConversationRagStoreFailure`, `ConversationRagStoreCounts`,
  `ConversationRagStoreSnapshot`, `ConversationRagGenerationBuilder`,
  `ConversationRagGenerationStore`, and `ConversationRagStoreError`. The fixed
  store-envelope schema is `story-conversation-rag-store-v1`; caller-owned
  projection schemas remain separate and are bound by the persisted checkpoint.
  Construction retains an exact identity-bound `Workspace` without I/O. The
  generation store exposes inspection, operation-scoped generation creation,
  verified active-reader creation with an injected ranker, advisory status, and
  exact-confirmed session purge. Inspection accepts the additive keyword-only
  `expected_projection_schema_id`, validates it before I/O, and defaults it to
  the store schema for legacy callers. The builder implements the existing
  story writer/resetter methods plus successful-result publication. None is
  re-exported by the package-root facade.
- **Direct internal dependencies:** `conversation_rag` and `workspace`.
- **State, resources, and side effects:** The store is the sole owner of the
  derived `agent_states/orchestrator_state/conversation_rag` subtree. Each
  operation acquires a process-local lock and a mandatory bounded POSIX
  advisory lock, retains verified no-follow directory and regular-file
  descriptors only for that call or builder/reader context, and closes them
  exactly once. Derived directories and regular files require modes 0700 and
  0600, meaningful device/inode identity, exact containment, and supported
  descriptor-relative primitives; there is no pathname fallback. Canonical
  strict-UTF-8 sorted compact JSON/JSONL, exact schemas, duplicate-field and
  non-finite-number rejection, finite canonical `float.hex` coordinates, and
  fixed corruption mapping for bounded decoder, recursion, and numeric
  failures are enforced. Streaming bounded record reads/digests,
  manifest/source/vector digests, source reconstruction, identities, counts,
  and fixed 800/80 chunking are completely reverified.
  Inspection first verifies the envelope and complete active generation, then
  compares the expected provider, embedding, and caller projection schema. A
  verified mismatch is `INCOMPATIBLE` while retaining PRESENT checkpoint,
  provider, generation, and safe-count metadata; an incompatible envelope or
  unverifiable generation exposes no checkpoint. Advisory status is applied
  only after compatibility is established, so stale or provider-failed state
  cannot mask provider, model, or projection-schema incompatibility.
  Projection writes remain in restartable, nonresumable inactive generations.
  Generation creation inspects against the request's projection schema, and
  active-reader reopening inspects against the verified snapshot checkpoint's
  schema. Legacy generations remain structurally verifiable, but a caller with
  a different schema must rebuild rather than treating them as current; no
  on-disk migration occurs. After preverification, inactive generations are
  renamed, reopened, and reverified. Advisory
  status is persisted before `active.json`, while that four-field pointer is
  the sole commit record. Its complete temporary file and session-directory
  entry are fsynced before the atomic rename, which is the commit point and
  final callable publication operation. Precommit failures preserve the old
  pointer and expose stale or provider-failed advisory state with any old
  checkpoint retained. Inspection never cleans state; later mutations safely
  remove validated build and inactive-generation orphans. Purge requires exact
  `confirmed=True`, prevalidates the entire one-session derived namespace, and
  removes the active pointer first. Ordinary failures are fixed and
  content-free; control exceptions propagate. The module imports no local or
  generic provider, configuration/settings, SQLite, authoritative conversation
  store, network, credential, or external package and performs no import-time
  I/O.
- **Primary verification/documentation:** `tests/test_conversation_rag_store.py`,
  `tests/test_rag_module_and_packaging_boundary.py`,
  `docs/design/persistence.md`, and `docs/operations/filesystem-safety.md`.

### `story_writing_agents.generic_rag_adapter`

- **Source:** `src/story_writing_agents/generic_rag_adapter.py`
- **Responsibility:** Translate exact story conversation-RAG contracts to and
  from the pinned generic-RAG API.
- **Supported surface:** Exactly three direct-module names in `__all__`:
  `GenericRagCapabilitySnapshot`, `GenericRagConversationService`, and
  `inspect_generic_rag_capabilities`. The supported import path is
  `story_writing_agents.generic_rag_adapter`; it requires the optional
  `generic-rag` package and is not re-exported by the package-root facade.
- **Direct internal dependencies:** `conversation_rag`.
- **State, resources, and side effects:** This is the sole production module
  that imports both story contracts and the exact owning modules of
  `generic-rag` 0.1.0. Capability inspection returns only an immutable
  sanitized module/symbol inventory. One service retains the exact
  caller-supplied collaborator bundle but acquires and closes none of its
  embedder, vector writer/resetter/reader, or lexical-retriever resources;
  call-local generic `Borrowed` wrappers preserve that caller-owned lifecycle.
  Projection translation uses empty generic attributes and reconstructs story
  checkpoint identities from the authorized request. Retrieval translation
  reconstructs stable source references from the caller-owned checkpoint,
  validates exact result classes and identities, and discards generic fragment
  text and scores. Ordinary generic or collaborator failures become fixed
  content-free story failures without provider exception text or chaining;
  `KeyboardInterrupt` and `SystemExit` propagate. Import requires the declared
  optional package but performs no filesystem, network, provider, settings,
  storage, credential, or application-startup operation.
- **Primary verification/documentation:**
  `tests/test_generic_rag_adapter.py`,
  `tests/test_rag_module_and_packaging_boundary.py`,
  `docs/reference/package-api.md`, `docs/guides/conversation-rag.md`, and
  `docs/design/overview.md`.

### `story_writing_agents.conversation_rag_composition`

- **Source:**
  `src/story_writing_agents/conversation_rag_composition.py`
- **Responsibility:** Compose allowlisted generic and fixed local
  conversation-RAG services after exact compatibility validation.
- **Supported surface:** Exactly ten direct-module names in `__all__`:
  `CONVERSATION_RAG_PROVIDER_ALLOWLIST`,
  `ConversationRagCompositionFailure`, `ConversationRagCompositionError`,
  `LocalConversationRagComposition`, `LocalConversationRagRuntimeComposition`,
  `LocalConversationRagRuntimeState`, `compose_conversation_rag`,
  `compose_local_conversation_rag`,
  `compose_local_conversation_rag_runtime`, and
  `inspect_conversation_rag_compatibility`. A local composition exposes the provider-
  neutral service as itself through read-only `service` and its retained,
  caller-owned generation store through read-only `store`. The supported import
  path is `story_writing_agents.conversation_rag_composition`; none is
  re-exported by the package-root facade. The allowlist is exactly `("local",)`
  and the fixed failure vocabulary is `provider_unsupported`,
  `dependency_missing`, `version_incompatible`, and
  `capability_incompatible`.
- **Direct internal dependencies:** `conversation_rag`,
  `conversation_rag_local`, `conversation_rag_runtime`,
  `conversation_rag_store`, `workspace`, and the deferred
  `generic_rag_adapter` import.
- **State, resources, and side effects:** Import defines immutable policy and
  error metadata and loads only standard-library support plus the provider-
  neutral, pure-local, and no-I/O store modules; it does not read distribution
  metadata or import `generic-rag`. An explicit composition call validates the
  exact provider and collaborator bundle, requires installed distribution
  version 0.1.0, then performs one statically named deferred local adapter
  import, verifies the fixed host-required capability manifest, and constructs
  the adapter service. Explicit local composition fixes the local embedder and
  ranker, retains only the supplied store and lexical retriever, and constructs
  a call-local generic service for every projection or retrieval. Projection
  publishes through an inactive builder only after generic success; retrieval
  forwards a present caller checkpoint's schema to store inspection, borrows
  only a completely verified compatible active generation, maps a projection-
  schema mismatch to incompatible, and treats same-schema checkpoint drift as
  stale. Missing-state inspection keeps the store's legacy default; every
  unavailable path returns a fixed literal-fallback reason. No descriptor or
  provider lifecycle is retained by the composition.
  It performs no provider discovery, dynamic module-name loading, environment
  or `sys.path` inspection, settings/storage access, credential work, or
  resource acquisition. Ordinary metadata, import, capability, and
  construction failures become fixed sanitized composition errors without
  dependency details or chaining; `KeyboardInterrupt` and `SystemExit`
  propagate. The compatibility probe performs the same deferred exact
  distribution-version/import/capability checks without constructing a
  provider service or writing. The concrete runtime state maps verified local
  store status/checkpoints to the provider-neutral port and owns stale marking;
  the frozen runtime composition explicitly exposes its correlated service,
  store, and state for command and later orchestration reuse. Its factory
  constructs only that fixed network-free local bundle with an explicit
  automatic policy.
- **Primary verification/documentation:**
  `tests/test_conversation_rag_composition.py`,
  `tests/test_rag_module_and_packaging_boundary.py`,
  `docs/reference/package-api.md`, `docs/guides/conversation-rag.md`, and
  `docs/design/overview.md`.

### `story_writing_agents.conversation_rag_runtime`

- **Source:** `src/story_writing_agents/conversation_rag_runtime.py`
- **Responsibility:** Authorize provider-neutral conversation-RAG projection
  and retrieval against current authoritative conversation state.
- **Supported surface:** Exactly ten direct-module names in `__all__`:
  `CONVERSATION_RAG_RUNTIME_POLICY`,
  `CONVERSATION_RAG_RUNTIME_SCHEMA_ID`,
  `ConversationRagRuntimeInspection`,
  `ConversationRagRuntimeInspectionStatus`, `ConversationRagRuntimeError`,
  `ConversationRagRuntimePolicy`, `ConversationRagRuntimeService`,
  `ConversationRagRuntimeState`, `ConversationRagRuntimeStateSnapshot`, and
  `inspect_authorized_conversation_rag`. The fixed schema is
  `story-conversation-rag-visible-turn-v1`. The stateful service accepts one
  provider-neutral low-level `ConversationRagService`, one persistent
  runtime-state port, and optional `automatic_enabled=False`; it exposes
  bounded `preflight(..., forced_query=None)`, explicit
  `search_authorized(...)`, and
  `project_authorized(..., rebuild=False)` operations. None is re-exported by
  the 118-name package-root facade.
- **Direct internal dependencies:** `conversation_literal_search`,
  `conversation_rag`, `conversation_retrieval`, `conversation_search`,
  `conversation_storage.contracts`, and `conversations`.
- **State, resources, and side effects:** Construction retains the injected
  service and restart-safe state/status capability without I/O or resource
  acquisition and rejects declared-async collaborators. Every attempted
  retrieval first proves exact workspace provenance, runs at most one
  active-only literal search and one eligible semantic call, then freshly
  enumerates ACTIVE metadata and authoritative transcripts under one bounded
  mutation-coordinator borrow. Every lease-backed target snapshot validates the exact
  workspace-bound lease, permits at most 256 conversations, 10,000 events, and
  32 MiB of aggregate role-framed source UTF-8. Retrieval releases its target
  lease before excerpt construction; projection retains its outer lease as
  described below. Dynamic awaitable and async-
  generator collaborator results are rejected synchronously; returned
  coroutines are closed. Only committed complete visible user/reply pairs
  become role-framed documents. Fixed length-framed SHA-256 identities bind
  schema, session, conversation, turn, metadata revision, exact event fields,
  and authoritative source text. Provider candidate text is ignored; stable
  references are checked against current document identity and range, merged
  with literal precedence, and deduplicated to the first stable excerpt per
  complete-turn document. Deduplication alone is not truncation. The resulting
  context contains at most 20 cited excerpts of at most 800 code points and
  32,768 aggregate UTF-8 bytes. Literal transcript windows use the deterministic
  prefix of the exact cited event-content interval; subject hits use the prefix
  of the newest complete turn's exact subject-content interval. Literal
  fragment identities use the fixed
  `story-conversation-rag-visible-turn-literal-v1` hash domain. Fragment ranges
  map to the earliest intersected authoritative subject, user, or reply
  citation, with framing-only ranges using a conversation citation. Rejected
  semantic identities/ranges and failed target reconstruction after semantic
  state use best-effort mark the derived projection stale without replacing
  the content-free attempted outcome. Explicit search bypasses automatic
  policy while retaining the same fixed query, literal, semantic, excerpt,
  reread, and citation authorization budgets. Read-only inspection constructs
  the same complete target through two full optimistic reads bracketed by two
  persistent-state reads, uses no mutation-coordinator borrow or provider
  service, creates no lock/RAG namespace, marks nothing stale, and writes
  nothing. Any drift fails content-free rather than reporting a false ready
  state. A stable observation reports only safe counts, target/checkpoint
  tokens, rebuild need, and missing/ready/stale/failed/corrupt/incompatible.
  Projection enumerates the complete
  current ACTIVE target, selects incremental or full rebuild from persistent
  state, requires the published checkpoint to match the schema/embedding/
  document target, re-inspects exact persisted state, and then repeats the
  authoritative enumeration. One authoritative mutation lease now spans
  target capture, provider/generation work, publication, persisted validation,
  and final target validation, so supported authoritative writers cannot race
  the publication. Ordinary projection failure best-effort marks persistent state
  stale; injected control exceptions propagate. The module owns no
  authoritative storage, generation store, provider, network, credential,
  controller/settings, SQLite, MySQL, or import-time resource.
- **Primary verification/documentation:** `tests/test_conversation_rag_runtime.py`,
  `tests/test_conversation_rag.py`, `tests/test_conversation_retrieval.py`,
  `tests/test_conversation_runtime.py`,
  `docs/guides/conversation-rag.md`,
  `docs/reference/conversation-retrieval.md`,
  `docs/reference/package-api.md`, and
  `docs/reference/runtime-api.md`.

### `story_writing_agents.conversation_retrieval`

- **Source:** `src/story_writing_agents/conversation_retrieval.py`
- **Responsibility:** Define immutable provider-neutral bounded conversation-
  retrieval actions, observations, results, progress, fixed policy, canonical
  untrusted-data framing, and the synchronous runtime/backend handler port.
- **Supported surface:** Exactly 24 direct-module names:
  `CONVERSATION_RETRIEVAL_POLICY`, `ConversationRagAttemptOutcome`,
  `ConversationRagContext`, `ConversationRagExcerpt`,
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
  `serialize_conversation_retrieval_observation`. The original retrieval
  contract names remain package-facade exports; the three RAG values and the
  serializer are supported only from this direct module, preserving the exact
  120-name root facade. `ConversationRetrievalRequest` carries additive
  optional `rag_context`; `ConversationRetrievalResult` carries additive
  optional `rag_outcome` plus trailing `rag_sources`, an exact unique result-
  ordered subset of sources proven to originate in the preflight-authorized
  RAG context. The outcome defaults to `None` and provenance to an empty tuple,
  so no-attempt construction and provider payloads omit RAG context exactly;
  nonempty provenance requires an attempted outcome. Attempted
  context always includes outcome/reason, canonical `g1` through `g20`
  authoritative excerpts, exact retained UTF-8 bytes, and truncation.
  `ConversationRetrievalResult`
  carries an additive optional, content-free `reference_operation_id`; its
  default remains `None`, and construction validates any supplied safe
  identity without importing artifact-runtime ownership.
  `ConversationRetrievalRequest` also carries an additive optional validated
  `story_session_context`; omission preserves legacy request construction and
  projection, and runtime-built requests may carry both context forms.
- **Direct internal dependencies:** `agents`, `conversation_rag`,
  `conversation_search`, `conversations`, `errors`, and `models`.
- **State, resources, and side effects:** Frozen, slotted contracts, immutable
  policy metadata, exact validators, and pure canonical serialization only.
  Authoritative excerpt content is excluded from dataclass representations but
  remains present in the explicitly framed provider dictionary. The module
  performs no I/O and owns no storage, provider, process, thread, filesystem,
  or mutable runtime state and has no operational import side effect.
- **Primary verification/documentation:**
  `tests/test_conversation_retrieval.py`, `tests/test_public_api_and_cli.py`,
  and `docs/reference/conversation-retrieval.md`.

### `story_writing_agents.conversation_literal_search`

- **Source:** `src/story_writing_agents/conversation_literal_search.py`
- **Responsibility:** Own the pure deterministic literal-search mechanics
  shared by provider-neutral and storage-specific conversation-search adapters.
- **Supported surface:** Exactly six names in `__all__`:
  `CONVERSATION_LITERAL_NORMALIZATION_VERSION`,
  `build_conversation_literal_snippet`,
  `classify_conversation_literal_subject`,
  `conversation_literal_sort_key`,
  `normalize_conversation_literal_query`, and
  `normalize_conversation_literal_text`. This direct-module surface is absent
  from both the package-root and conversation-storage facades.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Owns immutable version and match-rank
  metadata plus pure strict-UTF-8 validation, version-1 NFKC/casefold/category-C
  replacement/whitespace-collapse normalization, bounded query validation,
  subject-tier classification, bounded normalized snippet construction, and
  stable rank/subject/identity sorting. It owns no mutable state, filesystem,
  database, provider, process, or network resource; performs no I/O; and has no
  operational import side effect.
- **Primary verification/documentation:**
  `tests/test_conversation_search.py`, supported by
  `tests/conversation_search_contract.py`,
  `docs/reference/package-api.md`, and
  `docs/reference/conversation-search.md`.

### `story_writing_agents.conversation_search`

- **Source:** `src/story_writing_agents/conversation_search.py`
- **Responsibility:** Define provider-neutral deterministic conversation-history
  discovery contracts.
- **Supported surface:** Exactly 25 names in `__all__`. The established
  package-supported surface remains `CONVERSATION_MEMORY_POLICY`,
  `ConversationCitation`, `ConversationMemoryPolicy`,
  `ConversationMemoryService`, `ConversationSearch`, `ConversationSearchHit`,
  `ConversationSearchMatchKind`, `ConversationSearchQuery`,
  `ConversationSearchResult`, `ConversationSearchScope`,
  `ConversationTranscriptCursor`, `ConversationTranscriptPage`, and
  `ReferenceConversationSearch`. The additive direct-module-only schema-v2
  surface is `COLLABORATION_READABLE_SOURCE_SCHEMA_ID`,
  `CollaborationHistoryCitation`, `CollaborationHistoryPage`,
  `CollaborationHistoryPageCursor`, `CollaborationHistorySearchHit`,
  `CollaborationHistorySearchQuery`, `CollaborationHistorySearchResult`,
  `collaboration_readable_document_id`, `continue_collaboration_history`,
  `page_collaboration_history`, `resolve_collaboration_citation`, and
  `search_collaboration_history`. None is
  re-exported by the project root. Schema-v2 citations bind one event and
  stable readable field plus optional zero-based assignment/input ordinal and
  exact original-field code-point range. Page cursors bind both the observed
  tail sequence and tail batch digest. The derived readable-field identity
  length-frames the complete canonical stored event plus fixed schema,
  workspace/conversation, metadata-revision, field-coordinate, and exact
  readable-byte facts; it is never append, confirmation, or recovery
  authority. `ConversationMemoryService.scope` is an
  additive read-only projection of its immutable authorized session scope. Its
  established positional catalog/event-store construction remains valid, and
  an additive keyword-only structural `conversation_storage_services` input
  accepts one complete bundle only when raw catalog, event-store, and search
  collaborators are all absent.
- **Direct internal dependencies:** `conversation_literal_search`,
  `conversations`, and `errors`.
- **State, resources, and side effects:** Immutable value contracts and fixed
  policy/schema metadata own no mutable state or external resource. Pure v2
  search first validates and projects the complete authoritative history,
  applies the established NFKC/casefold/control/whitespace literal behavior,
  maps normalization and case-fold expansion back to exact original-field
  code-point ranges, and returns deterministic sequence/field-ordered matches.
  Pure paging retains whole readable records under fixed count/UTF-8 caps and
  rejects either sequence or tail-digest drift. The reference
  adapter delegates normalization, subject classification, snippet
  construction, and result sorting to the supported pure literal module while
  retaining its established bounded catalog/event-store scan semantics.
  Reference-search instances retain explicitly supplied `ConversationCatalog`
  and `ConversationEventStore` collaborators. A memory service retains either
  the established raw collaborators or the supplied immutable bundle plus its
  validated catalog, event-store, and search ports; private structural
  validation avoids a dependency from this provider-neutral module back into
  conversation storage. Conflicting or incomplete injection fails at
  construction. The scope property performs no catalog, store, search, or
  coordinator access. Instances perform reads only and have no import-time
  operational side effects.
- **Primary verification/documentation:**
  `tests/conversation_search_contract.py`,
  `tests/test_conversation_search.py`, and
  `tests/test_collaboration_context.py`,
  `docs/reference/collaboration-history.md`,
  `docs/reference/conversation-search.md`.

### `story_writing_agents.conversation_storage`

- **Source:** `src/story_writing_agents/conversation_storage/__init__.py`
- **Responsibility:** Provide the intentional public boundary for conversation-
  storage selection, composition, coordination, activation, and exact archive
  transfer.
- **Supported surface:** Exactly 63 names in `__all__`: the retained fixed
  `CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION`,
  `CONVERSATION_STORAGE_ACTIVATION_STATE_PATH`,
  `CONVERSATION_STORAGE_MUTATION_GUARD_PATH`, and
  `CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS` constants; generic
  `ConversationArchiveSource` and `ConversationArchiveSink`, plus
  `ConversationArchiveImportStatus` and `ConversationArchiveImportReceipt`;
  `ConversationStorageBackend`, `ConversationStorageCapability`,
  `ConversationStorageParity`, `ConversationStorageActivationOutcomeKind`,
  `ConversationStorageFailure`, `ConversationStoragePreflight`,
  `ConversationStorageTransferReadiness`,
  `ConversationStorageActivationState`,
  `ConversationStorageActivationOutcome`,
  `ConversationStorageProvenance`, `ConversationStorageComponentBinding`,
  `ConversationStorageServices`, `ConversationStorageMutationLease`, and
  `ConversationStorageMutationCoordinator`; fixed
  `ConversationStorageError`, `ConversationStorageConfigurationError`,
  `ConversationStorageServicesError`,
  `ConversationStorageUnavailableError`,
  `ConversationStorageProvisioningNeededError`,
  `ConversationStorageTransferRequiredError`,
  `ConversationStorageParityError`,
  `ConversationStorageActivationStateError`,
  `ConversationStorageActivationVersionError`,
  `ConversationStorageActivationConflictError`,
  `ConversationStorageActivationError`, and
  `ConversationStorageCoordinationError`; concrete
  `WorkspaceConversationStorageMutationCoordinator`; and
  `parse_conversation_storage_backend`,
  `validate_conversation_storage_mutation_timeout`,
  `compose_filesystem_conversation_storage`,
  `load_conversation_storage_activation_state`, and
  `compare_and_set_conversation_storage_activation`; plus canonical
  `CONVERSATION_ARCHIVE_SCHEMA_VERSION`,
  `CONVERSATION_ARCHIVE_MAX_RAW_BYTES`,
  `CONVERSATION_ARCHIVE_MAX_SESSIONS`,
  `CONVERSATION_ARCHIVE_MAX_CONVERSATIONS`,
  `CONVERSATION_ARCHIVE_MAX_EVENTS`,
  `CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES`, `ConversationArchive`,
  `ConversationArchiveSession`,
  `ConversationArchiveConversation`, whose events use the existing canonical
  `conversations.CollaborationEvent` value, the ten-name
  canonical archive exception hierarchy rooted at `ConversationArchiveError`,
  `serialize_conversation_archive`, `deserialize_conversation_archive`,
  `FilesystemConversationArchiveAdapter`, and
  `compose_filesystem_conversation_archive`. None are re-exported by the
  project-root `story_writing_agents` facade. The separately imported optional
  `story_writing_agents.conversation_storage.sqlite` and
  `story_writing_agents.conversation_storage.sqlite_database` modules, plus the
  direct integration surface in
  `story_writing_agents.conversation_storage.sqlite_search_index`, have their
  own supported surfaces. The seven-name direct transfer surface in the
  retained internal-named
  `story_writing_agents.conversation_storage.migration`, four-name direct
  integration surface in `story_writing_agents.conversation_storage.provisioning`,
  and eight-name direct integration surface in
  `story_writing_agents.conversation_storage.selection` are likewise excluded;
  none of these optional or workflow modules is re-exported by this package or
  the root facade.
- **Direct internal dependencies:** `conversation_storage.archive`,
  `conversation_storage.composition`, `conversation_storage.contracts`, and
  `conversation_storage.filesystem_archive`.
- **State, resources, and side effects:** Owns no state or resource; importing
  the package loads exactly its archive, composition, contracts, and filesystem
  archive modules, but does not import selection, provisioning, the retained
  internal-named transfer module, any
  SQLite adapter, database-owner, or search-index module or load the standard-
  library `sqlite3` module. Import
  performs no filesystem access, lock-file open, thread creation, archive scan,
  database access, or adapter construction.
- **Primary verification/documentation:**
  `tests/test_conversation_archive.py`,
  `tests/test_filesystem_conversation_archive.py`,
  `tests/test_conversation_storage.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_conversation_event_store.py`,
  `tests/test_conversation_search.py`,
  `tests/test_public_api_and_cli.py`,
  `docs/reference/conversation-archives.md`,
  `docs/reference/package-api.md`,
  `docs/operations/filesystem-safety.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`, and
  `docs/reference/conversation-search.md`.

### `story_writing_agents.conversation_storage.archive`

- **Source:** `src/story_writing_agents/conversation_storage/archive.py`
- **Responsibility:** Own the immutable canonical conversation archive domain
  and its deterministic version-2 wire representation.
- **Supported surface:** Exactly 21 names in `__all__`: fixed
  `CONVERSATION_ARCHIVE_SCHEMA_VERSION`,
  `CONVERSATION_ARCHIVE_MAX_RAW_BYTES`,
  `CONVERSATION_ARCHIVE_MAX_SESSIONS`,
  `CONVERSATION_ARCHIVE_MAX_CONVERSATIONS`,
  `CONVERSATION_ARCHIVE_MAX_EVENTS`, and
  `CONVERSATION_ARCHIVE_MAX_AGGREGATE_UTF8_BYTES` policy constants; frozen
  `ConversationArchive`, `ConversationArchiveSession`, and
  `ConversationArchiveConversation` values, with the latter containing only
  existing canonical `conversations.CollaborationEvent` values;
  content-free `ConversationArchiveError`, `ConversationArchiveFormatError`,
  `ConversationArchiveVersionError`, `ConversationArchiveBoundsError`,
  `ConversationArchiveDigestError`, `ConversationArchiveSourceError`,
  `ConversationArchiveSourceChangedError`,
  `ConversationArchiveDestinationError`, `ConversationArchiveConflictError`,
  and `ConversationArchiveDestinationChangedError`; and
  `serialize_conversation_archive` plus `deserialize_conversation_archive`.
- **Direct internal dependencies:** `conversations` and `errors`.
- **State, resources, and side effects:** Owns no mutable state or external
  resource. Construction, serialization, and parsing enforce exact container
  types and local counts, then shallow child types and complete nested global
  counts, before inspecting or copying descendant fields. They consume the
  fixed 32-MiB aggregate subject/canonical-event UTF-8 budget incrementally.
  Values are immutable and canonicalize session, conversation, and event
  order. Catalog metadata retains its owning schema version one while the
  archive and every collaboration event require schema version two. Complete
  history validation delegates to the existing collaboration projection, and
  event copying and wire conversion delegate to the existing collaboration
  event codec rather than duplicating its payload model.
  Serialization emits strict version-2 UTF-8 JSON with exact field order and
  one final LF; its lower-case SHA-256 covers the same canonical top-level
  schema/version and sessions image without the digest field. Deserialization
  caps raw bytes before decoding, rejects schema-version-one, duplicate, or
  noncanonical JSON, verifies the digest, and maps recursion anywhere in the
  complete parse and canonical-revalidation path to a content-free format error
  without retained cause. All archive errors are content-free, and the module
  performs no I/O.
  Its canonical bytes are the backend-neutral equality, source-fingerprint,
  and final-parity boundary consumed by explicit filesystem/SQLite transfer;
  the archive owner itself knows no backend, checkpoint, CLI, or activation
  lifecycle.
- **Primary verification/documentation:**
  `tests/test_conversation_archive.py`, supported by
  `tests/conversation_archive_fixture.py`, and
  `tests/test_conversation_storage_migration.py`;
  `docs/reference/conversation-archives.md` and
  `docs/reference/package-api.md`.

### `story_writing_agents.conversation_storage.composition`

- **Source:**
  `src/story_writing_agents/conversation_storage/composition.py`
- **Responsibility:** Own workspace-pinned conversation-storage composition.
- **Supported surface:** Package-supported
  `CONVERSATION_STORAGE_ACTIVATION_STATE_PATH`,
  `CONVERSATION_STORAGE_MUTATION_GUARD_PATH`,
  `WorkspaceConversationStorageMutationCoordinator`,
  `compose_filesystem_conversation_storage`,
  `load_conversation_storage_activation_state`, and
  `compare_and_set_conversation_storage_activation`.
  `compose_filesystem_conversation_storage` captures one private frozen
  canonical-root topology and returns concrete-`isinstance`-compatible pinned
  subclasses of `FilesystemConversationCatalog`,
  `FilesystemConversationEventStore`, and `ReferenceConversationSearch` under
  three explicit same-provenance `ConversationStorageComponentBinding` values.
  The subclasses materialize operation-local `Workspace` instances from that
  topology and retain their construction-pinned collaborators, so later caller
  root mutation or inherited collaborator rebinding cannot redirect composed
  I/O. The composed catalog's create/rename/archive/unarchive operations and
  event store's append operation borrow the exact coordinator returned in the
  same service bundle. Direct standalone filesystem adapters retain their
  established borrowed-`Workspace` and uncoordinated mutation behavior. The
  coordinator retains no caller `Workspace`; its
  canonical key, fixed guard, and every issued lease derive from its
  construction-pinned topology. Activation load and compare-and-set capture one
  canonical root per whole call. Compare-and-set rejects a wrong-root lease
  before operation-local workspace materialization or filesystem I/O, then
  requires an active owner-thread lease, an exact
  `ConversationStorageTransferReadiness`, and exact expected generation and
  revalidates that lease before publication; it never acquires coordination
  internally. These guarantees do not change established paths, root exports,
  or the default backend.
- **Direct internal dependencies:** `conversation_search`,
  `conversation_storage.contracts`, `conversation_storage.filesystem`, and
  `workspace.core`.
- **State, resources, and side effects:** Import initializes only a synchronized
  weak in-process registry and immutable constants; it performs no filesystem
  access and opens no resource. The frozen topology owns only one canonical
  absolute `Path`; composed pinned subclasses materialize fresh workspaces from
  it for operations and retain no caller-mutable workspace. Coordinator
  construction is I/O-free. Its one exclusive borrow validates a finite
  caller-supplied timeout, shares an in-process reentrant lock per canonical
  workspace, and on POSIX opens, validates, advisory-locks, unlocks, and closes
  the contained `config/.conversation_storage.lock` guard at the pinned root.
  Same-thread reentry returns the same owner-thread/workspace-bound lease
  without extending its monotonic expiry; exit deactivates it, and unsupported
  or unsafe coordination fails closed. Callers acquire this lease before all
  backend-specific mutation locks and acquire multiple workspace leases in
  canonical-key order. The weak registry has an inactive-state reset seam and
  retains no external resource. Each activation call owns one temporary frozen
  topology. Activation reads exactly the version-1 `schema_version`, `backend`,
  and nonnegative `generation` fields at
  `config/conversation_storage_state.json`; a missing marker returns filesystem
  generation zero without writing, malformed or unsupported state fails
  closed, same-backend CAS is unwritten, and successful backend change uses a
  pinned operation-local workspace's contained atomic JSON replacement before
  returning the next generation.
- **Primary verification/documentation:**
  `tests/test_conversation_storage.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_conversation_event_store.py`,
  `tests/test_conversation_search.py`,
  `docs/operations/filesystem-safety.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`, and
  `docs/reference/package-api.md`.

### `story_writing_agents.conversation_storage.contracts`

- **Source:** `src/story_writing_agents/conversation_storage/contracts.py`
- **Responsibility:** Define stable typed contracts for conversation-storage
  selection, composition, transfer, activation, and exact archives.
- **Supported surface:** Exactly 34 package-supported names in `__all__`:
  `CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION`,
  `CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS`,
  `ConversationStorageBackend`, `ConversationStorageCapability`,
  `ConversationStorageParity`, `ConversationStorageActivationOutcomeKind`,
  `ConversationStorageFailure`, `ConversationArchiveImportStatus`,
  `ConversationStoragePreflight`, `ConversationStorageTransferReadiness`,
  `ConversationStorageActivationState`,
  `ConversationStorageActivationOutcome`,
  `ConversationStorageProvenance`, `ConversationStorageComponentBinding`,
  `ConversationStorageServices`, `ConversationStorageMutationLease`,
  `ConversationStorageMutationCoordinator`, generic
  `ConversationArchiveSource` and `ConversationArchiveSink`,
  `ConversationArchiveImportReceipt`, the fixed storage exception hierarchy
  rooted at `ConversationStorageError`,
  `parse_conversation_storage_backend`, and
  `validate_conversation_storage_mutation_timeout`. Each component binding is
  an adapter-owned explicit assertion over one raw narrow catalog, event-store,
  or search port; bundle validation requires the same identity-bearing
  provenance token and does not inspect adapter-private state or infer
  provenance from collaborator attributes. Archive payload shape remains
  generic and adapter-owned, while the source/sink signatures require an active
  lease and the sink returns a typed imported/unchanged receipt. The transfer
  readiness value raises `ConversationStorageTransferRequiredError` with the
  fixed `ConversationStorageFailure.TRANSFER_REQUIRED` / `transfer_required`
  identity when a nonempty backend switch has no verified parity. This
  contracts module itself owns no archive record, serialization, or ordinary
  ID/timestamp/sequence allocation API.
- **Direct internal dependencies:** `conversation_search`, `conversations`,
  and `errors`.
- **State, resources, and side effects:** Frozen, slotted values, protocols,
  enums, pure validators, and fixed exceptions only; owns no mutable state,
  filesystem, database, lock, process, thread, network, or import-time
  operational side effect.
- **Primary verification/documentation:**
  `tests/test_conversation_storage.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_conversation_event_store.py`,
  `tests/test_conversation_search.py`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`, and
  `docs/reference/conversation-search.md`.

### `story_writing_agents.conversation_storage.filesystem`

- **Source:**
  `src/story_writing_agents/conversation_storage/filesystem.py`
- **Responsibility:** Own authoritative filesystem persistence for conversation
  metadata and histories.
- **Supported surface:** Exactly `FilesystemConversationCatalog`,
  `FilesystemConversationEventStore`, and
  `FilesystemCollaborationEventStore` in `__all__`, in that order. The owning
  module exposes all three class objects. The stable `workspace` facade and
  project-root facade continue to expose only the first two established v1
  classes; `FilesystemCollaborationEventStore` is a direct-module-only API and
  is not returned by active service composition. Every class's canonical
  defining module is
  `story_writing_agents.conversation_storage.filesystem`; established v1
  constructors, signatures, path-like inputs, concrete `isinstance` behavior,
  and workspace and root imports remain supported. The `conversation_storage`
  package facade deliberately does not re-export any of the three classes.
  The privileged filesystem archive adapter now composes one
  operation-local instance to transport exact schema-v2 history.
- **Direct internal dependencies:** `conversations`, `errors`, and
  `workspace.core`.
- **State, resources, and side effects:** Import creates only separate guarded
  weak process-wide v1 transcript- and v2 collaboration-history-lock registries
  plus immutable schema/allocation constants; it performs no filesystem access
  and opens no resource. Each catalog retains one supplied or constructed
  `Workspace`, owns bounded UUID
  allocation and exact metadata JSON under
  `discussion_logs/<session_id>/interactive/conversations/<conversation_id>/metadata.json`,
  and uses the workspace's secure path and atomic replacement primitives.
  Each event store retains one workspace plus its supplied or default catalog,
  selects one weakly retained process-wide lock per canonical
  workspace/session/conversation identity, strictly parses and serializes exact
  authoritative JSONL at the sibling `transcript.jsonl`, and holds that lock
  across catalog validation, transcript reads, bounded-context selection, and
  complete-file atomic append replacement. Each collaboration store likewise
  retains one workspace plus its supplied or default catalog and uses a weak
  per-history process lock. A present-history read or append additionally owns
  a blocking POSIX advisory lock through the persistent sibling
  `.collaboration-history.lock`; unsupported locking fails closed. The store
  reads only strict canonical schema-v2 JSONL from
  `collaboration-history.jsonl`, validates the complete path-bound history and
  projection, and maps stored-history corruption to the fixed read/append
  boundaries. A new atomic batch is assigned contiguous sequence and batch
  metadata plus durable timestamps, then the complete history is staged in the
  same directory, file-fsynced, replaced, and directory-fsynced before success
  is returned. Exact retries recover the original receipt after an uncertain
  post-replace failure. Failed create cleanup remains confined to a newly empty
  conversation directory; catalog, absent-transcript, and absent-collaboration-
  history reads do not create storage.
- **Primary verification/documentation:**
  `tests/test_conversation_catalog.py`,
  `tests/conversation_catalog_contract.py`,
  `tests/test_conversation_event_store.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/test_filesystem_collaboration_event_store.py`,
  `tests/test_conversation_storage.py`,
  `tests/test_filesystem_conversation_archive.py`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`,
  `docs/reference/package-api.md`,
  `docs/reference/collaboration-history.md`,
  `docs/design/persistence.md`, and
  `docs/operations/filesystem-safety.md`.

### `story_writing_agents.conversation_storage.filesystem_archive`

- **Source:**
  `src/story_writing_agents/conversation_storage/filesystem_archive.py`
- **Responsibility:** Own privileged lease-bound exact archive transfer for the
  canonical filesystem collaboration layout.
- **Supported surface:** Exactly two package-re-exported names in `__all__`:
  `FilesystemConversationArchiveAdapter` and
  `compose_filesystem_conversation_archive`. The concrete adapter's established
  two-positional-argument `import_archive` calls remain valid; an optional
  keyword-only `on_conversation_completed` callback is the supported progress
  seam for a caller that owns resumability.
- **Direct internal dependencies:** `conversation_storage.archive`,
  `conversation_storage.contracts`, `conversation_storage.filesystem`,
  `conversations`, `errors`, and `workspace.core`.
- **State, resources, and side effects:** Import performs no I/O. One adapter
  captures only one private immutable canonical root and workspace key; it
  retains no caller `Workspace` or publicly reachable catalog/event-store
  binding. Each export or import validates the caller-owned active
  correct-workspace owner-thread lease before materializing one unreachable
  operation-local public `Workspace`, `FilesystemConversationCatalog`, and
  `FilesystemCollaborationEventStore` set pinned to that root, then revalidates
  the same lease throughout scans and immediately before writes. Every
  included or excluded descendant entry is charged before retention, stat, or
  descent, with fixed limits of 1,000,000 entries, 1,024 contributing sessions,
  65,536 conversations, 1,000,000 addressed events, 64 MiB per included file,
  128 MiB cumulative included bytes, the archive's incremental 32-MiB logical
  subject/canonical-event budget, and its canonical wire bound. Included files
  are no-follow singly linked regular files; excluded bytes, the safe excluded
  `.collaboration-history.lock`, and the metadata-validated empty `.gitkeep`
  sentinel are never read. Any legacy `transcript.jsonl` makes source export or
  destination classification fail before mutation. Authoritative reads use
  addressed `catalog.get` and collaboration-store operations rather than a
  catalog-wide rescan.
  Export returns only after two complete byte-identical canonical scans; the
  caller must keep the source stable because this detects change without
  blocking ordinary writers. Before the first import write, the adapter
  validates the complete destination-plus-payload union, precomputed target
  bytes, projected paths and aggregate bounds, every target and ancestor, and
  the expected canonical wire. Import preserves unrelated excluded state,
  performs only create-only atomic metadata-first writes followed by one
  complete canonical `collaboration-history.jsonl`, accepts exact collisions,
  and verifies the complete expected bounded post-import snapshot. No partial
  collaboration JSONL or batch is exposed.
  When supplied, the completion callback runs for every payload conversation,
  including an already-identical resumable record, only after that record has
  been reread as an exact complete durable value and while the lease validates
  immediately before and after the call. Callback failure propagates without
  rollback or deletion; the adapter owns neither checkpoint state nor progress
  policy.
  Operational failure can leave only canonical addressed directory prefixes,
  a complete exact inactive conversation, or exact metadata awaiting its
  collaboration history,
  so retry converges without overwrite, deletion, or ordinary allocation.
- **Primary verification/documentation:**
  `tests/test_filesystem_conversation_archive.py`,
  `tests/test_conversation_storage_migration.py`,
  `docs/reference/conversation-archives.md`,
  `docs/operations/filesystem-safety.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.conversation_storage.migration`

- **Source:**
  `src/story_writing_agents/conversation_storage/migration.py`
- **Responsibility:** Own provider-free same-workspace canonical conversation-
  storage transfer, exact parity verification, reporting, and resumability.
- **Supported surface:** Exactly seven direct-module names in `__all__`:
  `CONVERSATION_STORAGE_TRANSFER_SCHEMA_VERSION`,
  `CONVERSATION_STORAGE_TRANSFER_CHECKPOINT_DIRECTORY`,
  `ConversationStorageTransferOperation`,
  `ConversationStorageTransferOutcome`,
  `ConversationStorageTransferReport`, `transfer_conversation_storage`, and
  `verify_conversation_storage`. The schema version is exactly one and the
  checkpoint directory is exactly
  `agent_states/orchestrator_state/conversation_storage_transfers`.
  The frozen report provides its fixed counts-only `to_dict`, compact sorted
  `to_json`, and process `exit_code` projections. These names are absent from
  both the 63-name conversation-storage facade and 118-name package-root
  facade. No former migration-named public constant, type, operation, or
  callable remains as an alias.
- **Direct internal dependencies:** `conversation_storage.archive`,
  `conversation_storage.composition`, `conversation_storage.contracts`,
  `conversation_storage.filesystem_archive`, `conversation_storage.sqlite`,
  `errors`, and `workspace.core`.
- **State, resources, and side effects:** Import defines frozen values, fixed
  policy metadata, validators, and workflow functions only; it performs no I/O
  and does not load `sqlite3`. Each call captures one canonical absolute
  workspace root, constructs operation-local public adapters, and holds one
  outer workspace mutation lease across activation/readiness validation, fresh
  canonical exports, planning, any destination mutation and checkpoint
  publication, and fresh final source/destination parity. The source must be
  the active backend, filesystem and SQLite must differ, and SQLite must
  already be provisioned and capable. Every identity is classified exactly
  once as create, identical, conflict, unsupported, or destination-only;
  SQLite-unrepresentable subject revisions or event sequences are unsupported
  before mutation. Conflict and unsupported state block both workflows, while
  destination-only state additionally blocks transfer and create or
  destination-only state is a valid verify mismatch.

  Dry-run performs no destination or checkpoint write, although it may validate
  a same-source checkpoint; verify ignores checkpoint state. Their only
  possible creation is the coordinator's empty fixed guard. A real filesystem-
  to-SQLite copy submits the complete canonical archive to one SQLite
  transaction. A SQLite-to-filesystem copy retains the filesystem adapter's
  complete global preflight and advances progress only through its post-durable-
  conversation callback, including already-identical resumable records. Both
  directions freshly re-export the unchanged source and exact destination
  before success; late source or destination drift fails without promising
  cross-backend rollback. FTS, configuration, activation, provisioning, source
  deletion, and dual-write lifecycles are excluded.

  The owner reads and performs atomic durable replacement only for the bounded
  transfer-schema-version-one direction files `filesystem-to-sqlite.json` and
  `sqlite-to-filesystem.json` below its fixed directory. A checkpoint contains
  only its direction, canonical source fingerprint and aggregate counts,
  progress counts, and sorted completed stable session/conversation identities;
  it contains no subject, event, path, archive, configuration, activation, or
  FTS bytes. No-follow single-link reads, exact schema/type/count/identity
  checks, previous-value comparison, lease validation, atomic workspace
  replacement, and exact reread make malformed, tampered, unsupported, or
  impossible progress fail closed. Transfer/checkpoint schema one is
  independent of canonical archive schema two. Matching progress is revalidated
  against current source/destination records; a changed source can replace
  prior state only after a fresh unblocked global plan. Successful transfer retains a
  terminal completed checkpoint. Domain and operational failures become fixed
  content-free reports whose complete stable schema always includes operation,
  outcome, backends, dry-run/parity/checkpoint/failure state, five plan counts,
  and source conversation/event counts; process-control exceptions and
  `dry_run` or backend argument failures rejected before workflow entry remain
  ordinary exceptions. This retained module is same-schema transport
  infrastructure only: it provisions or activates neither backend, never
  converts an older workspace/archive/database row, and never deletes the
  source, dual-writes, or copies derived FTS authority.
- **Primary verification/documentation:**
  `tests/test_conversation_storage_migration.py`, supported by
  `tests/conversation_storage_migration_fixture.py`,
  `tests/test_conversation_storage_migration_cli.py`,
  `tests/test_filesystem_conversation_archive.py`, and
  `tests/test_sqlite_conversation_storage.py`;
  `docs/guides/sqlite-conversation-storage.md`,
  `docs/reference/package-api.md`, `docs/reference/cli.md`,
  `docs/reference/conversation-archives.md`, `docs/design/persistence.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.conversation_storage.provisioning`

- **Source:**
  `src/story_writing_agents/conversation_storage/provisioning.py`
- **Responsibility:** Own explicit SQLite runtime-readiness, allowlisted host-
  installation, and storage-provisioning orchestration.
- **Supported surface:** Exactly four names in `__all__`:
  `ConversationStorageInstallationPlan`,
  `ConversationStorageProvisioningFailure`,
  `ConversationStorageProvisioningResult`, and
  `ConversationStorageProvisioner`. This direct integration surface is absent
  from both the package-root and conversation-storage facades. The frozen plan
  accepts only one of the exact commands `brew install sqlite`,
  `apt-get install sqlite3 libsqlite3-dev`, or
  `dnf install sqlite sqlite-devel`. The result exposes only the fixed
  `not_required`, `completed`, `not_run`, or `failed` installation and
  provisioning states plus a content-free typed failure and optional storage
  preflight.
- **Direct internal dependencies:** `conversation_storage.contracts`,
  `conversation_storage.sqlite`, and `workspace.core`.
- **State, resources, and side effects:** Import defines immutable policy,
  values, and the adapter class only; it performs no platform probe, environment
  read, executable lookup, subprocess execution, filesystem access, database
  operation, or `sqlite3` import. Construction retains injectable platform,
  exact `/etc/os-release`, environment, executable-lookup, and process-runner
  seams without invoking them. Planning matches only Darwin with a discoverable
  Homebrew executable or exact Linux IDs `debian`, `ubuntu`, `fedora`, and
  `rhel`; a nonempty `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `CIRCLECI`,
  `BUILDKITE`, `TF_BUILD`, `JENKINS_URL`, `TEAMCITY_VERSION`, `TRAVIS`,
  `APPVEYOR`, `BITBUCKET_BUILD_NUMBER`, `CODEBUILD_BUILD_ID`, or `DRONE`
  marker disallows installation. Explicit
  installation runs one exact argument tuple in the caller's operation-local
  workspace root with
  `shell=False`, inherited stdin, and both native output streams routed to the
  caller's diagnostic stream. It supplies no custom environment, timeout,
  retry, shell, privilege escalation, or automatic consent and propagates
  process-control exceptions. Readiness delegates the active-interpreter probe
  and lease-bound SQLite provision operation to their public owners; this
  module owns no coordinator lease, configuration, parity, activation, schema,
  CLI report, provider, FTS, or import-time resource.
- **Primary verification/documentation:**
  `tests/test_conversation_storage_provisioning.py` verifies this orchestration
  boundary; existing lower-level boundaries are verified by
  `tests/test_sqlite_database.py`,
  `tests/test_sqlite_conversation_storage.py`, and
  `tests/test_conversation_storage.py`; operational context is documented in
  `docs/guides/sqlite-conversation-storage.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/operations/filesystem-safety.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.conversation_storage.selection`

- **Source:** `src/story_writing_agents/conversation_storage/selection.py`
- **Responsibility:** Own desired conversation-storage selection and
  coordinated startup activation.
- **Supported surface:** Exactly eight names in `__all__`:
  `CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION`,
  `ConversationStorageSelectionFailure`,
  `ConversationStorageSelectionReport`, `ConversationStorageStartup`,
  `inspect_conversation_storage_parity`, `select_conversation_storage`, and
  `compose_active_conversation_storage` plus
  `activate_configured_conversation_storage`. This direct workflow surface is
  absent from both facades. The schema-v1 selection report is counts-free and
  uses only `not_required|completed|not_run|failed` installation/provisioning,
  `not_required|verified|unverified|mismatch` parity,
  `saved|unchanged|preserved` configuration, and
  `next_shell|unchanged` activation states. Its fixed sanitized failure enum is
  exactly `invalid_request`, `workspace_invalid`, `coordination_failed`,
  `installation_required`, `installation_unsupported`,
  `installation_disallowed`, `installation_failed`,
  `interpreter_unavailable`, `provisioning_failed`, `transfer_required`,
  `parity_mismatch`, `configuration_invalid`, `configuration_conflict`, or
  `configuration_write_failed`; its outcome is `completed|failed` and its exit
  code is zero only for a completed report.
- **Direct internal dependencies:** `config`, `workspace.core`,
  `conversation_storage.archive`, `conversation_storage.composition`,
  `conversation_storage.contracts`,
  `conversation_storage.filesystem_archive`,
  `conversation_storage.provisioning`, and `conversation_storage.sqlite`.
- **State, resources, and side effects:** Each call captures one immutable
  absolute workspace root and constructs operation-local public collaborators;
  it retains no caller-mutable workspace binding. Desired selection first
  proves coordinator acquisition, releases that proof before any optional host
  installation, then reacquires the same coordinator for runtime reprobe,
  explicit SQLite provisioning or filesystem composition, active-state read,
  fresh canonical source/target archive parity, and the config-owned exact-byte
  complete-document CAS. CLI selection changes only desired storage, while the
  settings caller may supply its complete validated pending configuration for
  the same final CAS and an optional caller-owned precommit guard; neither path
  performs transfer or changes activation. After fresh parity succeeds,
  selection validates the lease, invokes that guard, propagates process-control
  exceptions, maps an ordinary guard failure to configuration conflict, and
  revalidates the lease immediately before the final configuration CAS. A
  successful CAS is the last fallible body operation, with no post-CAS lease
  validation.
  Every failure before the final configuration CAS and every cancellation
  preserves configuration bytes; the config owner applies its fixed rollback
  contract to write failures. An installed host package or empty
  provisioned database intentionally survives a later parity/configuration
  failure. Parity treats an empty canonical source or same backend as not
  required, otherwise compares exact canonical archive wire bytes and returns
  `ConversationStorageTransferReadiness`; it knows no transfer report or
  checkpoint. Unverified nonempty parity maps to `TRANSFER_REQUIRED`, while a
  mismatch retains its distinct failure.

  Startup holds one outer workspace coordinator lease while it reloads exact
  configuration, resolves the current marker and desired backend, freshly
  verifies readiness/parity, composes the complete target service bundle, and
  compare-and-sets activation. An unchanged desired backend returns the active
  bundle without writing. Pre-CAS failure preserves activation; successful CAS
  is not rolled back by unrelated later shell failure.
  The separate read-only active composer reads the persisted activation,
  composes the already-active established filesystem or SQLite service bundle
  without borrowing the coordinator, then rereads and requires the complete
  activation value and composed backend to remain identical. Drift raises the
  fixed coordination failure; a stable call does not change activation,
  configuration, the mutation lock, or either backend.
  Import is I/O-free,
  launches no process, and does not load `sqlite3`; installer policy/execution,
  configuration persistence, archive serialization, backend composition, and
  activation publication remain with their named owners.
- **Primary verification/documentation:**
  `tests/test_conversation_storage_selection.py` and
  `tests/test_conversation_storage_selection_cli.py` verify this workflow and
  its provider-free CLI dispatch; existing component and compatibility coverage
  is in `tests/test_conversation_storage.py`,
  `tests/test_conversation_storage_migration.py`,
  `tests/test_sqlite_conversation_storage.py`,
  `tests/test_workspace_and_config.py`, and
  `tests/test_public_api_and_cli.py`; relevant current references are
  `docs/guides/sqlite-conversation-storage.md`,
  `docs/reference/cli.md`, `docs/reference/package-api.md`,
  `docs/reference/configuration/session-storage.md`, and
  `docs/reference/conversation-archives.md`.

### `story_writing_agents.conversation_storage.sqlite_database`

- **Source:**
  `src/story_writing_agents/conversation_storage/sqlite_database.py`
- **Responsibility:** Own the optional SQLite database identity,
  version-2 schema, fixed contained path, capability probe, connection and
  transaction lifecycle, verification, and create-only provisioning mechanics.
- **Supported surface:** Exactly 12 names in `__all__`: fixed
  `SQLITE_APPLICATION_ID`, `SQLITE_BUSY_TIMEOUT_SECONDS`,
  `SQLITE_DATABASE_PATH`, `SQLITE_MINIMUM_VERSION`, and
  `SQLITE_SCHEMA_VERSION` constants; `SQLiteDatabase`, whose supported
  construction and operation surface is `workspace_key`, `verify_existing`,
  `read`, `write`, and `provision`; and content-free
  `SQLiteDatabaseFormatError`, `SQLiteDatabaseMissingError`,
  `SQLiteDatabaseSecurityError`, `SQLiteDatabaseUnavailableError`, and
  `SQLiteDatabaseVersionError`; plus `probe_sqlite_runtime`, the explicit
  active-interpreter functional readiness probe. The contained path property,
  callback aliases, common error base, and connection helpers remain
  unsupported implementation details. The sibling
  `conversation_storage.sqlite` adapter is the production consumer of this
  owner interface.
- **Direct internal dependencies:** `workspace.core`.
- **State, resources, and side effects:** Import defines immutable schema and
  policy metadata only and deliberately does not load `sqlite3`; each requested
  operation lazily loads and probes the standard-library module for SQLite
  3.7.16 or newer. The public runtime probe uses that same lazy owner to require
  a successful in-memory connection, foreign-key enablement, one query, and
  close without touching workspace storage or requiring FTS. One
  `SQLiteDatabase` captures the supplied `Workspace`'s
  canonical root once and retains only its exact key and the fixed contained
  `discussion_logs/conversation_storage.sqlite3` path, so later mutation of the
  supplied `Workspace.root` cannot retarget database I/O. Construction performs
  only path arithmetic and creates nothing. Schema version 2 has fourteen
  explicit application-owned objects: four tables, six indexes, and four
  insert triggers. It retains the established schema-v1 catalog and transcript
  rows inside the version-2 database while adding one authoritative
  `collaboration_events` table. The singleton identity owns the exact
  nonnegative lexical generation; v1 event rows own required canonical
  `searchable_content` beside their public source. Collaboration rows own every
  indexed identity, sequence, timestamp, and batch fact beside the exact
  canonical schema-v2 event BLOB. Two unique collaboration indexes enforce
  event-ID and batch-ordinal identity per conversation, and a separate insert
  trigger enforces contiguous collaboration sequence allocation. The recent-
  outcome index serves bounded v1 context, while each event table's composite-
  primary-key autoindex serves ordered/MAX sequence access, so no redundant
  explicit event-order index is owned. FTS and its shadow objects are absent
  from this authoritative database manifest. The exact version-2 fingerprint
  is the only supported schema; a schema-v1 database is rejected without
  mutation rather than altered or migrated, and this owner exposes no schema-
  conversion operation.
  Each ordinary read or write, and each standalone explicit verification, owns
  and closes one non-creating URI connection. Provisioning and recovery from
  interrupted publication may instead use a bounded sequence of individually
  owned and closed creation or verification connections. Every opened storage
  connection uses a finite five-second busy timeout, verified foreign keys,
  `DELETE` journaling, `FULL` synchronization, UTF-8 encoding, and strict UTF-8
  decoding for stored `TEXT`.
  Malformed stored text raises the content-free database-format failure, while
  a caller-operation `UnicodeDecodeError` is not reclassified. Reads use an
  explicit transaction on a read-only connection so verification and the
  operation share one snapshot;
  writes validate an already-held lease before a `BEGIN IMMEDIATE` transaction
  and again before commit. Both paths commit explicitly, roll back an
  uncommitted transaction, and always close their connection. Routine reads
  and writes perform bounded application ID, user/schema version, exact schema
  manifest, and fingerprint verification without scanning unrelated catalog or
  event rows; configured foreign-key enforcement, schema constraints, and the
  adapter's operation-local reconstruction retain addressed-row domain
  validation. Explicit `verify_existing`/preflight and new, existing, or
  race-winner provisioning validation additionally check database integrity,
  an empty `foreign_key_check`, composite ownership, every contiguous
  event/correlation invariant supplied by the adapter, every derived normalized
  subject, and every canonical searchable v1 event value. The adapter-supplied
  snapshot validator additionally reconstructs every collaboration BLOB and
  verifies its redundant SQL fields, catalog ownership, complete batches, and
  legal full-history projection. Verification rejects
  unsafe or multiply linked named files; a named-database symlink or a
  nonmeaningful device/inode identity fails closed as a security error. Strict
  verification/preflight,
  provisioning, and writes reject every
  rollback/WAL sidecar without repairing or removing it; ordinary reads alone
  tolerate a concurrent DELETE-mode `-journal` at their initial and final
  boundary checks only when it is a singly linked regular file, while still
  rejecting unsafe journal forms and every `-wal` or `-shm` sidecar. Explicit
  fresh provisioning exhaustively validates its private temporary inode before
  installation. Under repeated caller-lease and exact candidate checks it
  creates the target hard link, revalidates before removing the temporary alias,
  verifies the singly linked named target, and synchronizes the file and
  directory; it does not repeat exhaustive domain verification through the
  freshly installed name. Candidate checks include exact owned-pattern
  temporary base names and the corresponding `-journal`, `-wal`, and `-shm`
  suffixed name shapes; ambiguity is preserved and rejected. A lease or
  candidate-validation failure after linking but before alias removal preserves
  both names, and lease expiry never authorizes that alias cleanup. Under a valid
  provisioning lease only, it may recover the single exact interrupted
  publication shape where the target and exactly one fixed-pattern temporary
  sibling are regular names for the same two-link inode with no sidecar or link
  ambiguity. It first exhaustively validates the intact target through a
  read-only connection that requires the exact two-link identity, then
  revalidates the lease, namespace, sidecars, identities, and link counts before
  removing only that alias and synchronizing. The outer provision path then
  exhaustively verifies the resulting singly linked target. Preflight and
  ordinary reads remain observational, and every ambiguous or unsafe shape is
  preserved and rejected.
- **Primary verification/documentation:**
  `tests/test_sqlite_database.py`,
  `tests/test_sqlite_collaboration_event_store.py`,
  `tests/test_conversation_storage.py`,
  `tests/test_conversation_catalog.py`,
  `tests/conversation_catalog_contract.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/conversation_search_contract.py`,
  `docs/guides/sqlite-conversation-storage.md`,
  `docs/design/persistence.md`,
  `docs/reference/collaboration-history.md`,
  `docs/reference/package-api.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`,
  `docs/reference/conversation-search.md`,
  `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.conversation_storage.sqlite`

- **Source:** `src/story_writing_agents/conversation_storage/sqlite.py`
- **Responsibility:** Provide the explicitly selected optional standard-library
  SQLite catalog, v1 and collaboration event stores, exact literal-search,
  archive, runtime probe, storage preflight, provisioning, and composition
  adapter boundary without changing filesystem defaults or activation.
- **Supported surface:** Exactly 15 names in `__all__`: fixed
  `SQLITE_CONVERSATION_STORAGE_APPLICATION_ID`,
  `SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS`,
  `SQLITE_CONVERSATION_STORAGE_DATABASE_PATH`,
  `SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION`, and
  `SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION` constants;
  `SQLiteConversationArchiveAdapter`, `SQLiteConversationCatalog`, direct-
  module-only `SQLiteCollaborationEventStore`,
  `SQLiteConversationEventStore`, and `SQLiteConversationSearch`; and
  `compose_sqlite_conversation_archive`,
  `compose_sqlite_conversation_storage`,
  `preflight_sqlite_conversation_storage`,
  `probe_sqlite_conversation_storage_runtime`, and
  `provision_sqlite_conversation_storage`. The archive adapter directly
  consumes and produces the canonical `ConversationArchive` values owned by
  the sibling `archive` module; it adds no private payload or record surface.
  `SQLiteCollaborationEventStore` is absent from the conversation-storage and
  project-root facades and is not returned by active service composition.
- **Direct internal dependencies:** `conversation_literal_search`,
  `conversation_search`,
  `conversation_storage.archive`,
  `conversation_storage.sqlite_database`,
  `conversation_storage.sqlite_search_index`,
  `conversation_storage.composition`, `conversation_storage.contracts`,
  `conversations`, `errors`, and `workspace.core`.
- **State, resources, and side effects:** Import defines immutable constants
  and adapter classes only; it performs no I/O,
  constructs no adapter, and does not load `sqlite3`. Each adapter retains one
  construction-pinned database owner, exact-workspace mutation coordinator,
  finite mutation timeout, and path-pinned disposable search-index owner.
  Construction of those owners performs path arithmetic only. Ordinary reads
  never provision, convert, repair, or write; preflight verifies only the
  authoritative main database and returns content-free capability while mapping
  unsupported, malformed, unsafe, and unavailable states to established
  storage/workspace failures. The optional companion is not a preflight or
  readiness dependency. The separate runtime probe maps the database owner's
  active-interpreter in-memory functional probe to ready or unavailable without
  touching workspace storage and without an FTS or SQLite CLI gate.
  Provisioning requires an explicit active correct-workspace lease.

  Catalog and event mutations borrow the workspace coordinator before opening
  the SQLite write transaction, so database revision, sequence, turn-slot,
  reply-correlation, required normalized-subject/searchable-content values, and
  one lexical-generation advance are atomic for each effective change. An
  already-effective status change or exact archive reimport does not advance
  the generation. Fresh ID allocation performs one bounded exact-identity event
  probe and rejects orphan rows before inserting metadata. Append obtains one
  plus the indexed maximum sequence and reads at most the addressed turn's
  user/outcome pair; constraints and triggers retain contiguous ordering and
  turn correlation without scanning unrelated transcript history. After an
  effective authoritative commit, while the same lease remains valid, the
  adapter best-effort rebuilds the derived companion from committed visible
  non-runtime event projections. Companion failure cannot roll back main state
  and leaves exact fallback available.

  `SQLiteCollaborationEventStore` reads one complete addressed canonical v2
  history and validates every redundant SQL field, BLOB, batch, transition,
  and catalog identity before returning events or the pure recovery
  projection. Append borrows the established workspace coordinator and uses
  the database owner's `BEGIN IMMEDIATE` transaction directly, so it validates
  the complete existing history, resolves an exact batch retry before CAS,
  assigns contiguous sequences, canonical UTC timestamps, ordinals, size,
  digest, and canonical event bytes, and inserts every event or none. It re-
  reads the completed transaction image before commit. Stored corruption is
  mapped to the fixed read/append boundaries; semantic retry, batch/event
  conflict, stale-sequence, and invalid-transition precedence comes from the
  pure collaboration validator. A precommit failure rolls back the complete
  batch, while an uncertain postcommit result is resolved by the durable batch
  ID and returns the original sequence range and timestamps. This direct v2
  store path does not advance lexical generation, rebuild or consult
  FTS, compose an active service, alter the v1 event store, or itself own
  archive/transfer authority; the separate archive adapter now owns exact
  collaboration transport.

  `SQLiteConversationSearch` owns one main-database read transaction for its
  complete scan: it selects at most 257 metadata rows to bound a 256-
  conversation scan, validates at most 10,000 ordered event rows, preserves
  subject precedence, counts runtime errors without matching them, and derives
  the exact public citations, snippets, counters, truncation flags, and result
  order from authoritative rows. Same-generation FTS coordinates are optional
  non-authoritative hints; missing, stale, malformed, unsafe, unavailable, or
  capability-mismatched companion state becomes the same full bounded literal
  fallback, and search never repairs it. Transcript-context reads use an
  indexed aggregate plus addressed recent-outcome/user queries and materialize
  at most 25 complete pairs to retain 24 and detect the boundary, while
  reporting the exact total and excluding an unmatched newest user.

  Privileged archive export/import validates a caller-supplied outer lease and
  the complete database snapshot. Export rejects any obsolete
  `conversation_events` row and constructs the exact schema-v2 canonical
  archive from every active/archived schema-v1 catalog field and complete
  ordered `collaboration_events` history. Import canonicalizes and completely
  validates the payload, including SQLite signed-integer representability,
  before the write transaction; it rejects any obsolete transcript row before
  mutation, validates every destination row, inserts the supplied canonical
  collaboration event bytes and all assigned identity/batch/order/timestamp
  metadata without regeneration, and revalidates the resulting rows in the
  same transaction. It preserves exact create-or-identical semantics and
  reports divergent identities through the canonical archive conflict
  taxonomy. Effective import participates in the existing generation and
  best-effort post-commit companion refresh lifecycle. The derived FTS
  companion is excluded from archive content, transfer parity, and checkpoint
  identity. A schema-version-one database identity remains unsupported at the
  database-owner preflight boundary; the archive adapter never converts it.
  Valid existing storage remains available to observational preflight and reads
  when POSIX `fcntl` is unavailable, while provisioning and supported mutations
  fail closed through the coordinator; selection/startup owns activation
  readiness. Service composition preflights once and returns same-provenance
  catalog/event-store bindings plus a provider-free
  `SQLiteConversationSearch` binding, one shared pinned companion owner, and
  the shared coordinator; archive composition returns the separate privileged
  adapter. Neither composition changes the selected default, either facade, or
  activation state.
- **Primary verification/documentation:**
  `tests/test_sqlite_conversation_storage.py`,
  `tests/test_sqlite_collaboration_event_store.py`,
  `tests/test_conversation_storage_migration.py`,
  `tests/test_conversation_storage.py`,
  `tests/test_conversation_catalog.py`,
  `tests/conversation_catalog_contract.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/conversation_search_contract.py`,
  `README.md`,
  `docs/INDEX.md`,
  `docs/guides/sqlite-conversation-storage.md`,
  `docs/design/persistence.md`,
  `docs/reference/collaboration-history.md`,
  `docs/reference/package-api.md`,
  `docs/reference/conversation-archives.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`,
  `docs/reference/conversation-search.md`,
  `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.conversation_storage.sqlite_search_index`

- **Source:**
  `src/story_writing_agents/conversation_storage/sqlite_search_index.py`
- **Responsibility:** Own the fixed disposable, capability-probed SQLite FTS5
  conversation-search companion and its contained publication lifecycle.
- **Supported surface:** Exactly five names in `__all__`: fixed
  `SQLITE_CONVERSATION_SEARCH_INDEX_PATH` and
  `SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION` constants;
  `SQLiteConversationSearchIndex`, whose supported operations are
  `candidates` and `rebuild`; immutable
  `SQLiteConversationSearchIndexDocument`; and content-free
  `SQLiteConversationSearchIndexError`. This direct integration surface is
  absent from the package-root and conversation-storage facades.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Import defines immutable SQL/policy
  metadata, validators, and owner classes only; it performs no I/O, does not
  load `sqlite3`, and imports no other story module. Construction accepts and
  pins one already-canonical absolute workspace key, derives only the fixed
  `discussion_logs/.conversation_storage_fts5.sqlite3` path, and performs path
  arithmetic only. Each operation lazily imports the standard-library SQLite
  module and runs a live in-memory create/insert/query/drop FTS5 capability
  probe. A usable owner-only, singly linked regular companion binds exact schema,
  source generation, literal-normalization version, tokenizer, and runtime
  identities; candidate rows contain only caller-supplied session,
  conversation, sequence, and searchable-content projections, never
  authoritative subjects. The SQLite adapter supplies canonical visible
  non-runtime event projections. Missing, stale, busy, malformed, unsafe,
  replaced, capability-mismatched, or unavailable state maps to the one
  content-free owner error for adapter fallback; the owner never treats FTS
  rank or content as search truth.

  `candidates` is read-only and never creates or repairs. Caller-validated
  `rebuild` can populate an absent companion through a private owner-only file
  and create-only hard-link publication, or refresh a recognized existing
  companion in one rollback-journal transaction. Publication repeatedly
  validates the caller callback, contained namespace, file identity, link
  count, and sidecar absence; cleanup is confined to an unchanged call-owned
  temporary or one exact interrupted two-link publication alias. Unsafe and
  ambiguous shapes are preserved. Interruption exposes only the old stale
  generation or a complete new generation; this resource owns no main-database
  readiness, transfer, archive, authorization, configuration, network, or
  diagnostic lifecycle.
- **Primary verification/documentation:**
  `tests/test_sqlite_conversation_storage.py`,
  `docs/guides/sqlite-conversation-storage.md`,
  `docs/reference/package-api.md`,
  `docs/reference/conversation-search.md`,
  `docs/design/persistence.md`,
  `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`,
  `docs/operations/recovery-and-limitations.md`, and
  `docs/reference/configuration/session-storage.md`.

### `story_writing_agents.conversations`

- **Source:** `src/story_writing_agents/conversations/__init__.py`
- **Responsibility:** Provide the stable subject-conversation and schema-v2
  collaboration contract import boundary.
- **Supported surface:** Exactly 83 names in `__all__`: the established 17 v1
  conversation names; the immutable schema-v2 event, proposal, state,
  readable-field/record/context, error, projection, and store contracts defined
  by the two owning leaves; `CollaborationPayload`; the five
  `COLLABORATION_*` constants; the
  two digest functions; the eight strict canonical proposal/event/draft/batch
  encode/decode functions; and the five pure collaboration readable/history,
  bounded-context, candidate, and transition functions. Every binding is the object
  defined by its owning leaf, without a wrapper, and every supported class or
  function retains the canonical `story_writing_agents.conversations` module
  identity. The project-root facade retains exactly its established v1 subset
  and exports no schema-v2 collaboration name.
- **Direct internal dependencies:** `conversations.catalog`,
  `conversations.context`, and `conversations.events`.
- **State, resources, and side effects:** Owns no mutable state or external
  resource. Import loads the three pure contract leaves and deterministically
  binds their public object metadata to the canonical facade identity; it
  performs no filesystem, provider, process, environment, thread, or
  application-startup operation.
- **Primary verification/documentation:**
  `tests/conversation_catalog_contract.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_catalog.py`,
  `tests/test_conversation_event_store.py`,
  `tests/test_collaboration_events.py`,
  `tests/test_collaboration_context.py`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`,
  `docs/reference/collaboration-history.md`.

### `story_writing_agents.conversations.catalog`

- **Source:** `src/story_writing_agents/conversations/catalog.py`
- **Responsibility:** Define subject-conversation identity and lifecycle
  contracts.
- **Supported surface:** Exactly seven owning-module names in `__all__`:
  `CONVERSATION_METADATA_SCHEMA_VERSION`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`,
  `MAX_CONVERSATION_SUBJECT_LENGTH`, `ConversationCatalog`,
  `ConversationMetadata`, `ConversationStatus`, and
  `normalize_conversation_subject`. The conversations and project-root
  facades expose the same defining objects.
- **Direct internal dependencies:** `errors`.
- **State, resources, and side effects:** Immutable metadata values, the
  storage-neutral catalog protocol, and deterministic subject, identifier, and
  timestamp validation only; owns no mutable state or external resource and
  performs no import-time I/O.
- **Primary verification/documentation:**
  `tests/conversation_catalog_contract.py`,
  `tests/test_conversation_catalog.py`,
  `docs/reference/conversation-catalog.md`, and
  `docs/reference/package-api.md`.

### `story_writing_agents.conversations.context`

- **Source:** `src/story_writing_agents/conversations/context.py`
- **Responsibility:** Define pure bounded projections over conversation
  histories.
- **Supported surface:** Exactly 21 owning-module names in `__all__`: the six
  established v1 context names plus `CollaborationEventStore`,
  `CollaborationHistoryProjection`, `CollaborationNextAction`,
  `project_collaboration_history`, `validate_collaboration_candidate_batch`,
  and `validate_collaboration_transition`; plus
  `COLLABORATION_READABLE_CONTEXT_POLICY`,
  `CollaborationContextBootstrap`, `CollaborationReadableContext`,
  `CollaborationReadableContextPolicy`, `CollaborationReadableField`,
  `CollaborationReadableFieldKey`, `CollaborationReadableRecord`,
  `project_collaboration_readable_history`, and
  `select_bounded_collaboration_context`. One validated readable record retains
  every canonical event identity and complete typed public payload while its
  closed fields expose only visible message/reply, proposal purpose/impact/
  assignment/input, dispatch, interruption, fixed state/decision/outcome, and
  call-usage text. The default provider-context selection carries only a
  content-free history bootstrap; explicitly selected event records remain in
  sequence order and are retained atomically under the fixed 24-record/32-KiB
  caps. This provider-context value is distinct from
  `CollaborationEventStore.read_context()`, which continues to return the
  recovery/state projection. The v2 store is the sole batch-oriented
  collaboration port, is consumed by the uncomposed direct-module filesystem
  and SQLite adapters, and returns its owning pure projection;
  candidate validation deterministically returns the original
  `ALREADY_COMMITTED` receipt for an exact retry and raises the fixed append
  failure taxonomy for batch/event conflicts, stale sequence, invalid
  candidates, or corrupt existing history. No active composition, v1 storage,
  or runtime consumer uses it in this change. The
  conversations facade exposes the same defining objects, while the
  project-root facade exposes only the established v1 subset.
- **Direct internal dependencies:** `conversations.events` and `errors`.
- **State, resources, and side effects:** Immutable policy, window, and
  projection/readable values; immutable closed field and transition metadata;
  the v1 and direct-adapter-consumed v2 storage-neutral ports; pure fixed-policy
  selection; and deterministic full-history, candidate,
  transition, correlation, budget-lineage, and atomic co-batch validation
  including exact external-exchange/result closure and rejection of every late
  provider/execution event outside the exact active running attempt. Owns no
  mutable state or external resource and performs no import-time I/O.
- **Primary verification/documentation:**
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_event_store.py`,
  `tests/test_collaboration_context.py`,
  `tests/test_filesystem_collaboration_event_store.py`,
  `docs/reference/conversation-transcripts.md`,
  `docs/reference/package-api.md`, and
  `docs/reference/collaboration-history.md`.

### `story_writing_agents.conversations.events`

- **Source:** `src/story_writing_agents/conversations/events.py`
- **Responsibility:** Define immutable visible conversation and collaboration
  event schemas.
- **Supported surface:** Exactly 55 owning-module names in `__all__`: the four
  established v1 event names; four schema-v2 constants; the closed
  `Collaboration*` actors, kinds, directions, inputs, profiles, basis, budget,
  assignments, proposal, outcomes, payloads, events, batches, receipts,
  failures, and errors; `CollaborationPayload`; two canonical digest
  functions; and eight strict proposal/event/draft/batch encode/decode
  functions. The conversations facade exposes every defining object, while
  the project-root facade retains only the established v1 names.
- **Direct internal dependencies:** `agents` and `errors`.
- **State, resources, and side effects:** Immutable v1 and v2 values plus
  deterministic identifier, strict UTF-8/size/privacy, actor direction,
  proposal closure, 2-round/12-call reservation, payload, outcome, canonical
  JSON, digest, batch, sequence, and timestamp validation only. Owns no mutable
  state or external resource and performs no import-time I/O.
- **Primary verification/documentation:**
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_event_store.py`,
  `tests/test_collaboration_events.py`,
  `docs/reference/conversation-transcripts.md`,
  `docs/reference/package-api.md`, and
  `docs/reference/collaboration-history.md`.

### `story_writing_agents.debug_log`

- **Source:** `src/story_writing_agents/debug_log.py`
- **Responsibility:** Persist content-safe bounded workspace runtime diagnostics.
- **Supported surface:** No project-root public import. Its module-supported
  internal surface is `WorkspaceDebugLog`; the fixed `DebugLevel`,
  `DebugComponent`, `DebugOperation`, `DebugPhase`, and `DebugOutcome` enums;
  and the exact path, size, backup-count, frame-count, and lock-timeout
  constants listed in its `__all__`. `WorkspaceDebugLog.path` joins the fixed
  relative path to the already-resolved workspace root without filesystem
  access; the borrowed workspace itself remains private. Its
  `trace` and `error` accept only the fixed taxonomy, validated optional
  correlation IDs, and an optional fixed outcome. No arbitrary detail mapping
  or caller-supplied timestamp, message, path, traceback field, or serialized
  value is accepted. The separate exact `execution_profile_event` seam accepts
  only validated profile transition, active-snapshot, and confirmation fields
  for the four dedicated execution-profile operations. The fixed internal
  taxonomy includes one `agent_context` operation and content-free
  bootstrap/delta, omission, search/read, answer, invalidation, exhaustion,
  taint, and checkpoint outcomes; it adds vocabulary without changing the
  diagnostic record schema.
- **Direct internal dependencies:** `agents`, `provider_adapter`, and
  `workspace`.
- **State, resources, and side effects:** Import and construction perform no
  filesystem I/O or application startup. Ordinary trace/error calls retain the
  schema-v1 record contract; execution-profile calls own content-free schema-v2
  records containing their safe correlation, canonical agent, route generation,
  activation state, exact nullable provider/model/effort projections and value
  sources, and optional fixed thread/context status. Hot transition events carry
  exact old/new profiles; startup applied snapshots and confirmations carry only
  the active profile; recovery-required records use the fixed error/failed
  projection. Calls share
  compact ASCII-safe JSONL serialization; ordinary errors retain qualified
  exception-type projection and at most the nearest 32
  module/function/positive-line frame facts. They
  never inspect exception prose, filenames, source lines, locals, arguments,
  representations, causes, contexts, or notes. Exact validated session,
  conversation, debate, and canonical agent IDs are the only variable
  correlation strings. A guarded process-wide weak lock registry serializes
  one resolved workspace in process. After the safe-primitive gate passes, the
  persistent `debug_logs/.runtime.lock` independently makes a bounded
  POSIX/Windows cross-process advisory-lock attempt within the shared
  100-millisecond deadline. `Workspace` owns the immutable canonical-root
  identity and supplies the package-internal no-follow open and reverification
  seam plus the `debug_logs` scaffold declaration. For each supported storage
  attempt, this module owns and holds both the verified root descriptor and a
  child `debug_logs` descriptor for the complete guard, rotation, append,
  rollback, and cleanup critical section. It creates or opens the child relative
  to the root, verifies each opened and named directory identity, and performs
  every guard, active-log, and five-backup leaf stat, open, create, unlink, and
  rename relative to the child descriptor; leaf stats do not follow symlinks.
  The module rechecks both canonical directory identities before each forward
  leaf mutation, and root, child, and final-leaf opens request `O_NONBLOCK` where
  the platform defines it. An unavailable root identity or a platform lacking
  `O_DIRECTORY`, `O_NOFOLLOW`, descriptor-relative mkdir/open/stat/unlink/rename,
  or no-follow stat support drops the record without opening a diagnostic leaf
  or using a weaker fallback. Before opening or lazily creating the persistent
  guard, the module validates any existing active file and each existing backup
  through the held child directory as regular, with `st_nlink == 1`, a meaningful
  inode identity, and no larger than 1,048,576 bytes. It then validates any
  existing guard the same no-follow way and requires it to be regular with
  `st_nlink == 1` and a meaningful identity. If any prevalidation fails, no guard
  is opened or created; the size bound applies only to active and backup files.
  The module verifies that the opened guard descriptor and held-directory name
  identify the same regular, singly-linked file before attempting the advisory
  lock and again after acquisition. Under that guard, it snapshots each active
  and backup name as absent or as its exact validated meaningful inode, checks
  the expected absence or identity of every source and destination immediately
  before each rotation unlink or rename, and updates the expected state after
  every successful mutation. It then verifies that the opened active descriptor
  and held-directory name identify the same regular, singly-linked file before
  and during append. It appends to `debug_logs/runtime.jsonl`, rotates before the
  active file would exceed the bound, and retains `runtime.jsonl.1` through `.5`.
  After a partial append or synchronization failure, best-effort rollback
  truncates only if the descriptor remains the exact inode captured before the
  append and that inode remains regular and singly linked. These validations
  cover only the identities observed at each check; they do not make concurrent
  external filesystem changes transactional. Invalid input, contention, unsafe
  storage, serialization, append, synchronization, flush, fsync, rollback,
  rotation, and cleanup failures covered by `Exception` drop only that record
  and never recurse. Cleanup still attempts every independently acquired
  descriptor and lock. An original body process-control `BaseException` wins;
  otherwise the first cleanup process-control failure propagates after all later
  cleanup attempts with its original object and traceback so each consumer can
  apply its boundary-specific fail-safe policy.
- **Primary verification/documentation:** `tests/test_debug_log.py`,
  `tests/test_agent_context_runtime_adoption.py`,
  `tests/test_workspace_and_config.py`, `docs/design/overview.md`,
  `docs/design/persistence.md`, `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/reference/configuration/session-storage.md`.

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

### `story_writing_agents.interactive_auth`

- **Source:** `src/story_writing_agents/interactive_auth.py`
- **Responsibility:** Coordinate provider-neutral interactive authentication
  presentation and selection.
- **Supported surface:** No package-supported API. Package-internal immutable
  `InteractiveAuthResult`, exact `InteractiveAuthResultKind` outcomes,
  `InteractiveAuthCoordinator`, and pure `select_default_provider` policy are
  consumed by CLI and controller orchestration without entering either package
  facade. The coordinator's additive internal picker-presentation keyword
  defaults to the standard layout and accepts the terminal's validated form
  layout without changing authentication, selection, or native-login results.
- **Direct internal dependencies:** `interactive_terminal` and `provider_auth`.
- **State, resources, and side effects:** The coordinator retains borrowed
  authentication and terminal collaborators plus a resource-free picker and
  startup-presentation adapter. Each `run` call forwards its validated
  call-local presentation to that picker and retains none of it afterward; the
  global authentication flow omits it and therefore remains standard, while
  Settings explicitly supplies form presentation. Each full or selected
  native-status probe runs
  in a bounded local Runner/default-executor span under terminal-owned progress;
  callback and incapable terminals remain silent. It projects stable Codex and Antigravity rows
  with explicit authenticated, unauthenticated, unavailable, and indeterminate
  text; disabled state and current selection remain meaningful without color.
  Before login it displays sanitized fixed guidance and completes that terminal
  transient before delegating exact native TTY ownership to `provider_auth`;
  afterward it explicitly refreshes both providers and displays only one fixed
  sanitized success or failure result. It never captures or renders provider
  output, account facts, credentials, environment values, paths, URLs, or
  codes. `select_default_provider` chooses an immediately newly authenticated
  provider, otherwise the sole authenticated provider, otherwise Codex when
  both are authenticated, and otherwise no default. The module owns no native
  process, credential, terminal mode, workspace configuration write, provider
  route, backend, session, network, or import-time I/O.
- **Primary verification/documentation:** `tests/test_interactive_auth.py`
  verifies authentication presentation and coordination, and
  `tests/test_public_api_and_cli.py` verifies the unchanged package facade.
  `docs/design/interactive-cli-design-system.md` documents the provider-state
  picker, default policy, refresh, and startup-presentation contract.

### `story_writing_agents.interactive_commands`

- **Source:** `src/story_writing_agents/interactive_commands.py`
- **Responsibility:** Own the deterministic management and inspection command
  language plus read-only agent roster metadata.
- **Supported surface:** Package-supported `ActionKind`,
  `InteractiveAction`, `CommandSpec`, `COMMANDS`, and
  `parse_interactive_line`; module-supported `AgentRouteSpec` and
  `AGENT_ROUTES`; package-internal completion metadata. Direct message,
  routed/sticky target, composition, send/cancel, run/draft-file, and one-shot
  RAG actions and payload fields are absent. Ordinary text, `@agent`, and the
  removed slash commands deterministically produce the integration-stage
  unavailable guidance. Agent-route aliases remain display/persona metadata
  for existing terminal and override consumers and are not parsed or completed
  as direct invocation grammar.
- **Direct internal dependencies:** `agents`.
- **State, resources, and side effects:** Immutable metadata and pure parsing
  only; no provider, filesystem, terminal, or persistence operation.
- **Primary verification/documentation:** `tests/test_interactive_commands.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_public_api_and_cli.py`, `docs/guides/lead-agent.md`, and
  `docs/reference/interactive-terminal.md` verify this boundary.

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
- **Responsibility:** Own one synchronous management/inspection shell lifecycle.
- **Supported surface:** Package-internal `InteractiveController`. It retains
  authentication, settings, overrides, session binding/lifecycle, conversation
  catalog/focus, search, history, transcript, exchanges, status, explicit
  persisted debate/context/reference inspection, help, and quit. It has no
  provider-turn, consensus, draft, one-shot RAG, sticky-target, artifact
  reference-activation, debate-progress, execution-result, or cancellation
  execution path. The controller uses the non-reference `InteractiveMainInput.read_main`
  seam, supplies an attached session without an agent target, supplies no
  artifact collaborators, and presents deterministic direct-execution
  unavailability.
- **Direct internal dependencies:** `agent_exchanges`, `agents`, `backend`,
  `cli_context`, `conversation_search`,
  `conversation_storage.contracts`, `conversation_storage.selection`,
  `conversations`, `debug_log`, `errors`, `interactive_auth`,
  `interactive_commands`, the retained controller policies/viewer helpers,
  `interactive_settings`, `interactive_terminal`, `orchestrator`,
  `provider_adapter`, `provider_auth`, `provider_catalog`,
  `session_lifecycle`, and `workspace`.
- **State, resources, and side effects:** Owns shell attachment/focus, input,
  picker, settings-progress, and cleanup state. Retained provider calls are
  limited to explicit authentication/settings/provider-catalog management; no
  story provider request, schema-v1 append, debate, or manuscript write occurs.
- **Primary verification/documentation:** `tests/test_interactive_controller.py`,
  `tests/test_interactive_management.py`,
  `tests/test_interactive_cancellation.py`, `tests/test_interactive_search.py`,
  `tests/test_agent_exchange_viewer.py`, `tests/test_session_lifecycle.py`, and
  `tests/test_interactive_pty.py`, with shared controller scaffolding in
  `tests/interactive_controller_fixture.py`; `docs/guides/lead-agent.md`,
  `docs/reference/interactive-terminal.md`, and `docs/design/overview.md`.

### `story_writing_agents.interactive_controller._exchange_policy`

- **Source:** `src/story_writing_agents/interactive_controller/_exchange_policy.py`
- **Responsibility:** Project authoritative exchange inventory into transient
  scope and agent choices.
- **Supported surface:** No project-public import. Its exact package-internal
  `__all__` contains `AgentExchangeProjectionError`,
  `DEBATE_SUBJECT_UNAVAILABLE`, frozen/slotted `ExchangeAgentChoice` and
  `ExchangeScopeChoice`, plus `debate_subject_from_messages`,
  `project_exchange_agents`, and `project_exchange_scopes`. Scope projection
  rejects cross-session or duplicate streams, deduplicates by exact scope kind
  and full ID, aggregates counts, and orders newest workspace sequence first
  with kind and ID tie-breakers. Conversation and debate remain distinct.
  Conversation subjects must come from the caller's complete catalog;
  debate subjects use only the exact first user initiation message of kind
  `prompt` or `draft`, collapse its whitespace, retain at most 512 characters
  as 511 plus an ellipsis, and otherwise use `[subject unavailable]`.
  Agent projection preserves canonical roster order and includes an immutable
  empty choice for every agent without an exact retained stream.
- **Direct internal dependencies:** `agent_exchanges`, `agents`, and `models`.
- **State, resources, and side effects:** Immutable contracts and pure
  projections only. The module owns no inventory, snapshot, catalog, debate
  log, picker, viewer, filesystem, terminal, provider, or persistent state and
  performs no I/O or import-time operational side effect.
- **Primary verification/documentation:** `tests/test_agent_exchange_viewer.py`,
  `docs/guides/agent-exchanges.md`, `docs/design/overview.md`, and
  `docs/design/persistence.md`.

### `story_writing_agents.interactive_controller._exchange_viewer`

- **Source:** `src/story_writing_agents/interactive_controller/_exchange_viewer.py`
- **Responsibility:** Adapt one Story exchange snapshot to the required generic
  JSONL viewer.
- **Supported surface:** No project-public import. Its exact package-internal
  `__all__` contains fixed-prose `AgentExchangeViewerError` and
  `prepare_agent_exchange_view`. Preparation accepts immutable bytes and exact
  Story header facts, lazily imports only installed `jsonl_viewer`, validates
  its exact three-name facade, `ViewerSpec` constructor, `ViewerHost` method
  shapes, `view_jsonl(source, spec, host)` signature, and constructed spec
  projection before returning an operation. Missing packages produce fixed
  installation guidance; opaque, missing, variadic, or incompatible API
  shapes produce the fixed incompatible category before terminal acquisition.
  The generic spec labels the full Conversation or Debate ID and subject,
  identifies the session and canonical agent, prioritizes timestamp, request
  type, and content, and enumerates only the exact durable schema fields as
  searchable. The returned operation accepts the internal borrowed host and
  calls the viewer exactly once; runtime viewer exceptions become fixed
  viewer-failed errors without exposing external prose.
- **Direct internal dependencies:** `agent_exchanges` and
  `interactive_terminal._external_view`. The base-installed `jsonl-viewer`
  exact pin is reached only through this validated lazy external boundary, not
  an internal module edge. The empty `viewer` extra remains an installation
  compatibility alias; the vendored gitlink is audit provenance and never a
  runtime import source.
- **State, resources, and side effects:** Import is I/O-free and does not import
  `jsonl_viewer`. Preparation performs the lazy facade check; a missing or
  incompatible required distribution produces fixed standard-install repair
  guidance before terminal acquisition. It retains one immutable source/spec
  closure only for the returned operation and acquires no terminal, stream,
  filesystem handle, provider, storage writer, or persistent state. All viewer
  state remains external and transient.
- **Primary verification/documentation:** `tests/test_agent_exchange_viewer.py`,
  `tests/test_rag_module_and_packaging_boundary.py`,
  `docs/guides/agent-exchanges.md`, `docs/design/overview.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/reference/cli.md`.

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
  `tests/test_interactive_management.py`, `tests/test_interactive_search.py`,
  `docs/reference/interactive-terminal.md`, and `docs/design/overview.md`.

### `story_writing_agents.interactive_controller._runtime_policy`

- **Source:** `src/story_writing_agents/interactive_controller/_runtime_policy.py`
- **Responsibility:** Validate and classify persisted runtime inspection facts.
- **Supported surface:** Exact package-internal `__all__` contains
  `DebateInspectionFacts`, `DebateContextInspectionFacts`,
  `validate_debate_inspection`, `validate_debate_context_inspection`,
  `validate_session_inspection`, and `classify_inspection_failure`.
  Execution, retrieval, result, and progress policy is absent.
- **Direct internal dependencies:** `errors`, `interactive_terminal`,
  `models`, and `workspace`.
- **State, resources, and side effects:** Pure immutable validation and error
  classification; no runtime, terminal, filesystem, or provider operation.
- **Primary verification/documentation:**
  `tests/test_interactive_management.py` and
  `tests/test_interactive_controller.py` verify the persisted-state projection
  and safe failure mapping.

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
  history store and one irreversible live-or-fallback input selection for the
  editor lifetime; public `read_line` records successful exact nonblank
  commits, while the internal binding supplies a provider to a history-free
  contextual read without retaining it. On a capable first input, the public
  main-line route clears its complete live frame before `read_line` returns;
  ordinary-line fallback retains its printed prompt and input and remains the
  source for all later reads on that driver. The unified driver preserves
  selectable-palette, editing,
  interrupt, EOF, output-failure, and restoration behavior. The history is not
  persisted, and the facade performs no provider, filesystem, persistence,
  subprocess, network, or import-time operational side effect.
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
  `SettingsBaseline`, `SettingsState`, `ProviderRouteEntry`, and
  `PreparedSettingsSave`; fixed `SettingsErrorKind` and redacted
  `SettingsError`; the exact ordered `SETTINGS_LANGUAGES`;
  `load_settings_state`, `update_language`, `update_persona`,
  `update_max_debate_rounds`, `update_conversation_storage_backend`,
  `update_conversation_rag_provider`, `update_conversation_rag_automatic`,
  `update_show_message_details`, `update_agent_backend`,
  `establish_default_backend`, `establish_settings_default_backend`,
  `update_agent_codex_model`, `update_agent_codex_reasoning_effort`,
  `update_agent_antigravity_model`, `update_agent_antigravity_effort`,
  `validate_settings_persona_catalog`, and compatibility `save_settings_state`.
  Package-internal prepare, commit, restore, read, and
  reconcile seams own the exact configuration/provider-route transaction. The
  module has no package-facade export.
- **Direct internal dependencies:** `agents`, `config`, `errors`, and
  `workspace`.
- **State, resources, and side effects:** Loading captures the exact strict-
  UTF-8 bytes of both persisted configuration documents, optional exact route-
  generation bytes, one fully validated immutable configuration snapshot, the
  complete provider-route roster, and a safe lexical persona catalog with
  top-level-template precedence. Pure update operations replace only the
  pending immutable candidate, including the independently validated desired
  conversation-storage backend, nullable allowlisted local RAG provider,
  local-only automatic policy, strict message-details boolean, one agent's
  provider, or one provider-specific model/effort value at a time while
  preserving every inactive-provider value.
  Disabling RAG forces automatic false; enabling automatic without Local fails
  before candidate mutation. These pure transforms never probe or compose the
  optional provider.
  System-level provider model/effort values remain unchanged fallback data
  rather than editable settings.
  `establish_default_backend` persists only a validated missing workspace
  default and never overwrites an existing value. Its settings-specific sibling
  rebases a validated missing provider default only in the pending in-memory
  candidate; authentication and default choice remain caller-owned, and
  Discard Changes or a clean exit preserves the exact opening bytes.
  Preparation completely validates and
  canonically serializes the twelve-key system and seven-key agent candidate,
  rereads both documents, the optional route document, and the catalog for
  exact stale-state detection, and performs no mutation. It
  increments exactly one roster entry's monotonically increasing generation
  whenever its effective provider changes, including a switch back;
  model/effort-only changes retain the generation. The resulting
  `PreparedSettingsSave` carries exact before and after bytes for
  `config/system_defaults.json`, `config/agent_configs.json`, and optional
  `agent_states/orchestrator_state/provider_route_generations.json` plus the
  validated snapshot and switched-agent roster. Commit rechecks all three
  baselines, writes only changed documents through `Workspace` in system,
  agent, then route order, and restores every attempted target in reverse order
  after a write failure. The separate restore seam truthfully recovers exact
  pre-save bytes only after verifying the expected post-save state; uncertainty
  raises the fixed recovery-required category. Startup reconciliation advances
  route generations for direct configuration edits and owns that exact schema-
  v1 file, while an absent file projects generation zero until persistence is
  necessary. `ConfigManager` owns the canonical two-document configuration CAS
  used by the storage-selection workflow;
  prepared commit accepts only the exact before or after configuration image,
  publishes any remaining route state, and includes all three files in reverse-
  order rollback and verified recovery. Configuration commit failures map to
  the established settings categories. The cross-module persona validator
  freshly compares the safe catalog with the opening baseline and raises the
  fixed conflict failure without persisting anything. The module owns the
  candidate, persona-catalog validation, provider-route generations, and exact
  cross-file recovery contract, but no authentication facts, readiness,
  installation, provisioning, parity, activation, terminal, controller,
  backend, runtime, provider, network, subprocess, or import-time operational
  resource.
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
  supported `InteractiveTerminal.write_error` accepts an additive optional
  keyword-only `debug_log_path`; omission preserves the exact established
  two-notice record, while supplied safe one-line strict-UTF-8 text adds
  exactly one third notice labelled `debug log` without changing the typed
  error value. The
  unlisted `InteractiveSemanticOutput` binds one terminal's ordinary semantic
  write capability plus its mode-gated accepted-input projection for controller
  use, and the existing `Field`, `ListView`,
  `Message`, `Notice`, `Panel`, `Record`, `Section`, `SemanticRole`, and
  `ViewState` contracts are likewise facade-bound but omitted from `__all__`.
  Unlisted `InputPresentation`, `MessagePresentation`, and `SpeakerAccent`
  additionally bind the internal standard/form input-layout,
  inline/chat-block layout, and fixed speaker-accent contracts without becoming
  supported exports. `Message` retains exact inline compatibility and
  accepts only additive keyword-only presentation, optional metadata, and
  accent values; `ListView` accepts messages alongside its established record
  and field rows. These bindings support count-aware dense semantic and
  attributed-message views without changing the package-supported surface.
  Callback construction discards accepted-input
  projections because its ordinary reader already owns the visible echo, while
  stream construction routes them through the sole driver's selected input
  mode without exposing that mode. The adapter and contracts add no
  project-root export or raw-resource ownership. Unlisted package-internal
  `InteractiveStartupPresentation` binds the terminal-owned fixed welcome,
  authentication-notice, result-boundary, timing, and cleanup lifecycle for
  `interactive_auth` without exposing capabilities, streams, or a public
  animation API. Unlisted package-internal
  `format_agent_reply_content` shares the pure strict-JSON agent-reply
  presentation projection with controller-owned semantic records without
  becoming a supported public export. Its
  unlisted package-internal `build_stream_terminal` factory is the sole real-CLI
  construction boundary and binds trusted semantic input rendering to the sole
  raw driver without exposing renderer or driver details. Unlisted
  `build_contextual_terminal` preserves the completed contextual-callback seam.
  Unlisted resource-free `InteractiveMainInput` accepts one call-local dynamic
  `Picker` provider for a main read; callback construction bypasses it, while
  capable stream construction projects opaque committed identities through the
  existing selectable main-control vocabulary without changing public
  `InteractiveTerminal.read_main`.
  Unlisted `MainInputHistoryState`,
  `MainInputControlState`, `append_main_input_history`, and
  `transition_main_input` bindings expose the internal pure history seam to the
  standalone compatibility facade without adding root-package exports.
  Unlisted `transition_main_command_input` and `read_main_command_line`
  bindings provide the shared selectable main-command control and driver
  adapters without exposing a terminal leaf. Public
  construction adapts `read_line` to deterministic plain semantic composition
  and confirmation prompts, while internal stream construction binds
  history-aware main plus history-free subject, composition, and confirmation
  readers to one driver's irreversible capable-live or ordinary-fallback input
  selection without prompt inspection. Unlisted
  `InteractivePickerInput`, `Choice`, `Picker`, `Prompt`, `ControlOutcome`, and
  `ControlOutcomeKind` bindings provide one resource-free generic picker input
  boundary without exposing opaque choice identities, terminal leaves, or a
  new package-supported API. Its additive package-internal presentation,
  form-only transient-context, and visible-choice-limit keywords default to the
  exact established standard layout, empty context, and seven-row live
  viewport; the explicit form layout accepts an exact tuple of semantic input
  context plus the same validated one-through-nine bound. Existing `Choice`
  values may carry one
  optional validated plain warning suffix plus an additive keyword-only validated
  semantic description role whose default preserves established construction
  and rendering. Capable ANSI independently decorates the warning suffix and
  description across wrapped rows only when no disabled, selected, or
  highlighted role supersedes them, while plain, `NO_COLOR`, and fallback
  projections retain the exact same text. The exact 12-name `__all__` remains
  unchanged, and this adds no supported facade export. Unlisted
  `InteractivePromptInput` binds one
  resource-free semantic cancellable-line capability whose additive internal
  presentation keyword defaults to the established standard layout and permits
  explicit form rendering for nested settings input;
  callback and stream factories respectively provide deterministic plain and
  sole-driver-backed implementations without changing the exact public
  `__all__`. The installed main, subject, composition, confirmation, picker, and
  cancellable-prompt routes enforce one submitted-input lifecycle: each
  complete capable live frame, including any picker-owned transient context,
  clears before return and cleanup is attempted on failure, while callback and
  line fallback omit that transient context, retain printed prompts and input,
  and emit no synthetic `completed` or `cancelled` control frame. The picker
  and prompt adapters validate their retained `transient` arguments but cannot
  opt out of that lifecycle. These adapters do not expand the exact public
  `__all__` or `InteractiveTerminal` method surface.
  Unlisted `InteractiveAdvisoryProgress`, `InteractiveDebateProgress`, and
  `AdvisoryProgressError` bind startup, advisory, consensus, and Settings
  option-read async progress lifecycles through the same facade: callback and
  incapable terminals await silently,
  while stream construction injects the pure fixed-row renderer into the sole
  driver. They do not expand the public `__all__` or expose terminal leaves to
  the controller.
  Unlisted package-internal `InteractiveTextViewer`, `TextViewerExitKind`, and
  `TextViewerResult` bind one storage-free supplied-text viewer. Each read
  returns only a typed noncommit outcome or one one-based inclusive tuple; it
  exposes no text, display metadata, viewport, terminal, composer, repository,
  or reference state. These bindings remain absent from the exact public
  `__all__` and project-root facade. Unlisted `InteractiveCompositionInput`,
  `ReferenceInputScope`, `ReferenceInputFailureKind`, and
  `ReferenceInputFailure` provide the controller with private resource-free,
  call-local reference-input seams. They do not change the 12-name facade
  export contract or add terminal/storage ownership.
  Unlisted package-internal `InteractiveExternalViewer` binds only one
  terminal's generic external-view operation opener for controller use. It
  exposes no driver, stream, capability, raw-mode, geometry, or external
  package type, retains no operation after `open`, and does not change the
  exact 12-name `__all__` or any public `InteractiveTerminal` method.
- **Direct internal dependencies:** `interactive_terminal._core`.
- **State, resources, and side effects:** Owns no terminal resource or mutable
  process state. Import deterministically canonicalizes supported public object
  module identities and binds the private startup adapter without running it;
  importing performs no stream, capability, filesystem, provider, persistence,
  subprocess, network, or application-startup operation.
- **Primary verification/documentation:** `tests/test_interactive_terminal.py`,
  `tests/test_interactive_line_editor.py`, `tests/test_public_api_and_cli.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_external_view.py`,
  `tests/test_interactive_terminal_viewer.py`,
  `tests/test_interactive_terminal_views.py`, `tests/test_interactive_pty.py`,
  `docs/design/overview.md`,
  `docs/guides/agent-exchanges.md`, `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`; `tests/test_interactive_commands.py`
  and `docs/reference/interactive-command-parser.md` verify the consumed
  command registry contract.

### `story_writing_agents.interactive_terminal._external_view`

- **Source:** `src/story_writing_agents/interactive_terminal/_external_view.py`
- **Responsibility:** Define the generic borrowed-host boundary for transient
  external terminal views.
- **Supported surface:** Package-internal exact `__all__` contains the
  runtime-checkable `ExternalViewerHost` protocol and `ExternalViewOperation`
  callable alias. The protocol exposes only terminal size, color permission,
  complete-frame presentation, closed event reads, and idempotent view close.
  Private validation admits printable text, newlines, and authorized ANSI SGR
  only, so cursor, alternate-screen, raw-mode, and arbitrary control sequences
  cannot cross from a viewer. The private ordinary-line host maps bounded
  commands to the same closed navigation/search/goto/help/cancel vocabulary
  and borrows rather than closes its callbacks or streams.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Protocols, pure validation, and one
  call-local borrowed fallback host only. The module owns no terminal mode,
  signal, geometry source, input latch, stream, viewer engine, filesystem,
  provider, or persistent state and performs no import-time I/O.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_external_view.py`,
  `tests/test_interactive_pty.py`, `tests/test_agent_exchange_viewer.py`,
  `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`.

### `story_writing_agents.interactive_terminal._model`

- **Source:** `src/story_writing_agents/interactive_terminal/_model.py`
- **Responsibility:** Define immutable semantic terminal contracts.
- **Supported surface:** Package-internal semantic roles, explicit view states,
  capability profiles, normalized keys including dedicated Shift+Up and
  Shift+Down viewer events, control outcomes, terminal
  capabilities, line-input state, immutable `ComposerState`, bounded
  `MainInputHistoryState`, correlated `MainInputControlState`,
  `SlashCommandSuggestion`, internal `CommandCompletionCandidate`,
  `CommandCompletionProjection`, the package-internal
  `ARTIFACT_REFERENCE_POSITION_PREFIX` and
  `EMPTY_ARTIFACT_REFERENCE_POSITION_PREFIX` completion namespaces, and the
  distinct `RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX` namespace for an
  editor-correlated nonmodal source picker plus its
  `EMPTY_RANGED_ARTIFACT_REFERENCE_POSITION_PREFIX` empty-source namespace,
  `MainCommandPaletteState`, and correlated `MainCommandControlState`, internal
  `PickerControlState` and bounded
  `PickerViewport` transfer values, the internal rejected control outcome, the
  unlisted purpose-bound `StartupWelcomeStage` and stage-only `StartupWelcome`
  design-component contract outside the general `Component` union, the closed
  `Prompt`, `Panel`, `Section`, `Field`, `Notice`, `Message`, `Record`,
  `ListView`, `SuggestionPalette`, and `Picker` component vocabulary, internal
  `InputPresentation` standard/form, `MessagePresentation` inline/chat-block,
  and `SpeakerAccent`
  magenta/cyan/yellow/green/blue enums, additive keyword-only message
  presentation, optional metadata, and accent values whose defaults preserve
  exact inline construction and rendering, message-capable list rows, shared
  `Choice` with its optional keyword-only validated plain `warning_suffix`,
  FORM-only state-summary text and `SemanticRole`, and additive keyword-only
  validated description text/role metadata,
  `RenderedFrame`, internal `InputPromptView` with additive keyword-only
  standard/form presentation plus an optional exact one-line initial value, and
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
  I/O. The ranged-source namespace reuses that same main-command palette state;
  it introduces no second live picker state. Picker-control values retain one
  validated semantic picker and its call-local window origin. A picker carries
  one validated standard/form presentation discriminator, an exact tuple of
  `Panel`, `Section`, `Field`, or `Notice` transient context accepted only for
  form presentation, and a bounded one-through-nine visible-choice limit whose
  default is seven, while viewport values carry one contiguous slice within
  that exact limit; stable choice identities remain internal correlation keys.
  The context is presentation-only and carries no terminal or durable-output
  ownership. A message owns only validated
  attributed text and immutable presentation metadata; it performs no
  sanitization or layout and its default
  cyan accent has no effect under the default inline presentation. A choice
  owns its immutable warning-suffix, FORM-summary, and description semantics but
  no styling or control precedence. Composer values retain exact accepted lines
  and derive joined content and code-point counts without mutation. Startup
  welcome values carry only one validated semantic stage and no terminal,
  presentation, provider, or authentication data. Models create no ANSI and own
  no callbacks, streams, rendering calculation, runtime, provider, filesystem,
  or storage handles; viewport and rendered-input
  transfer values only carry pure precomputed bounds or renderer-produced
  trusted/plain projections and perform no operational I/O.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_viewer.py`,
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
  a distinct cancel outcome for Escape without history or completion, and the
  private `transition_prefilled_prompt_input`, which additionally makes EOF an
  unconditional typed exit for a seeded or edited prompt, plus
  `project_slash_suggestions` for exact registry-supplied prefix projection and
  `project_composition_suggestions` for fixed ordered `/send`, `/cancel`
  preview projection. `append_main_input_history` retains exact nonblank
  commits with oldest-first eviction at 100, while `transition_main_input`
  performs pure newest/older/newer history navigation, draft restoration, and
  overlay-open arrow suppression. `project_main_suggestion_palette` turns one
  correlated completion projection into the existing semantic palette with
  distinct whole-file insertion and ranged-viewer instructions. Both whole-
  file and ranged empty-source namespaces render the same actionable guidance;
  `project_main_choice_completion` and `project_main_choice_palette` adapt
  controller-owned semantic choices into that same immutable state while
  keeping committed identities opaque, while `main_command_uses_choices`
  reports whether the correlated state owns that dynamic choice cycle.
  `transition_main_command_input` owns mutually exclusive completion and choice
  cycles, palette open/filter/highlight/clamp, static Tab/Enter completion and
  snapshot, dynamic Up/Down highlight and direct Enter commit, Escape
  restoration and latch, and the exact open-palette versus closed-history arrow
  routing. A ranged-source projection opens immediately through that existing
  nonmodal palette, preserves exact candidates instead of applying ordinary
  completion filtering, and never inserts its highlighted path. Enter or Tab
  with a ranged palette remains available for driver-owned viewer activation;
  after Escape, either key reopens the still-correlated palette rather than
  submitting the line. Typing `{` or further direct-marker text leaves the
  editor active, closes or transforms the overlay through normal projection,
  and preserves the exact `$${path}` buffer. Unlisted
  `ranged_artifact_picker_selection` purely
  validates the live palette/projection/span correlation and returns only its
  exact highlighted path. Unlisted `completion_candidate_matches` is the
  single pure, case-sensitive candidate predicate used by capable controls and
  injected fallback: it preserves literal prefix matching and the existing
  omitted `/` help-token match, permits only an omitted `{` after exactly one
  `$` for validated `${...}` candidates, and performs no `$$` normalization, fuzzy or
  substring matching, or case folding. Ranged-source candidates bypass
  ordinary completion insertion and matching.
  `transition_composer_line`
  applies exact `/send`, `/cancel`, `/say /send`, and `/say /cancel` semantics
  to immutable composer state, while `transition_confirmation_answer`
  classifies canonical yes/no/default answers and explicit rejection.
  `initialize_picker`, `reconcile_picker`, `transition_picker`,
  `transition_picker_fallback`, and `project_picker_viewport` own the pure
  generic single-select lifecycle: deterministic enabled highlight selection,
  stable-identity refresh, disabled-row skipping with clamped navigation,
  minimal movement within each picker's validated visible-choice limit, exact
  preservation of its transient context through every reconstructed picker,
  exact numbered or blank fallback
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
  Standard presentation remains the exact established default: opted-in
  prefilled prompts add one sanitized `Current value` field after all supplied
  context and before the focused prompt, and `render_picker_viewport` renders
  numbered live choices with the default seven-row limit, absolute
  position/range facts, simultaneous selected and highlighted cues, plus an ordinal/label-only
  selected-outside-window summary. Explicit form presentation instead separates
  heading, context, guidance, current value, choices, descriptions, and the
  active input with blank rows; renders the concise absolute `Choices N/M`
  counter; uses a plain `>` active pointer, `(selected)`, and explicit disabled
  copy rather than available/highlighted/focused/open labels; can render the
  Settings root's bounded nine-row viewport; and renders any picker-owned
  transient semantic context before the heading with one separating blank row.
  Form choices with state summaries share one complete-picker column after an
  em-dash separator when the physical width leaves at least 20 content cells;
  continuation rows align to that column. The calculation includes every
  choice rather than only the live viewport. At narrower widths, each summary
  stacks at the existing detail indentation so labels remain legible.
  `render_picker_fallback` renders the complete numbered list and semantic input
  prompt under the selected presentation while intentionally omitting that
  context and uses the same responsive summary calculation. Standard pickers
  and suggestion palettes ignore FORM-only summaries. Both form layouts keep
  opaque identities internal and preserve
  equivalent sanitized plain and fixed-ANSI meaning. Their generalized segment-aware
  physical-cell wrapping projects ordered non-overlapping semantic spans across
  narrow rows, independently preserving an optional warning suffix, FORM
  summary role, and optional role-bearing description. Fixed ANSI decorates each
  span only on
  otherwise available rows; disabled, selected, and highlighted whole-row roles
  retain precedence, with selected winning simultaneous selected/highlighted
  state, and every rich projection strips to the exact plain, `NO_COLOR`, and
  fallback text.
  `format_agent_reply_content`
  recognizes only a complete strict JSON object or array, rejects duplicate
  object keys and non-finite constants, and preserves key order and Unicode in
  a two-space-indented projection; malformed JSON, scalars, embedded prose, and
  every other string return exactly unchanged. The fixed `AGENT` role is bold
  terminal-default foreground with the same plain meaning and local reset.
  Messages retain the established inline layout by default. Generated completed
  state is visually silent across components, while every non-idle,
  non-completed state and semantic result-role suffix remains textual;
  unlabeled completed notices fall back to their semantic role. Opted-in chat
  blocks render a sanitized
  speaker header, then optional compact metadata on a subordinate default-styled
  row, directly above every explicit body line beneath a default-styled `│ `
  rail, and own one unrailed terminal-default separator row. A private fixed
  style carrier makes only the header bold plus its
  magenta, cyan, yellow, green, or blue accent; separator, rail, and body remain
  terminal-default in every profile. The
  message-specific word wrapper preserves whitespace and explicit blank or
  trailing-LF rows, prefers word boundaries without normalizing content,
  measures physical cells for combining and wide text, hard-splits long tokens,
  and safely removes the rail when the remaining width or a wide glyph cannot
  coexist with it. `ListView` delegates message rows through that same renderer.
  `render_advisory_progress_frame` projects one fixed text-free sixteen-cell
  block-shade shimmer whose graded bright point travels out and back, without
  ANSI, cursor controls, dynamic content, or state. Package-internal
  `render_startup_welcome_frame` separately consumes one
  exact purpose-bound `StartupWelcome`, never a raw stage index, and projects
  its validated stage without entering the general `Component` vocabulary. It
  selects its prefix-free wide, compact, or narrow layout from the supplied
  physical terminal width while fitting every row to the independently supplied
  safe projection width. The wide layout assembles
  five baseline-aligned printable-ASCII `STORY` glyph rows with one fixed bold
  cyan, magenta, yellow, blue, or green foreground per letter; compact and
  narrow layouts retain the same five-color identity. Fixed local resets make
  the ANSI projection strip to exactly the plain text, and the completed view
  includes explicit team and next-provider-check copy independent of color.
- **Direct internal dependencies:** `interactive_terminal._model`.
- **State, resources, and side effects:** Pure projections only. Dynamic text
  is sanitized before wrapping or fixed styling; the module writes and flushes
  no stream, emits no OSC, query, background, dim, blink, or cursor sequence,
  and owns no environment, terminal, runtime, provider, filesystem, or storage
  state. The startup glyphs, copy, span palette, and four-stage projections are
  immutable fixed module data and perform no import-time operation.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_views.py`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`; focused rich/plain/narrow/wide coverage
  belongs to the interactive terminal test suites.

### `story_writing_agents.interactive_terminal._input_lifecycle`

- **Source:** `src/story_writing_agents/interactive_terminal/_input_lifecycle.py`
- **Responsibility:** Own irreversible terminal input selection and ordered
  live character decoding.
- **Supported surface:** Package-internal `TerminalInputMode`, immutable
  `LiveDecoderSpec`, `inspect_live_decoder_spec`, `LiveCharacterReader`, and
  `TerminalInputLatch`. Decoder inspection admits only registry-canonical
  ASCII, UTF-8, or ISO-8859-1 byte sources whose resolved error-handler callable
  is the built-in `strict` or `surrogateescape` handler. The latch selects
  `LIVE` or `FALLBACK` once, rejects switching and decoder-source drift, and
  provides one synchronous input-operation exclusion boundary. A live reader
  retains one incremental decoder, reads exactly one byte at a time, exposes
  pending decoded suffix state for Escape lookahead, and returns one code point
  at a time from a maximum four-code-point queue.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Construction and decoder inspection
  perform no input read, output, descriptor wait, terminal mutation, or
  callback invocation. A selected live reader borrows the exact byte-read
  callable for the driver lifetime; clean EOF remains stable, strict incomplete
  EOF and read/decode/shape failures poison later reads. The latch exposes the
  exact driver-invoked `KeyboardInterrupt` reset that clears only incomplete
  decoder and pending-suffix state without changing the selected mode. The
  latch owns no stream closure, termios, descriptor,
  readiness/select, key, Escape/CSI, cursor, screen, filesystem, provider,
  runtime, or import-time resource.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_input_lifecycle.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_viewer.py`, and
  `tests/test_interactive_pty.py`; `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`.

### `story_writing_agents.interactive_terminal._viewer_model`

- **Source:** `src/story_writing_agents/interactive_terminal/_viewer_model.py`
- **Responsibility:** Define immutable ranged-text viewer contracts.
- **Supported surface:** Package-internal frozen/slotted `TextViewerDocument`,
  `TextViewerState`, `TextViewerViewport`, `TextViewerExitKind`,
  `TextViewerNotice`, and `TextViewerResult`, plus the package-private disposal
  lifecycle marker. Documents validate exact strict-UTF-8 supplied text and
  one-line display metadata, enforce the private 8 MiB source capacity, reject
  more than 65,536 LF-derived logical lines before splitting or tuple
  materialization, and prove the complete sanitized fallback projection is at
  most 16 MiB UTF-8 before capability, terminal, or callback I/O. Accepted
  documents retain their LF-derived logical lines in an immutable tuple. Empty
  state and viewport values require the exact fixed empty-document notice and
  no active, anchor, selection, or projected line, while nonempty values reject
  that notice. Empty fallback capacity accounts for its exact fixed projection,
  accepting the 16 MiB boundary and rejecting the first excess byte. Results
  contain only an exit kind and an optional normalized one-based inclusive
  tuple.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Immutable call-local values and pure
  correlation validation only. The module owns no stream, terminal mode,
  renderer, callback, filesystem, catalog, repository, runtime, provider,
  controller, parser, composer, or persistent resource and performs no I/O.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_viewer.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.interactive_terminal._viewer_controls`

- **Source:** `src/story_writing_agents/interactive_terminal/_viewer_controls.py`
- **Responsibility:** Apply pure ranged-text viewer transitions.
- **Supported surface:** Package-internal `initialize_text_viewer`,
  `transition_text_viewer`, `transition_text_viewer_fallback`, and
  `project_text_viewer_viewport`. Ordinary Up/Down clears the anchor and moves
  one clamped line; Shift+Up/Down fixes the current anchor and moves the active
  end through extension, contraction, and reversal. Enter selects a singleton
  or normalized anchored range, while Escape, interrupt, and EOF remain typed
  noncommit outcomes. Fallback accepts only exact ASCII positive `START-END`
  or `/cancel`, normalizes reverse endpoints, rejects unbounded numeric text
  before integer conversion, and retains a fixed retry notice.
- **Direct internal dependencies:** `interactive_terminal._model` and
  `interactive_terminal._viewer_model`.
- **State, resources, and side effects:** Pure immutable initialization,
  transition, and bounded contiguous viewport projection only. The module
  retains no state and owns no I/O, terminal, filesystem, provider, runtime,
  controller, parser, composer, or external resource.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_viewer.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `docs/reference/interactive-terminal.md`, and
  `docs/design/interactive-cli-design-system.md`.

### `story_writing_agents.interactive_terminal._viewer_render`

- **Source:** `src/story_writing_agents/interactive_terminal/_viewer_render.py`
- **Responsibility:** Render ranged-text viewer frames safely.
- **Supported surface:** Package-internal `render_text_viewer` and
  `render_text_viewer_fallback`. Live rendering emits one physical row per
  logical source line within the projected height, deterministic one-based line
  numbers and plain current/selected cues, fixed optional SGR, and cell-aware
  ellipsis clipping without wrap or horizontal control. Each live row uses one
  bounded sanitize-and-clip pass over at most its width plus 256 source code
  points. Fallback renders every complete sanitized, un-clipped numbered
  logical line and the exact ASCII range prompt without ANSI or raw controls;
  long fallback lines may wrap naturally in ordinary scrollback.
- **Direct internal dependencies:** `interactive_terminal._model` and
  `interactive_terminal._viewer_model`.
- **State, resources, and side effects:** Pure projections only. Caller
  metadata and content are visibly sanitized before styling; CR, Unicode line
  separators, controls, surrogates, combining marks, and wide characters cannot
  inject terminal controls. Live output remains one clipped physical row per
  projected logical line with width-bounded source work. Complete plain
  fallback output is intentionally un-clipped and may wrap in the borrowed
  ordinary reader's scrollback. The module writes no stream and owns no
  terminal, filesystem, runtime, or storage state.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_viewer.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`, and
  `docs/operations/terminal-safety.md`.

### `story_writing_agents.interactive_terminal._driver`

- **Source:** `src/story_writing_agents/interactive_terminal/_driver.py`
- **Responsibility:** Own stream-backed terminal interaction.
- **Supported surface:** Package-internal `RawTerminalDriver` and
  `detect_terminal_capabilities`. The driver accepts pure line, main-input, and
  history-append collaborators at construction and explicit suggestion or
  completion/palette collaborators per live read. `read_main_line` preserves
  the history-aware compatibility route; package-internal supported
  `read_main_command_line` performs selectable history-aware main editing, and
  `read_contextual_main_command_line` accepts a call-local buffer/cursor-aware
  provider of an existing `RenderedInputFrame` for responsive identity-header
  plus editable-prompt composition. It uses the frame's plain projection for
  fallback and recomputes/validates width-correlated frames during live edits.
  Package-internal `read_reference_main_command_line` and
  `read_reference_input_line`
  accept private call-local activation bindings for main and RUN/CHAT input
  without changing that established method signature. Those bindings carry the
  controls-owned completion predicate and ranged-palette selection callback.
  Fallback raw-probes dollar positions right to left and delegates provider-
  scope, escape-parity, and marker correlation decisions to the supplied
  reference projection; the driver interprets no artifact-reference grammar
  and retains none of those collaborators. On a capable live editor, the
  second character of
  exact `$$` immediately projects the existing main-command palette as a
  nonmodal ranged-source overlay. Up/Down remains controls-owned; Enter or Tab
  consumes only a freshly correlated highlighted path and opens the supplied-
  text viewer without inserting a completion, submitting the line, or mutating
  history. For an empty ranged projection, the driver consumes the reference-
  owned deferred-failure semantic: Enter or Tab reports `EMPTY_SOURCES` only
  while the empty panel is open, while a dismissed panel yields to the
  controls-owned palette reprojection. Exact `$${path}` opens that viewer
  directly, including eligible
  paths with ordinary spaces or Unicode. A single `$` remains ordinary whole-
  file completion. Ordinary fallback activates only after a submitted line:
  its raw probes become actionable only when the scope-aware reference
  projection accepts them, `$$` uses the existing numbered picker before the
  supplied-text viewer, and `$${path}` opens that viewer directly. Neither path
  uses a timer heuristic.
  `read_live_line` remains the history-free contextual compatibility route,
  `read_input_line` accepts an injected trusted semantic renderer for
  history-free composition and confirmation, and `read_line` uses the same
  history-free capable route for stream-backed subject input. The first actual
  driver input irreversibly selects live byte input or ordinary callback
  fallback for every later input method. Supported package-internal
  `read_prompt` accepts an injected
  trusted semantic renderer and pure transition for one cancellable, history-
  free settings line; opted-in values seed capable editing at their end, and
  exact-empty fallback results reuse the supplied value only after Escape is
  classified. Exact Escape cancels in raw or line-fallback mode, and interrupt
  and EOF remain distinct outcomes. Supported package-internal
  `read_picker` accepts pure call-local
  transitions, viewport projection, and live/fallback renderers for a dedicated
  generic single-select lifecycle that does not reuse editable-line state.
  Package-internal `read_text_viewer` accepts only an immutable supplied-text
  state plus pure transition/projection/render collaborators. It decodes real
  CSI Shift+Up/Down, returns a content-free `TextViewerResult`, uses exact ASCII
  range fallback when raw capability is absent, and never discovers or reads a
  file. For byte-backed streams it resolves the declared encoding through the
  public codec registry and admits live raw mode only for canonical `ascii`,
  `utf-8`, or `iso8859-1`, including their registry aliases. Every other or
  unknown codec or non-built-in decoder error handler selects fallback before
  `PC_VDISABLE`, configured-control
  inspection, byte reads, live output, termios mutation, or screen mutation.
  Within an admitted codec, raw VINTR/VEOF values are compared with
  `PC_VDISABLE` before decoding; each enabled control must decode to one code
  point and round-trip to the same byte. Disabled controls remain absent, and
  incompatible, incomplete, or lossy mappings select fallback. Viewer VINTR
  has priority over VEOF and both precede ordinary key
  meanings. The
  driver provides one-write output, redraws on the full immutable main-command
  or picker control state, and normalizes established editing and picker keys.
  Installed stream-terminal main, composition, confirmation, picker, and
  cancellable-prompt reads clear the complete capable live frame before
  returning from submit, commit, cancel, interrupt, or EOF and attempt the same
  cleanup on failure before restoration. An eligible capable main submission
  enters history only after that cleanup and restoration; an eligible fallback
  submission enters history after the ordinary reader returns. Static
  completion-only Enter keeps the live read open, performs no submitted-frame
  cleanup, and appends nothing. Package-internal `write_accepted_input` accepts
  one already rendered semantic payload only after the latch has selected an
  input mode: `LIVE` delegates to the existing progress-safe persistent write,
  `FALLBACK` performs no write because ordinary input remains visible, and
  `UNDECIDED` is rejected. It interprets no component or submitted content and
  retains no commit token or payload.
  The lower-level semantic-input, picker, and prompt methods validate their
  per-read transient flags, while the installed facade adapters always request
  this lifecycle. Line fallback cannot erase printed prompts or input and emits
  no synthetic `completed` or `cancelled` control frame. Supported
  package-internal `run_advisory_progress` accepts one startup, advisory,
  consensus, or Settings option-read awaitable and an injected fixed-row
  renderer, awaits
  silently without raw cursor capability, and on a capable stream joins the
  operation with one indicator task. The row is fitted to a freshly observed
  width while reserving the rightmost cell, cleared as a unit around semantic
  writes, and leaves no completed loading record. A fixed bold ANSI palette
  cycles by frame when decorative SGR is enabled; `NO_COLOR` retains the same
  spatial frames without SGR. Internal
  `AdvisoryProgressError` distinguishes indicator failure from provider failure
  for controller cleanup. Purpose-bound `run_startup_welcome`,
  `run_startup_notice`, and `run_startup_result_notice` accept injected pure
  renderers and timing, progressively display the fixed four-frame welcome or
  one transient authentication notice, and establish a fresh post-provider
  output boundary before results without introducing a general animation API.
  The private welcome-renderer contract carries physical terminal width
  separately from capabilities whose width is the active projection bound.
  This keeps physical 48- and 32-column layout tiers identical across animated
  and static paths: capable live frame replacement alone reserves the
  rightmost cell, while persistent/static fallback rendering uses the full
  configured width.
  Package-internal `open_external_view` accepts only one generic operation,
  rejects reentry and progress overlap through the existing input latch, and
  supplies either a borrowed ordinary-line host or one raw-driver-owned host.
  The capable path alone owns raw activation, alternate-screen entry/exit,
  complete-frame redraw, geometry, closed key/event decoding, canonical
  restored line prompts for goto, history-free live Search field/query prompts,
  prompt-local Escape and unrecognized-sequence cancellation, reactivation,
  retained-frame redraw, and exact terminal restoration. A cancelled Search
  prompt resumes viewer input without emitting an event or changing the
  viewer's existing search state.
  Operation failures outrank host and alternate-screen cleanup failures, while
  uncertain terminal restoration remains fatal; close and all cleanup are
  attempted exactly once. The driver never imports or interprets Story exchange
  records or the external viewer API.
- **Direct internal dependencies:** `interactive_terminal._input_lifecycle`,
  `interactive_terminal._external_view`,
  `interactive_terminal._model`, `interactive_terminal._reference_activation`,
  and
  `interactive_terminal._viewer_model`.
- **State, resources, and side effects:** Sole owner of borrowed input/output
  streams, independent capability checks, detected width, normalized physical
  keys, raw POSIX attributes, functional cursor controls, live redraw writes,
  flushes, and exact restoration. It retains exactly one bounded process-local
  main-input history store per driver instance; each main read resets only its
  call-local navigation state. A contextual input-frame provider is likewise
  call-local and shares the existing editor, history, reference activation,
  cleanup, ANSI/`NO_COLOR`, plain, and narrow-width behavior. An eligible
  fallback commit is appended once
  after the ordinary reader returns, while an eligible live commit is appended
  once only after complete-frame clearing and exact restoration. Static
  completion-only Enter and outcomes without an accepted line append nothing.
  The driver retains one private input-lifecycle latch: the first actual input
  chooses LIVE or FALLBACK permanently. LIVE revalidates the selected
  capability, terminal configuration, and exact decoder source on every later
  read; degradation or drift is operational failure rather than a fallback
  switch. FALLBACK never later performs termios, byte, or readiness input. The
  lifecycle unit owns the persistent one-byte incremental decoder, stable clean
  EOF, poisoning, interrupt reset, reentrancy exclusion, and bounded decoded
  suffix; the driver owns the fixed 50-millisecond Escape readiness select and
  checks that suffix before waiting. It ignores transient POSIX `PENDIN` when
  comparing otherwise exact terminal attributes. Only canonical ASCII, UTF-8,
  and ISO-8859-1 plus aliases and resolved built-in strict or surrogateescape
  handlers proceed to disabled-control inspection and the exact enabled
  VINTR/VEOF one-code-point/one-byte round-trip check; every other or unknown
  codec or handler selects permanent ordinary fallback before control
  inspection, byte input, live output, or terminal/screen mutation. Available
  decoded suffixes are reprocessed without a second reader or sleep loop.
  Completion, prompt-control, and palette
  collaborators are call-local and retain no prompt mode. Composition,
  confirmation, and history-free subject reads never navigate or mutate that
  store. Cancellable prompt and picker input
  likewise never consult or mutate history or completion state; their live
  paths reuse the sole driver's decoding, raw-mode, cursor, write, flush, and
  restoration lifecycle. The capable ranged-source overlay remains part of the
  outer editor control state; only its correlated viewer child runs inside that
  same held input latch and raw-mode lifetime rather than invoking another
  public input operation. Ordinary fallback keeps its callback-owned numbered
  picker and viewer flow. Success replaces only the activation span; cancel,
  interrupt, EOF, expected failure, or disposal restores the outer control
  state and never appends history or submits a composition. Picker input
  observes width again for each redraw,
  and its safe line fallback renders the complete numbered choices. Viewer
  input owns one call-local alternate-screen lifecycle but never hides or shows
  the cursor: it validates before capability inspection, observes bounded width
  and height before the first frame and after every non-exit key, enters raw
  mode and alternate screen once, exits that screen once, restores captured
  termios once, and retains no geometry, text, range, or screen state afterward.
  A resize while idle is reflected on the next viewer interaction without a
  signal handler. Before the first input, pre-activation uncertainty may select
  permanent line fallback; after LIVE selection, failures propagate after
  cleanup with termios restoration taking precedence over screen cleanup and
  the active failure. Viewer fallback
  uses no ANSI, raw mode, or alternate screen. Reference-aware fallback renders
  complete numbered choices only after the marker-bearing line is submitted,
  retains or amends the draft, and requires a separate blank confirmation
  before an amended canonical token can return;
  unresolved markers cannot bypass activation through that confirmation.
  During a capable external-view call, the raw host retains only its last
  validated, successfully written complete frame. Search prompt buffers,
  cancellation state, and that frame are call-local: the shared transient line
  lifecycle clears the prompt display, the driver reactivates the same raw
  configuration before redrawing after cancellation, and host close discards
  the frame. No prompt draft enters command history, viewer events, or durable
  state.
  Semantic input resolves capability and
  width once per read, uses the plain projection for fallback and cursor
  measurement, and emits the trusted styled projection only on its live path.
  Construction performs no stream access or terminal mutation, and history is
  never persisted. During one advisory operation the same driver alone retains
  the active frame and renderer, creates and joins the call-local animation
  task on the controller's existing event loop, serializes persistent writes by
  clearing and redrawing the indicator, cancels and joins the peer task after a
  worker or operation failure, and clears all indicator state before returning.
  It creates no OS thread or import-time task. Each startup presentation uses
  the same synchronous input-operation exclusion without selecting the
  irreversible input mode. A permanently selected fallback remains static and
  performs no delay or termios operation. An undecided driver may inspect a
  compatible decoder and terminal configuration for the call-local guard but
  does not latch either input mode; inability to prove the guard selects the
  static presentation. A capable guarded presentation after LIVE selection
  revalidates the same decoder, capabilities, and configuration, and drift or
  setup failure propagates instead of downgrading the selected source. A
  capable startup presentation requires known ANSI cursor support, a real
  POSIX terminal, no
  `STORY_REDUCED_MOTION`, and atomic `TCSAFLUSH` input discard. The driver uses
  call-local noncanonical/no-echo attributes, discards only type-ahead received
  during the guarded delay, and exactly restores the original terminal before
  native login or subsequent input. For capable live replacement it reobserves
  width before every startup frame, uses that physical width for welcome-tier
  selection, supplies a separately right-margin-reserved safe width, retains
  write-time physical-row geometry, resets SGR during cleanup, and preserves
  the primary error while avoiding speculative cursor erasure after unknown
  write progress. Persistent/static fallback instead renders once against the
  full configured width. Welcome frames change at 0.4-second intervals and
  notices are bounded to 0.8 seconds. A present
  `STORY_REDUCED_MOTION` on an otherwise capable ANSI terminal emits the final
  welcome once with its fixed foreground decoration but without delay, raw-mode
  activation, cursor animation, or flashing; its authentication notices remain
  static plain records. A nonempty, nonzero `NO_COLOR` value, callbacks,
  missing or uncertain capability, non-TTY, non-POSIX, `TERM=dumb`, or
  unavailable safe input discard instead emits one meaningful plain
  LF-delimited static rendering with no delay, ANSI, cursor animation, or
  flashing. Missing, uncertain, non-TTY,
  non-POSIX, or `TERM=dumb` capability on the first input safely selects
  permanent ordinary line input. `NO_COLOR` affects decorative SGR for ordinary
  input and startup: unset, empty or whitespace-only, and exact trimmed `0`
  permit decoration, while every other value opts out and selects the static
  plain welcome. Construction and import remain I/O-free.
- **Primary verification/documentation:**
  `tests/test_interactive_line_editor.py`,
  `tests/test_interactive_terminal_input_lifecycle.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_history.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_external_view.py`,
  `tests/test_interactive_terminal_viewer.py`,
  `tests/test_interactive_pty.py`,
  `docs/design/interactive-cli-design-system.md`,
  `docs/operations/terminal-safety.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.interactive_terminal._reference_activation`

- **Source:** `src/story_writing_agents/interactive_terminal/_reference_activation.py`
- **Responsibility:** Define pure private reference-input activation and edit
  contracts for terminal coordination.
- **Supported surface:** Package-internal frozen/slotted
  `ReferenceInputScope`, `ReferenceActivationRequest`, and
  `ReferenceInputProjection`; content-free `ReferenceInputFailureKind` and
  `ReferenceInputFailure`; plus pure `project_reference_input`, reference-source
  completion, selected-path correlation, whole-span insertion, and inclusive
  range-replacement helpers. Each actionable completion or request carries its
  immutable exact provider scope through selection and replacement. Projection
  scans only that supplied cursor-active provider scope, follows the directive
  parser's odd-backslash escape parity, and recognizes `$`, `$$`, and exact
  `$${path}` markers only when the cursor is strictly after the marker start.
  Nonempty exact `$$`
  produces one span-correlated ranged-source completion/`PICK` pair for the
  live overlay and ordinary fallback; empty exact `$$` instead pairs an empty
  ranged completion with deferred `EMPTY_SOURCES`, keeping actionable capable-
  terminal guidance visible before activation. Active-marker containment
  preserves outer braced spaces, `#`, and Unicode while applying directive-
  parser parity: an odd-escaped `$${...}` can rescan from its second dollar,
  whereas other protected nested dollars do not activate. Fallback delegates
  each raw probe to that same scope-aware projection. Marker discovery remains
  correct for batched or bracketed paste. Replacement validates the complete
  adjusted provider span atomically before constructing new editor state, so
  earlier provider-visible references participate while unrelated command text
  remains excluded.
  Every emitted `${path}` or `${path}#Lx-Ly` token is validated through
  the existing writing-artifact facade. It performs no catalog discovery, text
  read, display,
  submission, history, or authorization operation and is not exported by the
  terminal or project-root `__all__` contracts.
- **Direct internal dependencies:** `interactive_terminal._model` and
  `writing_artifacts`.
- **State, resources, and side effects:** Pure immutable call-local values and
  transformations only. It retains no buffer, catalog, artifact content,
  viewer, terminal mode, input latch, filesystem handle, provider capability,
  operation evidence, or import-time resource.
- **Primary verification/documentation:**
  `tests/test_interactive_terminal_reference_activation.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_artifact_reference_directives.py`,
  `tests/test_interactive_terminal_viewer.py`,
  `docs/guides/artifact-references.md`,
  `docs/reference/interactive-terminal.md`,
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
  callback-width semantics remain compatible. Main prompt projection accepts
  lobby scope with neither identity, attached session-only scope, or the
  established session-plus-agent scope; agent-without-session remains invalid.
  `write_error` alone adds the
  optional keyword-only `debug_log_path` presentation seam: `None` preserves
  the established error record exactly, and supplied validated text becomes
  one sanitized third notice labelled `debug log`. Its package-internal stream
  factory coordinates the driver, controls, command metadata, and current
  capability profile. Lobby, attached-session, and conversation-scope
  orientation plus specialized run, agent, typed-error, help, agent-roster,
  conversation-list, search-hit, and transcript-event writers construct
  semantic components; `InteractiveSemanticOutput` binds the same private
  component writer plus a separate accepted-input writer. Both render the
  closed component vocabulary through the terminal, while the latter delegates
  its completed rendering to a mode-aware callback without exposing terminal
  mode to the controller. The specialized agent writer applies the pure
  strict-JSON reply projection before constructing exactly one chat-block
  semantic message. It maps the canonical lead, plot, character, and execution
  IDs to cyan, yellow, green, and blue speaker accents respectively without
  changing its supported signature or write cardinality, and
  the core binds that formatter through the facade for controller-owned
  retrieval and persisted reply records. Composition opening, rejection, send,
  and discard events plus invalid-confirmation hints use the semantic component
  path; the generic status writer has no remaining internal composer call site.
  The core
  constructs ordered structured composition and confirmation input views and
  maps their pure control outcomes without changing public result contracts.
  Its package-internal command-completion adapter translates canonical command
  contexts into terminal completion models. Its package-internal main-choice
  adapter validates a call-local provider, caches only the active controller-
  supplied immutable `Picker` for rendering correlation, and gives dynamic choices
  precedence over static completion for that exact control state; fallback
  never invokes the provider. Its unlisted facade adapters supply the pure
  selectable control projection plus existing semantic palette renderer to the
  driver. The same coordinator combines command-owned provider scopes with the
  pure reference-activation projection and call-local source-path/read/fixed-error
  callbacks. It gives reference completion precedence only inside an eligible
  span, injects both the controls-owned completion predicate and exact ranged-
  palette selection into each call-local `ReferenceInputBindings`, and binds
  the existing generic picker for ordinary fallback plus the independent
  supplied-text viewer for both capable and fallback activation. Its fixed
  empty-source guidance reflects the broader lowercase-`.md` manuscript-source
  grammar. Filesystem listing and text reads remain caller-supplied callbacks;
  the terminal coordinator acquires neither resource.
  Callback-constructed terminals preserve their established ordinary behavior.
  The internal contextual factory preserves explicit ordinary/main/composition
  callback routing through deterministic plain semantic adapters, while the
  real stream factory binds the selectable main-command route, both preview
  providers, semantic composition and confirmation input, plus the pure
  main-input transitions to one driver. Resource-free `InteractiveMainInput`
  preserves the public main prompt and `TerminalAction` contracts while passing
  call-local choice and reference collaborators to stream input. Its private
  header-provider seam composes the controller's existing `Notice` with the
  existing `Prompt` inside `InputPromptView`; it does not extend the closed
  semantic vocabulary or persist the rendered header. Resource-free
  `InteractiveCompositionInput` selects the same private reference-aware reader
  for RUN/CHAT while retaining the established public `compose` behavior and
  signature. Resource-free
  `InteractivePickerInput`
  validates additive standard/form presentation, an exact form-only transient
  semantic-context tuple, and one-through-nine visible-choice-limit keywords,
  initializes one generic picker, then delegates only its immutable control
  state; callback construction binds deterministic
  plain complete-list picker input, and stream construction binds the same adapter to
  the sole driver's dedicated picker lifecycle with pure control and rendering
  collaborators. Resource-free `InteractivePromptInput` constructs one existing
  `InputPromptView` from an additive standard/form presentation keyword and
  returns typed commit, nested-cancel, interrupt, or EOF. Its standard default
  preserves every established caller and rendering path. Its optional
  keyword-only initial value preserves the unseeded route when
  omitted, seeds capable editing, and substitutes only an exact-empty borrowed
  fallback result; callback and stream construction bind it to plain or driver-owned input
  without adding a component kind or raw resource owner. The picker and prompt
  adapters validate a retained transient flag but always select the universal
  submitted-input lifecycle. Resource-free `InteractiveTextViewer` validates
  and initializes one supplied text before invoking a bound reader, returns
  only a typed inclusive range or noncommit outcome, and crosses the explicit
  disposal marker in `finally`. Callback construction binds its strict
  complete-text line fallback; stream construction binds the same pure state,
  control, viewport, and rendering seams to the sole driver's dedicated raw and
  alternate-screen lifecycle. It retains no viewer state and imports no
  workspace, artifact, runtime, provider, controller, parser, or composer code.
  The real stream factory binds main, composition,
  confirmation, picker, and cancellable-prompt reads to that lifecycle: the
  driver clears the complete capable live frame before return and attempts the
  same cleanup on failure, while callback and line fallback retain printed
  prompts and input and emit no synthetic `completed` or `cancelled` control
  frame. Stream-backed conversation-subject input shares the driver's capable
  history-free route; callback construction remains ordinary. Ordinary
  persistent semantic records retain the established write path. Accepted-input
  components use a no-op callback sink and the driver's selected-mode sink for
  callback and stream construction respectively. Resource-free
  `InteractiveAdvisoryProgress` and
  `InteractiveDebateProgress` bind the terminal's startup, picker preparation,
  advisory, consensus, and Settings option-read awaitable wrappers: callback construction remains
  visually silent, and stream construction injects the pure fixed-row
  renderer into the sole driver without exposing capabilities or streams.
  Resource-free
  `InteractiveStartupPresentation` similarly binds the fixed, prefix-free
  welcome plus semantic authentication guidance and result notices. The core
  owns the canonical ordered `STORY`, `WRITING`, `AGENTS`, and `HANDOFF`
  `StartupWelcome` sequence and the sole adapter from driver frame indices to
  those values; `HANDOFF` means that provider access checking follows, not that
  authentication succeeded. Callback construction writes that final plain
  welcome once, and stream construction delegates physical/safe width
  coordination, guarded timing, terminal reclaim, and cleanup to the sole
  driver. The supported public terminal class and facade exports remain
  unchanged.
  Resource-free `InteractiveExternalViewer` retains only the terminal's
  package-internal generic operation opener. Callback construction binds a
  borrowed plain-line host; stream construction binds the sole driver's
  external-view lifecycle. Neither route imports the required external viewer
  package, exposes terminal resources, nor retains operation, frame, input,
  search, or navigation state after return or failure.
- **Direct internal dependencies:** `agents`, `interactive_commands`,
  `interactive_terminal._controls`, `interactive_terminal._driver`,
  `interactive_terminal._external_view`,
  `interactive_terminal._model`, `interactive_terminal._reference_activation`,
  `interactive_terminal._render`,
  `interactive_terminal._viewer_controls`,
  `interactive_terminal._viewer_model`, and
  `interactive_terminal._viewer_render`.
- **State, resources, and side effects:** Callback-constructed instances retain
  borrowed synchronous read/write callbacks, deterministic plain capabilities,
  and an explicit width but inspect no stream and acquire no resource.
  Stream-factory instances delegate all raw terminal state, width detection,
  keys, writes, flushes, restoration, readiness, irreversible input-source
  selection, persistent incremental decoding, and the bounded process-local
  main-input history store, plus advisory indicator task and cleanup to one
  driver. Main completion, call-local choice, palette, reference scope/catalog/
  read callbacks, and composition providers plus semantic input renderers are
  selected per call and never retained by that driver. The buffer-aware
  contextual header provider is also selected per call and never retained;
  composition, confirmation, cancellable prompt, and picker input are history-
  free. Viewer text, display metadata, selection, and viewport state likewise
  remain call-local and are dropped after every return or raise. Capable
  stream-backed subject input is history-free live input; callback-constructed
  subject input remains ordinary. Callback-constructed terminals and the core
  itself own no history.
  Startup presentation instances retain only terminal-owned callbacks and one
  injectable sleeper. The core owns only the immutable four-value semantic
  sequence and its index adapter, not welcome-frame, glyph, palette, or ANSI
  data; all input exclusion, capability, stream, raw-mode, input-discard, delay,
  geometry, and cleanup resources remain driver-owned. The pure fixed startup
  projection and its immutable visual data remain renderer-owned.
  The semantic adapter retains only the terminal's bound ordinary-component
  writer and accepted-input writer and acquires no stream or raw resource. The
  callback accepted-input writer is a no-op; the stream-bound writer delegates
  the already rendered payload to the driver without retaining it.
  The optional debug-log path is call-local presentation input and is not
  retained by the terminal or semantic adapter.
  There is no runtime, filesystem, persistence, subprocess, network, or
  import-time operational I/O.
- **Primary verification/documentation:** `tests/test_interactive_terminal.py`,
  `tests/test_interactive_line_editor.py`, `tests/test_public_api_and_cli.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_interactive_terminal_foundation.py`,
  `tests/test_interactive_terminal_composer.py`,
  `tests/test_interactive_terminal_picker.py`,
  `tests/test_interactive_terminal_external_view.py`,
  `tests/test_interactive_terminal_viewer.py`,
  `tests/test_interactive_terminal_views.py`, `tests/test_interactive_pty.py`,
  `docs/design/overview.md`,
  `docs/reference/interactive-terminal.md`,
  `docs/design/interactive-cli-design-system.md`,
  `docs/operations/terminal-safety.md`, and
  `docs/operations/recovery-and-limitations.md`.

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
  `DebateResult`. Package-internal schema-v1 `StorySessionContext`,
  `OwnerOutcomeWindow`, `StoryFocusedContext`, `BoundCoreDocumentIdentity`,
  their fixed mode/schema/cap constants, and pure selection/build helpers define
  the bounded provider-neutral continuity envelope without a project-root
  export. `AgentRequest` carries optional canonical `conversation_context` and
  `story_session_context` fields, and `ConversationRequest` carries the latter,
  without changing context-free construction. `AgentDefinition` carries an
  additive default-false, repr-hidden `provider_managed_context` execution-
  strategy marker; the true form rejects bound context documents and lets
  prompt assembly and both adapters distinguish contextual binding without a
  provider-specific sentinel.
  `ConversationResult` and `DebateResult` each carry an additive optional,
  validated, content-free `reference_operation_id`; its default is `None`, and
  `DebateResult.to_dict()` omits the key in that default no-reference case.
- **Direct internal dependencies:** `agents`, `conversations`, and `errors`.
- **State, resources, and side effects:** Value contracts own no process state;
  owner outcomes retain only the newest complete suffix under both 24-entry and
  32,768-UTF-8-byte caps with total/retained/omitted/byte/truncation accounting.
  Story-session serialization records exact session/agent/scope, focused
  included/fresh/omitted context accounting and events when applicable, and
  bound core-document paths and hashes without duplicating document bytes.
  `PromptFileReference.from_path` performs one verified capture for ordinary
  configuration inputs, while constructed references retain immutable strict-
  UTF-8 bytes and never reopen their lexical source paths.
- **Primary verification/documentation:** `tests/test_prompts.py`,
  `tests/test_codex_backend.py`, `tests/test_conversation_runtime.py`,
  `tests/test_session_lifecycle.py`, orchestration and state tests,
  `docs/guides/lead-agent.md`, `docs/reference/agent-contracts.md`,
  `docs/reference/runtime-api.md`, and `docs/reference/backend-and-sessions.md`.

### `story_writing_agents.orchestrator`

- **Source:** `src/story_writing_agents/orchestrator.py`
- **Responsibility:** Own runtime management, configuration activation, session
  binding, and persisted-state inspection.
- **Supported surface:** Package-supported `StoryRuntime` and
  `RuntimeSession`; package-internal `RuntimeConfigurationCandidate`.
  Directive/override management, async prepare/commit/discard, session/debate/
  context/reference inspection, and quiescent provider-binding release remain.
  `converse`, `converse_with_retrieval`, `run_prompt`, `run_draft`,
  and the `Runtime`/`Session` aliases are absent.
- **Direct internal dependencies:** `backend`, `config`,
  `conversation_rag_runtime`, `conversation_storage.composition`,
  `conversation_storage.contracts`, `conversations`, `debug_log`,
  `directives`, `errors`, `prompts`, `workspace`,
  `writing_artifacts`, `writing_artifacts.repository`, and
  `writing_artifacts.runtime_references`.
- **State, resources, and side effects:** Retains workspace, prompt/config,
  storage, optional RAG-service, artifact-repository, backend snapshot, and
  quiescent binding state. It performs only explicit management/inspection I/O
  and no story provider request, transcript append, debate, or publication.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_runtime_reference_integration.py`,
  `tests/test_interactive_management.py`,
  `tests/test_interactive_cancellation.py`, and
  `tests/test_session_lifecycle.py`; `docs/guides/lead-agent.md`,
  `docs/reference/runtime-api.md`, and `docs/design/overview.md`.

### `story_writing_agents.prompts`

- **Source:** `src/story_writing_agents/prompts.py`
- **Responsibility:** Assemble content-addressed agent definitions and
  prompt-free per-turn requests.
- **Supported surface:** Internal `PromptAssembler`, whose optional keyword-only
  `artifact_repository` supplies filesystem-authoritative core context and whose
  optional keyword-only `configuration_snapshot` accepts one immutable
  persisted snapshot without changing the default on-demand construction path.
  Its optional provider-route-generation map and package-internal
  `build_story_session_context` seam bind durable route identity and bounded
  provider-neutral continuity to the exact definition used by a turn.
  `build_contextual_definition()` uses the same stable system prompt, persona,
  language, and provider route generation while deliberately returning an
  empty `context_document_files` tuple and setting the internal provider-managed
  context marker. That marker participates in the contextual definition
  fingerprint, so a direct binding cannot be resumed as a contextual binding;
  the established `build_definition()` keeps its exact eager core-document
  binding and fingerprint behavior for direct and explicitly custom callers.
- **Direct internal dependencies:** `agents`, `config`, `conversations`,
  `models`, `workspace`, `writing_artifacts`, and
  `writing_artifacts.repository`.
- **State, resources, and side effects:** Reads verified prompt references and
  owner-isolated agent history. With no injected snapshot it resolves persisted
  configuration on demand; with a snapshot it reuses those persisted values but
  still loads session runtime overrides for every definition. The legacy path
  requests only the agent's core context paths and binds stable, strict-UTF-8,
  NUL-free immutable byte snapshots into prompt references; no later provider
  step reopens an artifact path. The contextual path performs no core-document
  read and leaves mutable role artifacts outside the provider-binding
  fingerprint for source-owned runtime selection. Present safe empty,
  initialized, manual,
  pre-provenance, published, and hash-divergent files are included; absent or
  unsafe files are omitted, while invalid UTF-8 or NUL content fails closed.
  A nonzero provider-route generation participates in the definition
  fingerprint, while generation zero preserves the legacy fingerprint. This
  forces every persisted provider switch, including a switch back, onto a fresh
  binding/thread identity without changing same-provider model/effort identity.
  For each definition-backed substantive request, the assembler reads the
  owner's session-wide outcomes, applies the fixed complete-entry/UTF-8 caps,
  projects focused context in included/fresh/omitted mode, and derives current
  core-document path/hash identities from that same immutable definition. It
  forwards the resulting story-session envelope and any compatibility
  conversation-context object into the request. The assembler owns no write
  operation; source
  projection delegates lazy internal-state and lease setup to the injected
  artifact repository without catalog loading or transaction recovery.
- **Primary verification/documentation:** `tests/test_prompts.py`,
  `tests/test_agent_context_runtime_adoption.py`,
  `tests/test_codex_backend.py`, `docs/reference/writing-artifacts.md`,
  `docs/reference/agent-contracts.md`, and `docs/design/prompts-and-provider.md`.

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
- **Primary verification/documentation:** `tests/test_cli_context.py` verifies
  strict loading behavior, and `tests/test_interactive_controller.py` verifies
  the direct-module-only import boundary.

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
- **Responsibility:** Run a provider-free deterministic workspace and
  collaboration-contract diagnostic.
- **Supported surface:** Internal
  `run_smoke(workspace: Workspace | None = None)`; no package-supported API.
- **Direct internal dependencies:** `agents`, `conversation_search`,
  `conversations`, `debug_log`, `orchestrator`, and `workspace`.
- **State, resources, and side effects:** Verifies workspace initialization,
  directive persistence, metadata-only catalog persistence, and empty/read-only
  schema-v2 history, readable context, search, and page projections. It calls
  no provider and leaves no transcript, collaboration-history, debate,
  specialist-agent state, provider-session, exchange, or manuscript artifact.
  Temporary mode removes its workspace; explicit mode retains the allowed
  directive and catalog metadata.
- **Primary verification/documentation:** `tests/test_smoke_cli.py` verifies
  temporary/explicit lifecycle, report fields, and provider-free invariants.

### `story_writing_agents.workspace`

- **Source:** `src/story_writing_agents/workspace/__init__.py`
- **Responsibility:** Provide the stable workspace import boundary.
- **Supported surface:** Exactly `Workspace`, `initialize_workspace`,
  `FilesystemConversationCatalog`, and `FilesystemConversationEventStore` in
  `__all__`: the first two are defined by `workspace.core`, and the filesystem
  conversation classes are defined by `conversation_storage.filesystem`. All
  four are the same objects exposed by the project-root facade, while both
  conversation classes are also identical to their owner-module exports.
  `Workspace` and `initialize_workspace` retain canonical module identity
  `story_writing_agents.workspace`; the conversation classes intentionally
  identify `story_writing_agents.conversation_storage.filesystem` as their
  canonical defining module. Established signatures, workspace/root imports,
  and pickle lookup through those canonical modules remain supported.
  Package-internal `open_workspace_root_directory`,
  `verify_workspace_root_directory`, and `WORKSPACE_DIRECTORIES` are statically
  bound on both the facade and core for existing internal consumers but are
  absent from both modules' `__all__` and from the project-root facade. No
  incidental core implementation global or private helper is forwarded.
- **Direct internal dependencies:** `conversation_storage.filesystem` and
  `workspace.core`.
- **State, resources, and side effects:** Owns no state or external resource.
  Import deterministically canonicalizes only the workspace class and
  initializer's defining-module metadata, imports the side-effect-free
  filesystem owner, and performs no filesystem write, provider, environment,
  thread creation, or application-startup operation.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_workspace_and_config.py`, and `docs/reference/workspace-api.md`.

### `story_writing_agents.workspace.core`

- **Source:** `src/story_writing_agents/workspace/core.py`
- **Responsibility:** Own secure generic workspace filesystem persistence.
- **Supported surface:** Exactly `Workspace` and `initialize_workspace` in
  `__all__`. The stable workspace facade re-exports those objects and retains
  their established canonical workspace-module identity. Package-internal
  `open_workspace_root_directory`,
  `verify_workspace_root_directory`, and `WORKSPACE_DIRECTORIES` provide the
  identity-bound internal-storage and topology boundary used by diagnostic,
  agent-exchange, disposable-generation, and smoke owners; they remain
  deliberately absent from both public `__all__` declarations and the
  project-root facade.
  `Workspace.inspect_debate_context` is the strict, separate context-sidecar
  inspection operation. `Workspace.read_agent_session_state` returns one
  validated owner's complete session outcomes in stable chronology, while
  `read_agent_state` remains its debate-ID-validating compatibility delegate;
  topology and serialization details are internal.
- **Direct internal dependencies:** `agents`, `conversations`, `errors`, and
  `models`, plus the `writing_artifacts` facade.
- **State, resources, and side effects:** Owns topology, path and symlink
  security, and each instance's canonical-root identity lifecycle. Construction
  makes one nonmutating no-follow identity probe and retains no descriptor: an
  existing directory binds immediately, while a specifically missing root
  remains pending for its first valid `initialize()` attempt. Invalid
  `overwrite_prompts` input raises `TypeError` before I/O without consuming that
  attempt. A first valid pending attempt becomes terminal before capability
  checks or mutation and preflights the required descriptor-relative directory
  and atomic-file primitives. It walks or creates canonical-root components
  without following names, verifies the opened and named root identities, and
  retains the root descriptor throughout pending topology and default writes.
  Directory and file-parent traversal stays beneath that descriptor. File
  installation stages an exclusive temporary regular file, keeps create-only
  and prompt-overwrite handling distinct, and verifies the staged and installed
  identities. After all writes, initialization rewalks the canonical root and
  matches it to the retained identity. Owned descriptors follow exact-once
  cleanup, and the instance records success only after final root-descriptor
  cleanup completes. Missing primitives raise `OSError` before mutation. The
  exact first `OSError`, `KeyboardInterrupt`, or `SystemExit` from initialization
  or final cleanup propagates unchanged; cleanup does not replace an active
  failure, and created components or intended partial data are not rolled back.
  After any earlier failure, every later `initialize()` call raises the
  established identity-unavailable `OSError` before all filesystem operations
  without retry or rebind. Roots existing at construction, repeated successful
  calls, and ordinary post-initialization path operations retain their
  established behavior; a new `Workspace` performs its own construction probe.
  An existing root whose
  initial probe is unavailable or nonmeaningful remains in the distinct
  unavailable state: diagnostic helper access fails closed without rebinding,
  while established pathname-based initialization may still scaffold it. The
  package-internal helpers open and reverify the bound named root and descriptor;
  once a meaningful identity is bound, a later named or opened symlink,
  non-directory, or identity replacement raises
  `WorkspaceSecurityError`, while an unsupported safe primitive raises an
  ordinary `OSError`. Retained identity protects internal storage owners such
  as `debug_log` and `conversation_rag_store`, not ordinary domain operations:
  public path I/O remains pathname-based and nontransactional and may observe a
  real directory installed later at the same root path. The module also owns
  strict UTF-8 and JSON I/O, overrides, agent and debate state, logs, results,
  and immutable debate-context sidecars under
  `discussion_logs/<session_id>/<debate_id>.context.json`, and generic atomic
  text/JSON replacement, except that its supported generic text/JSON writers
  reject canonical writing-artifact identities plus every case-folded or
  NFKC-normalized alias of the repository-owned `core_documents` and
  `manuscript` namespaces before mutation. It exposes no artifact-specific
  core-document reader, no longer creates core-document templates, and owns no
  repository-mediated manuscript/core-document reads, writes, commits, or
  rollback. Context
  sidecars use an exact identity-bound version-1 schema, require a non-null
  canonical snapshot for included mode and a null snapshot for fresh mode,
  reject malformed or colliding files, and return `None` when absent for legacy
  debates. Persisted debate results may carry one validated content-free
  `reference_operation_id`; omission is canonical for `None` and no-reference
  runs. Workspace owns only that result serialization and never the reference
  evidence or artifact content. Filesystem conversation metadata, transcripts,
  allocation, parsing, serialization, cleanup, and the guarded weak transcript-
  lock registry belong exclusively to `conversation_storage.filesystem`; core
  neither defines nor imports those owners. The fixed initialized topology
  includes
  `agent_states/orchestrator_state/conversation_defaults` and `debug_logs`;
  existing initialized workspaces need neither directory until the corresponding
  owning feature first creates it lazily. `debug_log`, not `Workspace`, owns the
  files, lock, schema, append, and rotation inside the diagnostic directory. New
  workspaces also receive the canonical twelve-key system-default document with
  null Codex model/reasoning, Antigravity model/effort, and workspace
  `default_backend` and `conversation_rag_provider` values, automatic RAG set
  to false, desired `conversation_storage_backend` set to `filesystem`, and
  `show_message_details` set to false; canonical seven-key
  per-agent entries retain a null backend,
  language, persona, and both providers' model/effort values.
  Existing configuration bytes are never migrated or rewritten by
  initialization. `cli_context` owns the schema stored in the conversation-
  default directory. The backend-owned story-session process-access claim
  remains the cross-process same-session write guard.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `tests/test_debug_log.py`, `tests/test_agent_state.py`,
  `tests/test_public_api_and_cli.py`,
  `tests/conversation_catalog_contract.py`,
  `tests/test_conversation_catalog.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_event_store.py`,
  `docs/reference/workspace-api.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`, `docs/design/persistence.md`,
  and `docs/operations/filesystem-safety.md`.

### `story_writing_agents.writing_artifacts`

- **Source:** `src/story_writing_agents/writing_artifacts/__init__.py`
- **Responsibility:** Provide the side-effect-free canonical writing-artifact
  value facade.
- **Supported surface:** Exactly 31 names in `__all__`: canonical core and
  resource-limit constants; `ArtifactKind`, `ArtifactCatalogEntry`,
  `ArtifactCatalog`, `ArtifactSourceIdentity`, `ArtifactContentSnapshot`,
  `ArtifactLineRange`, and `ArtifactReference`; immutable
  `ArtifactReferenceBatch` and its
  `ArtifactReferenceDirectiveError`; content-free
  `AuthorizedArtifactReference`, `ReferenceAuthorizationErrorCode`,
  `ReferenceAuthorizationError`, `ReferenceOperationFailure`,
  `ReferenceOperationManifest`, `ReferenceOperationState`,
  `ReferenceInvocationCorrelation`,
  `ReferenceInvocationState`, `ReferenceEvidence`, and
  `ReferenceOperationHistory`; content-free `ReferenceReadActionKind`,
  `ReferenceReadRequest`, and `ReferenceReadAction`; plus
  `canonical_artifact_kind`, `manuscript_artifact_path`, and
  `parse_artifact_references`. `canonical_artifact_kind` and
  `manuscript_artifact_path` retain strict publication grammar; public
  reference values independently accept the broader safe filesystem-source
  grammar owned by `references`. The filesystem source adapter, repository,
  evidence adapter,
  synchronous read coordinator, and transient content-bearing authorization,
  capability, delivery, and read-result values are intentionally not
  re-exported.
- **Direct internal dependencies:** `writing_artifacts.authorization`,
  `writing_artifacts.catalog`, `writing_artifacts.directives`,
  `writing_artifacts.read_actions`, and `writing_artifacts.references`.
- **State, resources, and side effects:** Owns no state or resource. Importing
  the facade constructs only immutable metadata and performs no filesystem,
  provider, process, environment, or application-startup operation.
- **Primary verification/documentation:** `tests/test_writing_artifacts.py`,
  `tests/test_artifact_reference_directives.py`,
  `tests/test_artifact_reference_authorization.py`,
  `tests/test_artifact_reference_read_actions.py`,
  `tests/test_public_api_and_cli.py`,
  `docs/guides/artifact-references.md`, `docs/reference/writing-artifacts.md`,
  `docs/reference/workspace-api.md`, and `docs/design/persistence.md`.

### `story_writing_agents.writing_artifacts.catalog`

- **Source:** `src/story_writing_agents/writing_artifacts/catalog.py`
- **Responsibility:** Define immutable artifact provenance values and strict
  publication path grammar.
- **Supported surface:** Package-supported `CORE_ARTIFACT_PATHS`,
  `ArtifactKind`, `ArtifactCatalogEntry`, `ArtifactCatalog`,
  `canonical_artifact_kind`, and `manuscript_artifact_path`. Canonical core
  identity is limited to the four exact `core_documents/*.md` paths;
  manuscripts require normalized `manuscript/<part-id>/.../*.md` identities,
  and output syntax is the corresponding required `PART/PATH.md` suffix. The
  safe component `manuscript` is a legal part identifier. Package-internal
  `validate_artifact_path_syntax` owns only shared normalized, workspace-
  relative, strict-UTF-8 POSIX lexical validation; it classifies neither
  publication nor reference-source eligibility. Catalog entries are
  publication and audit metadata; they do not authorize filesystem discovery
  or reads.
- **Direct internal dependencies:** None.
- **State, resources, and side effects:** Immutable values and pure validation
  only; catalog mappings are copied and exposed read-only.
- **Primary verification/documentation:** `tests/test_writing_artifacts.py`,
  `tests/test_workspace_and_config.py`,
  `docs/reference/writing-artifacts.md`, `docs/reference/runtime-api.md`, and
  `docs/reference/workspace-api.md`.

### `story_writing_agents.writing_artifacts.references`

- **Source:** `src/story_writing_agents/writing_artifacts/references.py`
- **Responsibility:** Define immutable reference-source eligibility, artifact-
  reference identities, directive batches, and verified source snapshots.
- **Supported surface:** Package-supported `ArtifactSourceIdentity`,
  `ArtifactContentSnapshot`, `ArtifactLineRange`, `ArtifactReference`,
  `ArtifactReferenceBatch`,
  `ArtifactReferenceDirectiveError`, `MAX_ARTIFACT_PATH_BYTES`,
  `MAX_REFERENCE_COUNT`, `MAX_REFERENCE_UTF8_BYTES`,
  `MAX_REFERENCE_TOTAL_BYTES`, and `MAX_VIEWER_UTF8_BYTES`. The batch preserves
  exact strict-UTF-8 source and provider-projected text beside ordered,
  content-free path/range identities, enforces the 16-reference limit, and
  rejects duplicate, whole/range-conflicting, or overlapping identities.
  Token discovery, runtime token resolution, and provider framing are absent
  from this value leaf. Package-internal `reference_source_kind` accepts only
  the four exact core paths or a safely representable lowercase-Markdown
  descendant of `manuscript/`, including direct children, arbitrary nesting,
  ordinary spaces, and Unicode. It applies no publication part/component
  regex. Brace-closing, control/format/surrogate, Unicode line/paragraph
  separator, unsafe lexical, and non-Markdown identities remain ineligible so
  `${path}` indicators round-trip exactly. `ArtifactSourceIdentity` records
  that reference-source path/kind, full-source hash and byte size, plus device,
  inode, modification, and change stamps; `ArtifactContentSnapshot` correlates
  exact strict-UTF-8, NUL-free bytes to that content-free identity.
- **Direct internal dependencies:** `writing_artifacts.catalog`.
- **State, resources, and side effects:** Pure frozen values, including a
  physical filesystem identity and exact hash-verified immutable byte snapshot,
  with no I/O or import-time operational side effect.
- **Primary verification/documentation:** `tests/test_writing_artifacts.py`,
  `tests/test_artifact_reference_directives.py`,
  `tests/test_public_api_and_cli.py`, `docs/reference/writing-artifacts.md`,
  `docs/reference/runtime-api.md`, and `docs/reference/cli.md`.

### `story_writing_agents.writing_artifacts.directives`

- **Source:** `src/story_writing_agents/writing_artifacts/directives.py`
- **Responsibility:** Parse explicit writing-artifact reference directives into
  immutable content-free batches.
- **Supported surface:** Package-supported `parse_artifact_references`. It
  recognizes ordered `${path}` and `${path}#Lx-Ly` tokens, validates complete
  reference-source identities, including eligible spaces and Unicode, and
  strict positive-ASCII one-based inclusive ranges. It projects odd/even
  backslash escaping without changing retained source text and atomically
  rejects malformed or reserved unresolved dollar syntax, unrepresentable
  paths, range continuations, the seventeenth occurrence, and ambiguous
  repeated-path combinations.
- **Direct internal dependencies:** `writing_artifacts.references`.
- **State, resources, and side effects:** Pure call-local scanning and immutable
  value construction only; it performs no filesystem, repository, provider,
  persistence, controller, terminal, or import-time operational work.
- **Primary verification/documentation:**
  `tests/test_artifact_reference_directives.py`,
  `tests/test_writing_artifacts.py`, and
  `docs/reference/writing-artifacts.md`.

### `story_writing_agents.writing_artifacts.authorization`

- **Source:** `src/story_writing_agents/writing_artifacts/authorization.py`
- **Responsibility:** Own provider-neutral explicit-read authorization
  contracts and their call-local content lifetime.
- **Supported surface:** Package-supported content-free
  `ReferenceAuthorizationErrorCode`, `ReferenceAuthorizationError`,
  `ReferenceOperationFailure`,
  `AuthorizedArtifactReference`, `ReferenceOperationManifest`,
  `ReferenceOperationState`, `ReferenceInvocationCorrelation`,
  `ReferenceInvocationState`, `ReferenceEvidence`, and
  `ReferenceOperationHistory`, all re-exported by the writing-artifact facade.
  `ReferenceOperationFailure` exposes only its validated operation identity and
  authorization error code; it never incorporates artifact content or provider
  exception text.
  A public invocation correlation carries a positive contiguous ordinal,
  canonical operation/invocation/agent/phase identities, an optional
  nonnegative round number, and a positive attempt; capability identity is not
  caller-controlled or exposed by that contract.
  Module-supported repository/evidence seams own strict identifier validation,
  schema-record conversion, universal-newline selection, ordered proof
  construction, and the unexported `ReferenceAuthorization`, `ReadCapability`,
  and `ArtifactReferenceDelivery` transient values. New durable records use
  authorization schema version 2 and carry reference-source identities rather
  than catalog entries; version-1 records cannot authorize or resume work.
- **Direct internal dependencies:** `writing_artifacts.catalog` and
  `writing_artifacts.references`.
- **State, resources, and side effects:** Pure strict-UTF-8 byte selection
  treats CRLF as one separator, LF and lone CR as separators, preserves exact
  terminators, omits a phantom line after a final terminator, treats an empty
  source as zero lines, and retains a final unterminated fragment. It validates
  the complete source for UTF-8 and NUL before selecting an exact unclamped
  one-based range. One selection is bounded at 128 KiB and one ordered batch at
  256 KiB inclusive. Frozen proofs and evidence carry paths, ranges,
  full-source identities, selected hashes, counts, lifecycle identities,
  states, and coded outcomes but never artifact content. One closeable
  authorization owns selected immutable bytes
  and returns correlated proofs plus bytes atomically under its lock;
  repository-minted one-use capabilities consume before delivery validation;
  identity-only, nonserializable deliveries own the provider-facing byte tuple
  until explicit or context-manager close. Closing an authorization drops its
  captured byte references without promising memory zeroing. Locks are
  call-local to these transient values; importing the module performs no I/O
  or other operational side effect.
- **Primary verification/documentation:**
  `tests/test_artifact_reference_authorization.py`,
  `tests/test_writing_artifacts.py`,
  `tests/test_artifact_reference_directives.py`,
  `docs/reference/writing-artifacts.md`, `docs/design/persistence.md`,
  `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.writing_artifacts.read_actions`

- **Source:** `src/story_writing_agents/writing_artifacts/read_actions.py`
- **Responsibility:** Define provider-neutral contracts for one mandatory
  writing-artifact read prelude.
- **Supported surface:** Package-supported, frozen, content-free
  `ReferenceReadActionKind`, `ReferenceReadRequest`, and
  `ReferenceReadAction`, all re-exported by the writing-artifact facade.
  Module-supported `ReferenceReadInvocation` is the synchronous coordinator
  protocol consumed by the backend and implemented by the repository;
  `ReferenceReadResult` is its closeable, content-bearing return. The same
  module supplies its backend consumer with the exact line-range, reference,
  correlation, and authorization-error types used by this protocol while
  their stable package ownership remains with `references`, `authorization`,
  and the writing-artifact facade.
- **Direct internal dependencies:** `writing_artifacts.authorization` and
  `writing_artifacts.references`.
- **State, resources, and side effects:** Requests and actions contain only one
  validated full invocation correlation and a nonempty exact ordered reference
  tuple. Results retain only the corresponding tuple of selected bytes until
  explicit or context-manager close, use identity equality, hide content from
  `repr`, reject serialization, and drop their byte references on close without
  promising memory zeroing. The protocol requires synchronous delivery,
  result-received, acceptance, and termination methods. Construction and
  import perform no filesystem, repository, provider, process, controller, or
  application-startup operation.
- **Primary verification/documentation:**
  `tests/test_artifact_reference_read_actions.py`,
  `tests/test_codex_backend.py`,
  `tests/test_artifact_reference_authorization.py`,
  `docs/reference/writing-artifacts.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.writing_artifacts.runtime_references`

- **Source:**
  `src/story_writing_agents/writing_artifacts/runtime_references.py`
- **Responsibility:** Coordinate one call-local top-level runtime artifact-
  reference operation across parse-once projection, repository capture,
  correlated provider invocations, and terminal finalization.
- **Supported surface:** Package-internal `RuntimeReferenceOperation`,
  `prepare_runtime_reference_operation`, and
  `reference_operation_belongs_to_session`; none is re-exported by the
  writing-artifact or project-root facade. Preparation preserves exact source
  and provider-text projections and uses a no-operation-ID fast path when no
  references exist. Each referenced provider attempt receives a fresh
  contiguous ordinal, UUID-backed invocation identity, and attempt value 1.
  While open, `reference_proofs` projects the exact ordered immutable
  content-free authorized-reference facts without exposing captured bytes or
  the repository-owned authorization capability, allowing context composition
  to reserve the same reference batch without private-state access.
  Successful completion requires no pending invocation before repository
  finalization; failures terminate unfinished invocations and return one safe,
  structured `ReferenceOperationFailure`.
- **Direct internal dependencies:** `writing_artifacts.authorization`,
  `writing_artifacts.directives`, `writing_artifacts.read_actions`,
  `writing_artifacts.references`, and `writing_artifacts.repository`.
- **State, resources, and side effects:** One operation owns only a call-local
  reentrant lock, pending-invocation map, next ordinal, generated identities,
  immutable source/provider projections, and a borrowed concrete repository.
  It owns no filesystem, persistent-evidence, provider, process, or terminal
  resource; repository calls remain the only operational effects. Importing
  the module performs no I/O.
- **Primary verification/documentation:**
  `tests/test_runtime_reference_integration.py`,
  `tests/test_agent_context_runtime_adoption.py`; production inventory and
  clean-import checks in `tests/test_interactive_controller.py` and
  `tests/test_session_lifecycle.py`; the user workflow and lifecycle are in
  `docs/guides/artifact-references.md`, while orchestration consumers are
  recorded in `docs/design/overview.md` and
  `docs/operations/recovery-and-limitations.md`.

### `story_writing_agents.writing_artifacts._reference_evidence`

- **Source:**
  `src/story_writing_agents/writing_artifacts/_reference_evidence.py`
- **Responsibility:** Persist and validate content-free explicit-read lifecycle
  evidence.
- **Supported surface:** Private `ReferenceEvidenceStore`, constructed and
  called only by `writing_artifacts.repository` while its repository lease is
  active.
- **Direct internal dependencies:** `writing_artifacts._storage` and
  `writing_artifacts.authorization`.
- **State, resources, and side effects:** Uses only the secure storage
  adapter's supported descriptor-relative directory, read, list, and
  create-only publication operations. It owns schema-version-2 canonical JSON
  under fixed `input_snapshots/<operation-id>/` operation and invocation
  layouts. Operation and invocation manifests and every numbered event are
  immutable; recursive duplicate keys, unknown or content-bearing fields,
  noncanonical bytes, unsafe/colliding identities, sequence gaps, changed
  correlations or filesystem-source proofs, capability reuse, noncontiguous or
  duplicate invocation ordinals, and durably stored illegal or post-terminal
  transitions fail closed
  as corruption for that operation. After fully validating healthy stored
  evidence, an illegal repeated or post-terminal caller request is rejected as
  an invalid transition before any write. Inspection orders invocation
  histories by their verified ordinal, returns no history for a completely
  absent valid operation, and treats an existing partial directory as
  corruption. Operation capture, revalidation,
  acceptance, and termination plus invocation authorization, delivery,
  result-received, acceptance, and termination are exact state machines.
  Accepted operations require all invocations terminal and at least one
  accepted invocation. The adapter retains no artifact bytes, opens no lock,
  does not enumerate repository transaction journals, and cannot make evidence
  corruption block unrelated catalog, commit, or recovery work.
- **Primary verification/documentation:**
  `tests/test_artifact_reference_evidence.py`,
  `tests/test_artifact_reference_authorization.py`,
  `tests/test_writing_artifacts.py`, `docs/reference/writing-artifacts.md`,
  `docs/design/persistence.md`, `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`,
  `docs/operations/recovery-and-limitations.md`, and
  `docs/reference/configuration/session-storage.md`.

### `story_writing_agents.writing_artifacts._storage`

- **Source:** `src/story_writing_agents/writing_artifacts/_storage.py`
- **Responsibility:** Enforce descriptor-relative no-follow filesystem access
  for the writing-artifact repository.
- **Supported surface:** Private `SecureWorkspaceStorage` and
  `ArtifactTraversalLimitError`, consumed only by
  `writing_artifacts.repository` and its package-private
  `writing_artifacts._reference_evidence` and
  `writing_artifacts.filesystem_sources` adapters. The repository remains the
  only lease owner; both adapters receive the same instance and use its
  supported methods only within repository-managed calls.
- **Direct internal dependencies:** `errors`.
- **State, resources, and side effects:** Construction captures only a lexical
  absolute workspace-root name, without symlink resolution, filesystem access,
  or a platform/security probe. Required primitives are checked lazily on first
  artifact I/O, which binds a meaningful root `(st_dev, st_ino)` pair. Every
  later operation reopens and verifies that identity. Every successfully opened
  child descriptor enters call-local ownership immediately; a post-open
  validation failure unwinds through one reverse cleanup and closes it exactly
  once. Each traversal uses
  retained and revalidated ancestor descriptors, `O_NOFOLLOW`, nonblocking
  pre-stat/name/descriptor checks, unique-regular-file checks, and exact stored
  entry spelling. Final file names are alias-checked before their first stat and
  reverified around descriptor reads. Metadata-only recursive discovery reads
  no file bodies, uses descriptor-relative streaming scans, counts every raw
  descendant entry against one call-local budget before filtering, fails the
  complete traversal on overflow, skips unsafe or alias-colliding entries, and
  retains and revalidates descriptor ancestry. Stable reads return exact bytes
  plus device, inode, size, modification, and change stamps. Descriptor-relative
  directory listings reject case-folded or NFKC-normalized aliases before
  access; traversal fsyncs each parent directory entry after successful child
  creation and before descending. Publication also fsyncs its target/staging
  directories and uses a lazily required Darwin/Linux native atomic no-replace
  rename. Publication,
  replacement holding, rollback, temporary cleanup, and completed-journal
  removal never overwrite or unlink a name: prior, failed, and completed bytes
  move exclusively into ignored non-authoritative staging and remain retained.
  The adapter owns a permanent readiness triple: exact primary, anchor, and
  ready hard-link names for one meaningful regular inode with link count three.
  Each acquisition first takes a transient exclusive bootstrap flock on the
  already-open artifact-state directory descriptor. When all three names are
  absent, bootstrap owns retained-ancestry fsync, exclusive primary creation and
  file/parent fsync, anchor link/parent fsync, verified two-link state with the
  ready name absent, ready link/parent fsync, and final triple verification. It
  then acquires the primary flock, reverifies ancestry and the triple, and
  releases bootstrap before body entry; an existing triple opens without create
  flags. Partial, legacy two-link, aliased, divergent, or extra-link states are
  retained and fail closed without cleanup, reconstruction, or migration. Each
  context owns its directory and primary descriptors/flocks, and its active
  mutation lease includes and reverifies the ready link for that lifetime.
  It fails closed on unsupported primitives, special files, symlinks,
  hardlinks, identity changes, lock replacement, destination collision, or
  ambiguous traversal. The lease serializes supported repository writers;
  manual filesystem mutation is supported only while workspace processes are
  stopped, not as a hostile same-user live namespace writer.
- **Primary verification/documentation:** `tests/test_writing_artifacts.py`,
  `tests/test_workspace_and_config.py`,
  `docs/reference/writing-artifacts.md`, `docs/design/persistence.md`, and
  `docs/operations/filesystem-safety.md`.

### `story_writing_agents.writing_artifacts.filesystem_sources`

- **Source:**
  `src/story_writing_agents/writing_artifacts/filesystem_sources.py`
- **Responsibility:** Authorize referenceable writing-artifact sources from the
  current filesystem.
- **Supported surface:** Package-internal `CanonicalArtifactFilesystem`, plus
  its package-private strict-UTF-8, NUL, and inventory-limit format errors and
  hard traversal/inventory constants. `list_paths_bounded` lists the
  four exact core paths and every safely representable regular lowercase-
  Markdown file recursively below `manuscript/`, including direct children,
  arbitrary nesting, ordinary spaces, and Unicode. It selectively captures
  requested paths, returns bounded text, and compares a later complete source
  identity without consulting publication provenance.
- **Direct internal dependencies:** `errors`, `writing_artifacts._storage`,
  `writing_artifacts.catalog`, and `writing_artifacts.references`.
- **State, resources, and side effects:** Retains one borrowed
  `SecureWorkspaceStorage` and owns no descriptor or lock. Listing performs
  metadata-only safe-file inspection, includes empty files, filters through
  reference-source eligibility, applies caller-selected bounds no greater than
  8,192 raw manuscript entries and 4,096 total eligible sources, and orders
  complete workspace-relative paths. The no-argument listing delegates to
  those hard bounds; neither overflow returns a partial first-N inventory.
  Capture validates complete strict UTF-8 and NUL-free bytes and mints one
  immutable content snapshot carrying its full hash, size, and physical stamp.
  All descriptors remain call-local to `_storage`; import performs no I/O.
- **Primary verification/documentation:** `tests/test_writing_artifacts.py`,
  `tests/test_artifact_reference_authorization.py`, `tests/test_prompts.py`,
  `tests/test_runtime_reference_integration.py`,
  `docs/reference/writing-artifacts.md`,
  `docs/guides/artifact-references.md`, and
  `docs/operations/filesystem-safety.md`.

### `story_writing_agents.writing_artifacts.repository`

- **Source:** `src/story_writing_agents/writing_artifacts/repository.py`
- **Responsibility:** Coordinate canonical writing-artifact repository
  operations.
- **Supported surface:** Module-supported internal
  `WritingArtifactRepository`, with provenance-audited `catalog`,
  filesystem-authoritative sorted `list_paths`, complete bounded
  `list_paths_bounded`, ordered selective immutable
  `read_current`, verified `read_text`, and
  unanimous `commit_consensus` operations; plus capture, invocation
  authorization, one-use delivery, result receipt, invocation acceptance or
  termination, operation revalidation or finalization, and content-free
  evidence inspection for explicit reference reads. Its
  `begin_reference_invocation(authorization, correlation)` factory returns the
  package-private synchronous coordinator that alone owns one repository-minted
  capability across provider action, delivery, result receipt, acceptance, or
  termination. The repository is not exported by either package facade.
- **Direct internal dependencies:** `agents`, `errors`,
  `writing_artifacts._reference_evidence`, `writing_artifacts._storage`,
  `writing_artifacts.authorization`, `writing_artifacts.catalog`,
  `writing_artifacts.filesystem_sources`, `writing_artifacts.read_actions`, and
  `writing_artifacts.references`.
- **State, resources, and side effects:** Owns a per-instance reentrant lock
  and the workspace-local permanent `.artifacts.lock`,
  `.artifacts.lock.anchor`, and `.artifacts.lock.ready` readiness triple.
  Provenance state is the exact sibling `catalog.json`, `commits/`,
  `transactions/`, reserved `input_snapshots/`, and non-journal `staging/`
  topology. Staging retains exclusively named prior versions, failed
  publications, and completed journals; recovery enumerates only authoritative
  transaction JSON and ignores all such residue. Immutable exact-name commit
  records form the permanent audit ledger, and their stored bytes must exactly
  equal the repository's canonical JSON serialization. Manuscript paths must be
  unique across the ledger, and the catalog path set must equal the union of
  every artifact path across every non-exempt commit, with repeated core paths
  collapsed. Every catalog entry must project an exact artifact and metadata
  record from its named commit. Historical core records may be superseded, but
  their paths remain required; for each repeated core path the current entry
  must name the unique commit with the greatest parsed, offset-aware
  `committed_at`. Equal greatest instants and stale projections fail closed.
  Each supported commit instant is strictly greater than the maximum instant in
  the entire ledger even if the clock moves backward; timestamp overflow fails
  before transaction or target mutation. Outside one exact pending phase, a
  missing catalog is valid
  only with an empty non-exempt ledger; divergence fails closed and is never
  reconstructed. Every catalog audit and commit performs locked recovery
  first. One
  create-only immutable schema-v2 transaction journal records the
  exact catalog-before existence/hash/image, complete catalog-after image,
  immutable commit, ordered targets, old/new hashes, and complete new bytes
  before any target mutation; it has no replaceable phase field. JSON decoding
  rejects duplicate object keys recursively for journals, catalogs, and commits.
  At exact catalog-before, recovery accepts each target only at its recorded old
  or new bytes and may exempt only this journal's exact already-published commit
  from the expected path union and newest-core authority selection, so that
  commit contributes no paths while all older established manuscript and core
  paths remain required; the projected catalog-after is audited without an
  exemption. Recovery completes remaining
  target and commit publication,
  rechecks journal authority and catalog-before, then publishes the catalog
  last. At exact catalog-after, recovery is verification-only with no exemption
  and requires every target, commit, and ledger projection to be final. Any
  other catalog position, unrelated catalog or commit entry, target/commit
  divergence, or missing/changed journal creates a durable recovery-block
  marker and fails closed. The exact immutable journal is strictly retired only
  after final target, commit, catalog-after, and ledger verification; a missing
  retirement source is never success.
  Source listing acquires the same permanent repository lease without
  recovering transactions or loading or auditing the catalog or ledger. It
  metadata-inspects the four exact core paths and recursively walks only the
  `manuscript/` tree, filters every result through reference-source eligibility,
  and returns deterministic complete workspace-relative paths. It reads no
  artifact body and includes safe empty, initialized, manual, pre-provenance,
  published, hash-divergent, root-level, arbitrarily nested, space-bearing, and
  Unicode current Markdown files. Unsafe aliases, overlong, unrepresentable,
  non-Markdown, `.gitkeep`, symlinked, non-unique hard-linked, device, FIFO,
  socket, and absent files remain excluded.
  Selective reads reopen only the requested reference sources and return
  immutable snapshots after stable descriptor reads, strict UTF-8 decoding,
  and NUL rejection. Supplied-text viewing borrows the same source boundary
  with its caller-provided 8 MiB bound. Catalog or ledger absence, corruption,
  or membership does not participate in discovery, prompt reads, viewing, or
  reference capture, and no read reconstructs, adopts, or changes provenance.
  Root, lock, path, or source-identity insecurity still fails closed. The
  repository imports no controller or terminal unit and retains no viewer
  state. Consensus commits require the execution specialist for
  revision-zero manuscripts and the lead for later manuscripts, publish them
  create-only beneath a part directory, reject every identity already reserved
  by immutable commit history even when its file or catalog entry was externally
  deleted, and allow only lead-authored core updates. Transaction/commit/
  catalog-before/catalog-after sets, unrelated-entry preservation, authorship,
  ordering, and scalar schemas are correlated exactly. Publication and
  recovery retain their complete provenance, ledger, journal, authorship, and
  hash audits; filesystem read eligibility never weakens those write gates.
  Explicit-reference capture returns `None` before a lock, filesystem access,
  or history when the parsed batch has no references. Otherwise one existing
  source/evidence lease securely reads each unique requested path once,
  validates its complete strict-UTF-8, NUL-free bytes, and captures canonical
  path/kind, full-source hash and size, plus device, inode, modification, and
  change stamps. Ordered whole/ranged selections enforce the 128 KiB
  per-reference and 256 KiB aggregate limits; an empty whole source is valid
  while an empty ranged selection fails. Durable authorization evidence uses
  schema version 2 and stores only source identities, range facts, selected
  hashes, and lifecycle metadata. Schema-version-1 authorization evidence is
  not reusable. Delivery consumes its exact repository-minted capability
  before any revalidation, requires the complete ordered request, persists
  delivery evidence before exposing captured bytes, and never refreshes the
  snapshot. Provider-result acceptance and top-level workflow acceptance each
  reopen and re-hash every unique source and require the complete captured
  physical identity, so deletion, replacement, content drift, and same-byte
  replacement are drift. Retries use new invocation
  correlations with the next positive ordinal and a new private capability
  identity over the same operation snapshot. Finalization disposes captured
  bytes only after a durable accepted or terminated event.
  The private reference-read coordinator serializes its own state under one
  call-local lock, rejects every action except the first exact request, converts
  one repository delivery into one closeable read result, closes result bytes
  before result-received evidence, and revalidates before acceptance. Repeated,
  mismatched, failed, and explicitly terminated calls preserve the primary
  failure while best-effort retiring the same capability; it performs no
  provider call and exposes no capability identity.
  The repository owns both lock modes and all filesystem and recovery lifetime;
  its source/evidence mode creates only required state/evidence directories and
  never calls recovery or loads publication state. The private filesystem
  adapter applies reference-source eligibility and owns capture, the private
  evidence adapter owns only create-only schema mechanics, and authorization
  values own only transient bytes and one-use state.
- **Primary verification/documentation:** `tests/test_writing_artifacts.py`,
  `tests/test_artifact_reference_authorization.py`,
  `tests/test_artifact_reference_evidence.py`,
  `tests/test_artifact_reference_read_actions.py`,
  `tests/test_runtime_reference_integration.py`,
  `tests/test_smoke_cli.py`, `docs/guides/artifact-references.md`,
  `docs/reference/writing-artifacts.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/design/persistence.md`, `docs/operations/filesystem-safety.md`,
  `docs/operations/privacy-and-provider.md`, and
  `docs/operations/recovery-and-limitations.md`.

## Consistency Notes

- Attached management input crosses only `InteractiveMainInput.read_main`.
  It passes `session_id` with no `agent_id`, which terminal core renders as
  `story[SESSION]> ` without constructing a direct-execution target. No
  reference scope, path-list, or text-reader collaborator crosses that call,
  so ordinary `$` input and removed commands cannot activate artifact access.
  This correction changes no import edge or public export.
- Direct story execution was retired without adding, deleting, renaming, moving,
  splitting, or merging a production module. The inventory remains 97 modules.
  Static AST derivation over every module-level and function-local import yields
  360 unique direct internal edges and no directed cycle, down from 385 edges.
- The 25 removed edges are execution-only: CLI no longer depends on `models`
  or `run_inputs`; interactive commands no longer depend on
  `conversation_search`; controller core no longer depends on RAG
  composition/retrieval, models, run inputs, or writing artifacts; runtime
  policy no longer depends on agent-context, retrieval, or search; orchestrator
  no longer depends on agent-context planning/service/source modules, agents,
  retrieval/search, models, or session lifecycle; and smoke no longer depends
  on retrieval, models, session lifecycle, or the artifact repository.
- The project-root facade contains exactly 118 exports. Only `Runtime` and
  `Session` were removed; `StoryRuntime`, `RuntimeSession`, generic
  backend/model contracts, and the still-schema-v1 active storage exports
  remain.
- `models.py` remains a provider-neutral value-contract owner with direct
  dependencies on `agents`, `conversations`, and `errors`.
  `run_inputs.py` remains the strict input-loading helper with its sole direct
  dependency on `errors`. Neither module changed in this contraction.
- Generic `AgentBackend`, `BoundAgent`, provider adapter, request/reply,
  prompt, context, and lifecycle seams remain in their established owner
  modules for later workflow work. No lead/specialist workflow was introduced.
- Active conversation storage remains on the existing schema-v1-shaped service
  boundary. Direct schema-v2 collaboration values, projections, search, pages,
  citations, stores, archive, and transfer contracts remain available through
  their existing direct modules; no active-v2 cutover occurred.
- Interactive terminal progress, composition, and reference-activation modules
  and the generic `run_inputs` module remain indexed because this issue did
  not conclusively establish them as globally dead. The contracted controller
  has no execution-time call path into those terminal capabilities.
- All dependency arrows point inward to existing owners. No new dependency,
  packaging change, policy exception, or structural compatibility layer was
  introduced. Removed public/runtime/command surfaces have no forwarding,
  deprecation, migration, or compatibility shim.
- The final documentation-link and production-isolation conformance check is
  `tests/test_agent_context_conformance.py`. Restored retained-shell coverage
  remains in `tests/test_interactive_pty.py`,
  `tests/test_interactive_management.py`, and
  `tests/test_interactive_cancellation.py`; the latter two share only the
  test-local `tests/interactive_controller_fixture.py`, which is not a
  production importable unit.
