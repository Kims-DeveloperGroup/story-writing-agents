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

## Current Dependency Direction

The current graph flows from entry points and orchestration toward provider,
configuration, prompt, persistence, and stable contract modules:

```text
__main__ -> cli -> orchestrator -> prompts -> config -> workspace
                 |               |         |         -> conversations/models/errors/agents
                 |               |         -> workspace
                 |               -> workspace/models/agents
                 -> backend -> config/workspace/models/errors/agents
                 -> cli_context -> workspace/errors/agents

smoke -> orchestrator/workspace/models/agents
directives -> config/workspace/errors/agents
interactive_commands -> agents
story_writing_agents facade -> interactive_commands
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
  `normalize_conversation_subject` from `conversations`; `ActionKind`,
  `InteractiveAction`, `CommandSpec`, `COMMANDS`, and
  `parse_interactive_line` from `interactive_commands`; `InputKind`, `Phase`,
  `Verdict`, `DebateStatus`, `SessionLeaseState`, `SessionSummary`,
  `DebateMessage`, `AgentStateEntry`, `PromptFileReference`, `AgentDefinition`,
  `AgentRequest`, `AgentReply`, `ConversationRequest`, `ConversationReply`, and
  `DebateResult` from `models`; `StoryRuntime`, `RuntimeSession`, `Runtime`, and
  `Session` from `orchestrator`; and
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`,
  `Workspace`, plus `initialize_workspace` from `workspace`.
- **Direct internal dependencies:** `agents`, `backend`, `directives`, `errors`,
  `conversations`, `interactive_commands`, `models`, `orchestrator`, and
  `workspace`.
- **State, resources, and side effects:** Owns no state or external resource;
  importing the facade loads its exported modules.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_codex_backend.py`, `tests/test_conversation_catalog.py`,
  `tests/test_session_lifecycle.py`, `tests/test_conversation_event_store.py`,
  `tests/test_interactive_commands.py`, `docs/reference/package-api.md`, and
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
  `errors`, `models`, `orchestrator`, and `workspace`; imports `smoke` lazily for
  the diagnostic command.
- **State, resources, and side effects:** Reads draft files, writes JSON or
  errors to standard streams, creates and closes backends, and runs the async
  event loop.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_cli_context.py`, `tests/test_smoke_cli.py`,
  `docs/reference/cli.md`, and `docs/getting-started.md`.

### `story_writing_agents.cli_context`

- **Source:** `src/story_writing_agents/cli_context.py`
- **Responsibility:** Discover initialized workspaces and manage workspace-local
  CLI session selection.
- **Supported surface:** Internal `CliContext`, `resolve_workspace`,
  `load_cli_context`, `select_session`, and `resolve_session`.
- **Direct internal dependencies:** `agents`, `errors`, and `workspace`.
- **State, resources, and side effects:** Reads workspace sentinels and owns
  `agent_states/orchestrator_state/cli_context.json`, written through
  `Workspace`.
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
- **Responsibility:** Deterministically parse interactive lines into immutable
  typed actions and publish canonical command metadata.
- **Supported surface:** Package-supported `ActionKind`, `InteractiveAction`,
  `CommandSpec`, `COMMANDS`, and `parse_interactive_line`; all parser helpers,
  agent aliases, and command aliases are internal.
- **Direct internal dependencies:** `agents`.
- **State, resources, and side effects:** Immutable metadata only; owns no
  process state and performs no provider, filesystem, persistence, environment,
  or import-time operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_commands.py`,
  `tests/test_public_api_and_cli.py`, and
  `docs/reference/interactive-command-parser.md`.

### `story_writing_agents.models`

- **Source:** `src/story_writing_agents/models.py`
- **Responsibility:** Define immutable cross-boundary request, reply, state, and
  result contracts.
- **Supported surface:** Package-supported `InputKind`, `Phase`, `Verdict`,
  `DebateStatus`, `SessionLeaseState`, `SessionSummary`, `DebateMessage`,
  `AgentStateEntry`, `PromptFileReference`, `AgentDefinition`, `AgentRequest`,
  `AgentReply`, `ConversationRequest`, `ConversationReply`, and `DebateResult`.
- **Direct internal dependencies:** `agents`, `conversations`, and `errors`.
- **State, resources, and side effects:** Value contracts own no process state;
  `PromptFileReference` performs verified filesystem reads.
- **Primary verification/documentation:** `tests/test_prompts.py`,
  `tests/test_codex_backend.py`, `tests/test_session_lifecycle.py`, orchestration
  and state tests,
  `docs/reference/agent-contracts.md`, `docs/reference/runtime-api.md`, and
  `docs/reference/backend-and-sessions.md`.

### `story_writing_agents.orchestrator`

- **Source:** `src/story_writing_agents/orchestrator.py`
- **Responsibility:** Coordinate debate phases, consensus, failure handling,
  finalization, and protocol invariants.
- **Supported surface:** Package-supported `StoryRuntime`, `RuntimeSession`, and
  compatibility aliases `Runtime` and `Session`.
- **Direct internal dependencies:** `agents`, `backend`, `config`, `directives`,
  `errors`, `models`, `prompts`, and `workspace`.
- **State, resources, and side effects:** Owns the in-memory binding cache,
  in-flight bind tasks, and lock; sequences provider work and persistence while
  `Workspace` owns filesystem mechanics.
- **Primary verification/documentation:** `tests/test_orchestration_workflow.py`,
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

- `Workspace` owns persistence mechanics; `StoryRuntime` sequences persistence
  as part of the debate workflow.
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
- The filesystem event adapter's process-wide weak locks serialize one
  conversation in process; the story-session lease remains responsible for
  cross-process same-session write exclusion.
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
