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

Importable production units indexed: 40.

## Current Dependency Direction

The current graph flows from entry points and orchestration toward provider,
configuration, prompt, persistence, and stable contract modules:

```text
__main__ -> cli -> interactive_controller -> interactive_controller._core
                 -> interactive_terminal -> interactive_terminal._core
                 -> interactive_auth -> interactive_terminal/provider_auth
                 -> interactive_settings -> agents/config/errors/workspace
                 -> provider_auth -> (standard library only)
                 -> run_inputs -> errors
                 -> debug_log -> agents/workspace
                 -> orchestrator -> conversation_retrieval -> agents/conversation_search
                 |               |                           -> conversations/errors
                 |               -> conversation_search -> conversations/errors
                 |               -> debug_log -> agents/workspace
                 |               -> prompts -> config -> workspace
                 |               |         |         -> conversations/models/errors/agents
                 |               |         -> conversations
                 |               |         -> workspace
                 |               -> conversations/workspace/models/agents
                 |               -> session_lifecycle
                 -> backend -> antigravity_adapter -> models/provider_adapter/provider_process
                 |          -> codex_adapter -> agents/config/errors/models
                 |          |                 -> provider_adapter/provider_process
                 |          -> config/conversation_retrieval/debug_log
                 |          -> workspace/models/errors/agents
                 |          -> provider_adapter/provider_process/session_lifecycle
                 -> cli_context -> workspace/errors/agents

interactive_controller._core -> interactive_controller._session_policy
                             -> interactive_controller._conversation_policy
                             -> interactive_controller._runtime_policy
                             -> agents/backend/cli_context/debug_log
                             -> conversation_retrieval/conversation_search
                             -> conversations/errors/interactive_commands
                             -> interactive_auth/interactive_settings/interactive_terminal
                             -> models/orchestrator/run_inputs
                             -> session_lifecycle/workspace
interactive_controller._session_policy -> errors/interactive_terminal/session_lifecycle
interactive_controller._conversation_policy -> conversation_search/conversations
                                            -> errors/interactive_terminal/workspace
interactive_controller._runtime_policy -> conversation_retrieval/conversation_search
                                       -> errors/interactive_terminal/models/workspace

smoke -> agents/conversation_retrieval/conversation_search/conversations/debug_log
      -> models/orchestrator/session_lifecycle/workspace
debug_log -> agents/workspace
directives -> config/workspace/errors/agents
interactive_commands -> agents
interactive_auth -> interactive_terminal/provider_auth
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
provider_adapter -> models
provider_auth -> (standard library only)
provider_process -> (standard library only)
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
  The re-exported `ActionKind` includes the additive `AUTH` case, while it and
  `InteractiveAction` retain the earlier additive search and transcript cases
  plus the optional positive `start_sequence` payload; the facade name count
  and established import paths are unchanged.
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
- **Responsibility:** Coordinate provider adapters under the provider-neutral
  session, lifecycle, retrieval, and reply contracts.
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
- **Direct internal dependencies:** `agents`, `antigravity_adapter`,
  `codex_adapter`, `config`, `conversation_retrieval`, `debug_log`, `errors`,
  `models`, `provider_adapter`, `provider_process`, `session_lifecycle`, and
  `workspace`.
- **State, resources, and side effects:** Owns exact V2 provider-session
  manifests at
  `agent_states/orchestrator_state/provider_sessions/<session_id>.json`, exact
  V2 process-access claims at sibling `<session_id>.access.json` paths, the
  lazily created workspace-global `.access-claims.lock` OS advisory guard,
  in-memory claim tokens, persisted `provider_threads`, shared
  debate/conversation/retrieval per-thread locks, adapter routing, and
  structured domain-response validation. Manifests contain only `schema_version`,
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
  per-instance `atexit` registration. One retrieval thread lock covers the
  complete repeated-call action loop and synchronous handler invocations. The
  backend owns the exact action schemas, initial and
  continuation payloads, canonical observation framing handoff,
  incomplete-history taint decisions, and fixed response-validation
  diagnostics. It receives no memory service and performs no retrieval search
  or transcript-storage call. Debate requests add the canonical
  included-context event list to provider payloads only when present; absent
  and fresh context requests retain the exact context-free payload. At
  construction it validates and snapshots the complete workspace system and
  per-agent configuration once, passes that complete immutable snapshot to the
  Codex adapter, validates explicit Antigravity model/effort inputs, and
  projects the validated Antigravity system and roster-complete per-agent
  values into provider-private primitives for adapter-owned immutable copying.
  It preserves compatibility delegates for previously test-observed private
  Codex helpers.
  The Antigravity settings are additive keyword-only constructor inputs.
  Configuration values remain fixed for the backend lifetime and do not change
  provider-thread identity, bindings, fingerprints, manifests, or request
  payloads. Each backend retains one I/O-free workspace-bound
  debug-log handle but begins emitting only after the existing complete
  configuration snapshot loads successfully. It best-effort records only fixed
  configuration, binding, provider-invocation, session-lifecycle, and cleanup
  facts or safe failures. Lifecycle diagnostics cover complete enumeration and
  the create, reopen, and pause boundaries, including acquisition, rejection,
  durable mutation, release, and final lifecycle reads without changing their
  established cleanup or exception order. Backend-private trace and error
  guards catch diagnostic `BaseException` failures. Diagnostic calls never
  persist provider stderr, payloads, replies, configuration values, or
  access-claim owner/token facts and never replace the established result,
  exception, or cancellation flow; `debug_log` retains ownership of its files,
  locks, schema, and rotation.
- **Primary verification/documentation:** `tests/test_codex_backend.py`,
  `tests/test_antigravity_adapter.py`, `tests/test_antigravity_parity.py`,
  `tests/test_session_lifecycle.py`, `docs/reference/backend-and-sessions.md`,
  `docs/guides/antigravity.md`,
  `docs/reference/conversation-retrieval.md`,
  `docs/design/prompts-and-provider.md`, `docs/design/session-lifecycle.md`, and
  `docs/operations/privacy-and-provider.md`.

### `story_writing_agents.antigravity_adapter`

- **Source:** `src/story_writing_agents/antigravity_adapter.py`
- **Responsibility:** Adapt immutable agent bindings and structured turns to the
  Antigravity CLI protocol.
- **Supported surface:** No package-supported API. Package-internal
  `AntigravityAdapter` implements the private provider-adapter contract.
- **Direct internal dependencies:** `models`, `provider_adapter`, and
  `provider_process`.
- **State, resources, and side effects:** Owns the Antigravity executable,
  defensive immutable copies of explicit/system/per-agent model and effort
  primitives, bound-agent selection, exact explicit-over-agent-over-system-
  over-provider-default precedence, isolated provider state, command
  construction, headless capability check, NDJSON stream validation, and
  provider conversation-ID protocol. Binding writes deterministic rules beneath
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
  Invocation serializes its output schema, payload, and NDJSON stdin event
  before creating any new per-session runtime tree; encoding failure therefore
  creates no new provider or workspace directory. After serialization, it
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
- **Direct internal dependencies:** `agents`, `config`, `errors`, `models`,
  `provider_adapter`, and `provider_process`.
- **State, resources, and side effects:** Owns construction-time Codex
  executable/model/reasoning settings, isolated binding directories and their
  generated `AGENTS.md` files, invocation-scoped schema/final-response
  directories, exact Codex argv construction, JSONL event and thread parsing,
  and provider-specific safe process diagnostics. Binding and invocation read
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
  `ProviderBinding`, `ProviderInvocation`, and `ProviderResult` values,
  `ProviderInvocationError`, and `ProviderAdapter` protocol are consumed by the
  coordinator and concrete adapters.
- **Direct internal dependencies:** `models`.
- **State, resources, and side effects:** Owns no mutable state or external
  resource; importing and constructing its values perform no I/O.
- **Primary verification/documentation:** `tests/test_codex_backend.py` and
  `docs/design/overview.md`.

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
  streams and environment. The service waits and reaps its exact child,
  performs bounded terminate/kill/reap cleanup after Python-side failure or
  interruption, and runs a fresh status probe after each normally completed
  login; only that probe's exact authenticated state reports success. It owns
  no credential, token, account, URL, browser, keyring, configuration,
  workspace persistence, provider turn, or import-time resource.
- **Primary verification/documentation:** `tests/test_provider_auth.py` verifies
  the native authentication contract, and `tests/test_public_api_and_cli.py`
  verifies the unchanged package facade.
  `docs/operations/privacy-and-provider.md` documents the native authentication
  process and credential boundary.

### `story_writing_agents.cli`

- **Source:** `src/story_writing_agents/cli.py`
- **Responsibility:** Parse command-line arguments and dispatch CLI commands.
- **Supported surface:** Script entry point `story_writing_agents.cli:main` for
  both `story` and `story-writing-agents`. `build_parser` and other helpers are
  not package-supported APIs. Interactive `chat` and noninteractive `run`
  accept additive `--antigravity-executable` (default `agy`),
  `--antigravity-model`, and `--antigravity-effort` options and pass their
  values to `AgentBackend`. The lifecycle grammar is exactly
  `story session create SESSION`, `story session reopen [SESSION]`, and
  `story session pause [SESSION]`; those actions have no lifecycle `--session`
  option. Omitted reopen/pause targets use `default_session_id`; create and
  reopen save that default only after their ready/access transition succeeds,
  while pause retains it. Lifecycle success is printed after access release as
  sorted, indented JSON containing only `access_state`, `durable_state`, and
  `session_id`.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`, `config`,
  `debug_log`, `errors`, `interactive_auth`, `interactive_controller`,
  `interactive_settings`, `interactive_terminal`, `models`, `orchestrator`,
  `provider_auth`, `run_inputs`, `session_lifecycle`, and `workspace`; imports
  `smoke` lazily for the diagnostic command.
- **State, resources, and side effects:** Reads terminal input and delegates
  caller-selected draft-file reads to `run_inputs`; writes JSON, plain-text
  interaction, or errors to standard streams; creates backends; constructs one
  unified stream-backed terminal through its package-internal factory after a
  stdin/stdout TTY guard; constructs the interactive controller; and runs the
  async event loop. The factory binds main and composition previews plus
  semantic confirmation input explicitly through one terminal driver, which
  retains the existing `input` callback as its unsupported-terminal fallback;
  subject input remains an ordinary read, while composition and confirmation
  use history-free semantic input. Interactive startup validates TTY and all
  arguments before workspace discovery, then constructs one workspace-root-
  bound `ProviderAuthService` and `InteractiveAuthCoordinator`. It sequences
  the welcome, an initial hard gate until at least one provider is exactly
  authenticated, missing-default selection and persistence, immutable
  configuration loading, effective-route resolution in CLI-override, explicit
  agent, workspace-default, then Codex order, and a fresh hard gate for every
  distinct effective provider. It treats cancellation, interrupt, and EOF as
  clean pre-backend exits and delays CLI-context loading, backend and controller
  construction, initial session attachment, and lobby work until both gates
  pass. It injects the same borrowed coordinator into the controller for later
  `/auth` and authenticated settings work. Native subprocess, terminal, and
  configuration-persistence ownership remain with `provider_auth`,
  `interactive_terminal`, and `interactive_settings` respectively. The
  controller owns interactive backend cleanup after its session-lobby run
  begins, while the CLI closes a backend if construction fails before that
  ownership transfer. After workspace
  initialization or discovery succeeds, the CLI independently marks later
  failure-hint eligibility and attempts optional workspace-bound debug-log
  handle construction through one `BaseException`-guarded helper. Interactive,
  `init`, explicit-smoke, and ordinary resolved-workspace entry all use that
  helper; module-private trace and error helpers accept an absent handle and
  guard every submission. For explicit smoke, the CLI initializes and retains
  one identity-bearing `Workspace` and passes that exact instance to
  `smoke.run_smoke` without resolving the path again. Initialization,
  discovery, argparse, and other pre-workspace failures, plus implicit smoke,
  remain hint-free. A later caught eligible noninteractive `Exception` appends
  the fixed workspace-relative hint even when no handle, record, or file is
  available, while returned domain outcomes remain JSON-only. Interactive
  startup failures after workspace discovery add the corresponding fixed
  terminal notice independently of handle availability. Existing sanitized
  error text and exit behavior are otherwise unchanged, and `debug_log`
  retains storage ownership.
- **Primary verification/documentation:** `tests/test_public_api_and_cli.py`,
  `tests/test_cli_context.py`, `tests/test_smoke_cli.py`, and
  `tests/test_interactive_pty.py` for primary process-level POSIX PTY
  verification; `docs/reference/cli.md`, `docs/getting-started.md`, and
  `docs/guides/antigravity.md`.

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
  `ConfigurationSnapshot`, and exact `KNOWN_AGENT_BACKENDS` value
  `("codex", "antigravity")`; legacy `gemini` values receive fixed migration
  guidance. `SystemDefaults` retains its established positional fields and
  trailing optional Codex model/reasoning plus Antigravity model/effort fields.
  It adds the trailing optional nullable workspace `default_backend` field.
  `AgentConfig` retains its seven-field order while allowing its leading
  backend route to be null. `ConfigManager` owns both complete persisted schemas
  plus provider-specific safe-model and fixed-effort validators. Missing legacy
  default and agent backend keys are interpreted as null without rewriting
  their documents. Package-internal
  `validate_configuration` validates both persisted documents as one complete
  candidate, `load_configuration_snapshot` reads and publishes one immutable
  pair, and `resolve_from_snapshot` applies live runtime values while resolving
  providers in exact CLI override, explicit agent route, workspace default,
  then Codex order. Existing on-demand loaders and `resolve` retain their
  signatures and delegate to the same validation and resolution rules.
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
  value is accepted.
- **Direct internal dependencies:** `agents` and `workspace`.
- **State, resources, and side effects:** Import and construction perform no
  filesystem I/O or application startup. Calls own schema-v1 UTC timestamps,
  compact ASCII-safe JSONL serialization, qualified exception-type projection,
  and at most the nearest 32 module/function/positive-line frame facts; they
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
  facade.
- **Direct internal dependencies:** `interactive_terminal` and `provider_auth`.
- **State, resources, and side effects:** The coordinator retains borrowed
  authentication and terminal collaborators plus a resource-free picker and
  startup-presentation adapter. It projects stable Codex and Antigravity rows
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
  command and route metadata without parsing. Agent-valued slash completion
  positions project exactly each route's preferred prompt alias in roster
  order; leading routed-message completion derives `@`-prefixed candidates
  from those same preferred aliases, while all ordered aliases remain valid
  parser inputs and fuzzy agent suggestions present the preferred alias. Other
  parser helpers and command-alias maps are internal; agent parsing and
  canonical validation derive from the route registry. Search and transcript
  actions extend the
  frozen, slotted `InteractiveAction` with the optional positive
  `start_sequence` field;
  existing action payloads remain compatible. `/search QUERY` and
  `/transcript ID [COUNT] [--from SEQUENCE]` participate in the same immutable
  help registry and exact parser. Global no-argument `/auth` and `/settings`
  are represented by the additive `AUTH` and `SETTINGS` actions and the same
  help/completion grammar. Their registry metadata truthfully allows native
  provider calls and artifact writes: authentication may persist provider-owned
  native auth state, while settings may additionally persist workspace
  configuration.
  Story-session parsing recognizes exactly
  `/session create NAME`, `/session resume [NAME]`, `/session pause`, and
  `/session list`, with no old lifecycle verbs or aliases. Conversation
  selection recognizes exactly `/conversation resume [ID]`: omission produces
  a `CONVERSATION_USE` action without an argument for controller-owned live
  selection, while an included identity remains the action argument. Existing
  public `ActionKind` member names and values remain unchanged, with `AUTH`
  added as the exact `"auth"` value. The package facade does not re-export the
  route metadata.
- **Direct internal dependencies:** `agents`.
- **State, resources, and side effects:** Immutable command and agent-route
  metadata only. Completion projection is pure and call-local; the module owns
  no editor, palette, history, process state, or external resource and performs
  no provider, filesystem, persistence, environment, or import-time
  operational side effects.
- **Primary verification/documentation:** `tests/test_interactive_commands.py`,
  `tests/test_interactive_terminal_completion.py`,
  `tests/test_public_api_and_cli.py`, and
  `docs/reference/interactive-command-parser.md`, `docs/reference/cli.md`, and
  `docs/guides/story-sessions.md`.

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
  collaborators plus optional keyword-only `ConversationCatalog` and borrowed
  `InteractiveAuthCoordinator` collaborators, and whose single-use
  `run_session_lobby` operation returns process status 0 or 2. An omitted
  catalog uses `FilesystemConversationCatalog`; an omitted authentication
  coordinator preserves direct legacy construction without native auth. The
  shell dispatches
  the exact lifecycle commands `/session create`, `/session resume`,
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
  and consensus waits retain the facade's unlisted
  `InteractiveAdvisoryProgress` and `InteractiveDebateProgress` adapters and
  wrap exactly the runtime awaitables instead of emitting persistent routing
  and waiting notices; the controller owns no indicator glyph, cursor control,
  or animation task. It also retains the facade's resource-free main-input,
  generic-picker, and cancellable-prompt adapters. Omitted
  `/session resume` and `/conversation resume` pass a call-local provider of
  controller-built immutable `Picker` choices and opaque canonical command
  identities through `InteractiveMainInput`; capable input obtains a fresh
  backend lifecycle or active-catalog snapshot for each exact completion
  state, while fallback input bypasses the provider. Dynamic-choice Enter
  submits an explicit identity and bypasses the generic picker. An omitted
  resume buffer that is itself submitted instead starts a fresh lifecycle or
  catalog read. When that read succeeds and yields eligible rows, the controller
  opens the generic picker: it is live and transient on a capable stream and
  numbered in line fallback, and its selected identity is freshly validated.
  An empty result or read failure emits its applicable result or error without
  opening the picker. The submitted omitted main line's history eligibility is
  independent of that post-submit outcome. Settings continues to consume picker
  and prompt adapters for its call-local edit flow. When the optional
  coordinator is supplied, global `/auth`, provider-status choices,
  authenticated route assignment, missing-default coordination, and a fresh
  effective-route authentication gate before settings Save use that borrowed
  collaborator. The controller owns no native authentication child, cursor
  sequence, or terminal geometry.
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
  roster agent, enables Codex or Antigravity selection, and edits only the
  active provider's model and effort vocabulary without discarding the inactive
  provider's values. The summary projection names both providers' model/effort
  rows, so changed-only confirmations can identify changes to either without
  changing the active backend or runtime snapshot. Editors contain no repeated
  Current/Pending context panel. Explicit Save and Cancel actions each use one
  selectable YES/NO confirmation whose preceding context contains only changed
  Current/Pending properties in stable settings and roster order; each changed
  value has an explicit text cue and warning role, and an unchanged candidate
  contains only an explicit no-changes notice without empty sections. NO or
  nested Escape returns to the settings menu, while interrupt or EOF preserves
  whole-flow cancellation.
- **Direct internal dependencies:** `agents`, `backend`, `cli_context`,
  `conversation_retrieval`, `conversation_search`, `conversations`, `debug_log`,
  `errors`, `interactive_auth`, `interactive_commands`,
  `interactive_controller._conversation_policy`,
  `interactive_controller._runtime_policy`,
  `interactive_controller._session_policy`, `interactive_settings`,
  `interactive_terminal`, `models`, `orchestrator`, `run_inputs`,
  `session_lifecycle`, and `workspace`.
- **State, resources, and side effects:** Instances retain borrowed workspace,
  CLI-context, backend, terminal, and optional authentication-coordinator
  collaborators plus either a borrowed injected catalog or a controller-
  constructed default filesystem catalog. It
  constructs and retains one `StoryRuntime` and one
  `FilesystemConversationEventStore` bound to the resolved catalog. It shares
  that exact event-store instance with the runtime and the attachment-scoped
  `ConversationMemoryService`. The memory service is absent in lobby state,
  constructed for a validated destination before attachment commit, rebound on
  a successful switch, and cleared on pause, cleanup, and final close. The
  controller also owns its current attached-session identifier, current-focus
  conversation metadata, sticky target, current CLI-context snapshot,
  process-local per-session latest accepted STARTED debate-ID map, and
  one-run/closed state. It also retains a controller-private count of
  successfully rendered typed errors. Each submitted command snapshots that
  count before dispatch and independently records `failed`/`rejected` when a
  normally returned path rendered a new typed error, or
  `succeeded`/`completed` when it rendered none; an earlier command cannot
  reclassify a later one. It retains one workspace-bound debug-log handle,
  best-effort records fixed lobby, command, attachment, focus, cleanup, and
  safe failure facts, and routes every post-workspace typed error through the
  fixed debug-log-path notice without retaining or resolving that notice.
  Controller-private trace and error guards catch diagnostic `BaseException`
  failures. `debug_log` remains the resource owner, and diagnostic failure does
  not alter controller results, exceptions, cancellation, or cleanup
  sequencing. Once its single-use run starts, it constructs and owns
  one private `asyncio.Runner` for the rest of that lifecycle. Repeated
  `Runner.run` calls execute every accepted advisory and consensus coroutine on
  that Runner; controller-local entry/cancellation/join evidence and public
  Runner task inspection distinguish a quiescent pre-entry interruption, a
  completed entered cancellation, and an unjoined or otherwise uncertain
  operation. Final cleanup attempts each applicable stage once in order:
  Runner close and its residual asynchronous work, known process-access
  release, backend close, then controller-state clearing. An uncaught body
  process-control failure wins; otherwise the first cleanup process-control
  failure wins, with every later stage still attempted before the exact object
  and its originating traceback chain propagate. The Runner reference is
  retained only when Runner close itself is interrupted by process control;
  ordinary Runner-close failure clears it, while attachment, focus, and memory
  always clear and the controller closes. With no retained process control,
  ordinary cleanup failures force status 2 and one best-effort nonduplicated
  `cleanup_failed` diagnostic. A diagnostic process control participates only
  when no earlier winner exists and cannot replace a retained body or cleanup
  control.
  Authentication actions request full status refresh and native login only
  through `interactive_auth`, coordinate its default-selection policy, and
  delegate exact-byte default persistence and open-settings rebase to
  `interactive_settings`. Settings assignment requires a fresh exact
  authenticated status for the selected provider, and Save freshly gates every
  distinct effective agent route with fixed unauthenticated, unavailable, or
  indeterminate remediation. Auth/default effects completed during an open
  settings flow remain durable independently of its pending candidate; this
  module owns only interaction state and sequencing. `provider_auth` retains
  subprocess ownership, while `interactive_settings`, `config`, and
  `Workspace` retain persistence and route-resolution ownership.
  `AgentBackend` retains the actual access claim, manifest, and provider-thread
  coordination, while provider adapters retain binding, staging, and provider-
  native state and `provider_process` owns subprocess resources. The settings flow
  retains only one call-local immutable baseline and pending candidate,
  constructs its changed-only
  confirmation context, and delegates exact-byte conflict detection and guarded
  persistence to `interactive_settings`; cancellation or failed saves do not
  mutate the active backend or runtime snapshot. Through public collaborators
  the controller reads and parses terminal input, submits typed presentation
  facts, invokes terminal-owned semantic writers, and sequences nested
  conversation transitions. A session destination proceeds through
  configuration validation, lifecycle pre-read, create/acquire or durable
  reopen, correlated post-read, destination focus load, destination-default
  persistence, source release when switching, and only then in-memory
  attachment/focus commit. Later failure releases newly
  acquired destination access where possible but does not roll back a
  successful paused-to-ready reopen. A reported default-save failure can follow
  `os.replace`, so the persisted default is not guaranteed to retain its old
  value; source-release failure after destination-default persistence leaves
  the new default and aborts through `cleanup_failed`. Pause retains defaults,
  changes durable state, releases access, and returns to the lobby. Quit, EOF,
  and final cleanup release access without pausing. Persisted conversation
  defaults remain distinct from the in-memory current focus.
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
  `docs/guides/story-sessions.md`, `docs/reference/cli.md`,
  `docs/reference/interactive-terminal.md`, and
  `docs/reference/conversation-retrieval.md` verify the current collaborator,
  lifecycle, selection, and retrieval-presentation contracts.

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
  history-free contextual read without retaining it. On a capable stream, the
  public main-line route clears its complete live frame before `read_line`
  returns; ordinary-line fallback retains its printed prompt and input. The
  unified driver preserves exact fallback, selectable-palette, editing,
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
  `SettingsBaseline`, and `SettingsState`; fixed `SettingsErrorKind` and
  redacted `SettingsError`; the exact ordered `SETTINGS_LANGUAGES`,
  `SETTINGS_REASONING_EFFORTS`, and `SETTINGS_ANTIGRAVITY_EFFORTS`;
  `load_settings_state`, `update_language`, `update_persona`,
  `update_max_debate_rounds`, `update_agent_backend`,
  `establish_default_backend`, `establish_settings_default_backend`,
  `update_agent_codex_model`, `update_agent_codex_reasoning_effort`,
  `update_agent_antigravity_model`, `update_agent_antigravity_effort`, and
  `save_settings_state`. The module has no package-facade export.
- **Direct internal dependencies:** `agents`, `config`, `errors`, and
  `workspace`.
- **State, resources, and side effects:** Loading captures the exact strict-
  UTF-8 bytes of both persisted configuration documents, one fully validated
  immutable configuration snapshot, and a safe lexical persona catalog with
  top-level-template precedence. Pure update operations replace only the
  pending immutable candidate, including one agent's provider or one
  provider-specific model/effort value at a time while preserving every
  inactive-provider value. System-level provider model/effort values remain
  unchanged fallback data rather than editable settings.
  `establish_default_backend` persists only a validated missing workspace
  default and never overwrites an existing value. Its settings-specific sibling
  conflict-checks the opening exact bytes and persona catalog, writes one
  default-only canonical system document, rereads and verifies that exact new
  baseline, and replays every pending edit onto it; authentication and default
  choice remain caller-owned. These completed default writes are durable
  independently of whether the open pending transaction later saves or
  cancels. Saving completely
  validates and fully serializes that candidate,
  rereads both documents and the catalog for exact stale-state detection, and
  performs no write when the candidate is unchanged. Changed documents are
  serialized canonically and atomically replaced through `Workspace` in system-
  then-agent order; any attempted targets are restored from exact baseline text
  in reverse order after a write failure, all restorations are attempted, and a
  restoration failure produces the fixed rollback category. The module owns
  configuration persistence and rebase but no authentication facts, terminal,
  controller, backend, runtime, provider, network, subprocess, or import-time
  operational resource.
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
  unlisted `InteractiveSemanticOutput` binds one terminal's semantic write
  capability for controller use, and the existing `Field`, `ListView`,
  `Message`, `Notice`, `Panel`, `Record`, `Section`, `SemanticRole`, and
  `ViewState` contracts are likewise facade-bound but omitted from `__all__`.
  These bindings support count-aware dense semantic views without changing the
  package-supported surface. The adapter and contracts add no project-root
  export or raw-resource ownership. Unlisted package-internal
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
  and confirmation prompts, while internal construction binds distinct
  ordinary, history-aware main, and history-free semantic composition and
  confirmation readers without prompt inspection. Unlisted
  `InteractivePickerInput`, `Choice`, `Picker`, `Prompt`, `ControlOutcome`, and
  `ControlOutcomeKind` bindings provide one resource-free generic picker input
  boundary without exposing opaque choice identities, terminal leaves, or a
  new package-supported API. Unlisted `InteractivePromptInput` binds one
  resource-free semantic cancellable-line capability for nested settings input;
  callback and stream factories respectively provide deterministic plain and
  sole-driver-backed implementations without changing the exact public
  `__all__`. The installed main, composition, confirmation, picker, and
  cancellable-prompt routes enforce one submitted-input lifecycle: each
  complete capable live frame clears before return and cleanup is attempted on
  failure, while callback and line fallback retain printed prompts and input
  and emit no synthetic `completed` or `cancelled` control frame. The picker
  and prompt adapters validate their retained `transient` arguments but cannot
  opt out of that lifecycle. These adapters do not expand the exact public
  `__all__` or `InteractiveTerminal` method surface.
  Unlisted `InteractiveAdvisoryProgress`, `InteractiveDebateProgress`, and
  `AdvisoryProgressError` bind advisory and consensus async progress lifecycles
  through the same facade: callback and incapable terminals await silently,
  while stream construction injects the pure fixed-glyph renderer into the sole
  driver. They do not expand the public `__all__` or expose terminal leaves to
  the controller.
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
  correlated completion projection into the existing semantic palette;
  `project_main_choice_completion` and `project_main_choice_palette` adapt
  controller-owned semantic choices into that same immutable state while
  keeping committed identities opaque, while `main_command_uses_choices`
  reports whether the correlated state owns that dynamic choice cycle.
  `transition_main_command_input` owns mutually exclusive completion and choice
  cycles, palette open/filter/highlight/clamp, static Tab/Enter completion and
  snapshot, dynamic Up/Down highlight and direct Enter commit, Escape
  restoration and latch, and the exact open-palette versus closed-history arrow
  routing.
  `transition_composer_line`
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
  Installed stream-terminal main, composition, confirmation, picker, and
  cancellable-prompt reads clear the complete capable live frame before
  returning from submit, commit, cancel, interrupt, or EOF and attempt the same
  cleanup on failure before restoration. An eligible capable main submission
  enters history only after that cleanup and restoration; an eligible fallback
  submission enters history after the ordinary reader returns. Static
  completion-only Enter keeps the live read open, performs no submitted-frame
  cleanup, and appends nothing.
  The lower-level semantic-input, picker, and prompt methods validate their
  per-read transient flags, while the installed facade adapters always request
  this lifecycle. Line fallback cannot erase printed prompts or input and emits
  no synthetic `completed` or `cancelled` control frame. Supported
  package-internal `run_advisory_progress` accepts one awaitable and injected
  fixed-glyph renderer, awaits silently without raw cursor capability, and on a
  capable stream joins the operation with one indicator task. Internal
  `AdvisoryProgressError` distinguishes indicator failure from provider failure
  for controller cleanup. Purpose-bound `run_startup_welcome`,
  `run_startup_notice`, and `run_startup_result_notice` accept injected pure
  renderers and timing, progressively display the fixed four-frame welcome or
  one transient authentication notice, and establish a fresh post-provider
  output boundary before results without introducing a general animation API.
- **Direct internal dependencies:** `interactive_terminal._model`.
- **State, resources, and side effects:** Sole owner of borrowed input/output
  streams, independent capability checks, detected width, normalized physical
  keys, raw POSIX attributes, functional cursor controls, live redraw writes,
  flushes, and exact restoration. It retains exactly one bounded process-local
  main-input history store per driver instance; each main read resets only its
  call-local navigation state. An eligible fallback commit is appended once
  after the ordinary reader returns, while an eligible live commit is appended
  once only after complete-frame clearing and exact restoration. Static
  completion-only Enter and outcomes without an accepted line append nothing.
  The same read loop owns
  call-local one-byte
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
  It creates no OS thread or import-time task. A capable startup presentation
  requires known ANSI cursor support, a real POSIX terminal, no
  `STORY_REDUCED_MOTION`, and atomic `TCSAFLUSH` input discard. The driver uses
  call-local noncanonical/no-echo attributes, discards only type-ahead received
  during the guarded delay, and exactly restores the original terminal before
  native login or subsequent input. It reobserves width for every startup
  frame, reserves the right-margin column, retains write-time physical-row
  geometry, resets SGR during cleanup, and preserves the primary error while
  avoiding speculative cursor erasure after unknown write progress. Welcome
  frames change at 0.4-second intervals and notices are bounded to 0.8 seconds.
  `NO_COLOR`, reduced motion, callbacks, missing or uncertain capability,
  non-TTY, non-POSIX, `TERM=dumb`, or unavailable safe input discard instead
  emits one meaningful plain LF-delimited static rendering with no delay, ANSI,
  cursor animation, or flashing. Construction and import remain I/O-free.
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
  callback-width semantics remain compatible. `write_error` alone adds the
  optional keyword-only `debug_log_path` presentation seam: `None` preserves
  the established error record exactly, and supplied validated text becomes
  one sanitized third notice labelled `debug log`. Its package-internal stream
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
  contexts into terminal completion models. Its package-internal main-choice
  adapter validates a call-local provider, caches only the active controller-
  supplied immutable `Picker` for rendering correlation, and gives dynamic choices
  precedence over static completion for that exact control state; fallback
  never invokes the provider. Its unlisted facade adapters supply the pure
  selectable control projection plus existing semantic palette renderer to the
  driver.
  The internal contextual factory preserves explicit ordinary/main/composition
  callback routing through deterministic plain semantic adapters, while the
  real stream factory binds the selectable main-command route, both preview
  providers, semantic composition and confirmation input, plus the pure
  main-input transitions to one driver. Resource-free `InteractiveMainInput`
  preserves the public main prompt and `TerminalAction` contracts while passing
  one call-local choice provider only to capable stream input. Resource-free
  `InteractivePickerInput`
  validates and initializes one generic picker, then delegates only its
  immutable control state; callback construction binds deterministic plain
  complete-list picker input, and stream construction binds the same adapter to
  the sole driver's dedicated picker lifecycle with pure control and rendering
  collaborators. Resource-free `InteractivePromptInput` constructs one existing
  `InputPromptView` and returns typed commit, nested-cancel, interrupt, or EOF;
  callback and stream construction bind it to plain or driver-owned input
  without adding a component kind or raw resource owner. The picker and prompt
  adapters validate a retained transient flag but always select the universal
  submitted-input lifecycle. The real stream factory binds main, composition,
  confirmation, picker, and cancellable-prompt reads to that lifecycle: the
  driver clears the complete capable live frame before return and attempts the
  same cleanup on failure, while callback and line fallback retain printed
  prompts and input and emit no synthetic `completed` or `cancelled` control
  frame. Conversation-subject input remains ordinary. Persistent semantic
  records are unchanged. Resource-free `InteractiveAdvisoryProgress` and
  `InteractiveDebateProgress` bind the terminal's advisory and consensus
  awaitable wrappers: callback construction remains visually silent, and stream
  construction injects the pure fixed-glyph renderer into the sole driver
  without exposing capabilities or streams. Resource-free
  `InteractiveStartupPresentation` similarly binds a fixed semantic four-stage
  welcome plus authentication guidance and result notices; callback
  construction writes one plain static record and stream construction delegates
  guarded timing, terminal reclaim, and cleanup to the sole driver. The
  supported public terminal class and facade exports remain unchanged.
- **Direct internal dependencies:** `agents`, `interactive_commands`,
  `interactive_terminal._controls`, `interactive_terminal._driver`,
  `interactive_terminal._model`, and `interactive_terminal._render`.
- **State, resources, and side effects:** Callback-constructed instances retain
  borrowed synchronous read/write callbacks, deterministic plain capabilities,
  and an explicit width but inspect no stream and acquire no resource.
  Stream-factory instances delegate all raw terminal state, width detection,
  keys, writes, flushes, restoration, readiness, incremental decoding, and the
  bounded process-local main-input history store, plus advisory indicator task
  and cleanup to one driver. Main completion, call-local choice, palette, and
  composition providers plus semantic
  input renderers are selected per call and never retained by that driver;
  composition, confirmation, cancellable prompt, and picker input are history-
  free, while subject input remains ordinary. Callback-constructed terminals
  and the core itself own no history.
  Startup presentation retains only terminal-owned callbacks and one injectable
  sleeper; its semantic frame tuple is immutable and all capability, stream,
  raw-mode, input-discard, delay, geometry, and cleanup resources remain
  driver-owned.
  The semantic adapter retains only the
  terminal's bound component capability and acquires no stream or raw resource.
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
- **Supported surface:** Package-supported `StoryRuntime` and `RuntimeSession`.
  `StoryRuntime` exposes provider-free directive application, runtime-override
  saving, and session/debate/context inspection; `RuntimeSession` supplies their
  bound-session equivalents. Both forms retain unchanged one-step `converse()`,
  additive bounded `converse_with_retrieval()` with required scoped memory
  service and optional synchronous progress sink, context-aware `run_prompt()`
  and `run_draft()`, and strict `inspect_debate_context()`, plus compatibility
  aliases `Runtime` and `Session`.
- **Direct internal dependencies:** `agents`, `backend`, `config`,
  `conversation_retrieval`, `conversation_search`, `debug_log`, `directives`,
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
  context-free. Each runtime retains one I/O-free workspace-bound debug-log
  handle, but runtime construction emits no diagnostic record. Later operations
  best-effort record fixed configuration, directive, inspection, advisory,
  retrieval, consensus, and safe failure facts through runtime-private trace
  and error guards that catch diagnostic `BaseException` failures. Diagnostic
  calls preserve the established result, exception, cancellation, and
  persistence behavior, while `debug_log` retains storage ownership. For
  synchronous `apply_directive`, `set_runtime_overrides`, `inspect_session`,
  `inspect_debate`, and `inspect_debate_context`, an underlying
  `KeyboardInterrupt` or `SystemExit` attempts exactly one
  `cancelled`/`interrupted` record through that safe guard before a bare
  re-raise preserves the identical object and originating traceback. Ordinary
  failures retain `failed`/`failed`; successful directive/configuration calls
  retain `succeeded`/`completed`, and successful inspections retain
  `observed`/`completed`.
  It synchronously emits typed provider-neutral debate progress in lifecycle
  order, makes result and terminal state durable before terminal debate
  progress, preserves typed observer failures, and retains external
  cancellation while best-effort recording interruption. It also sequences
  ordinary debate persistence and the one-step advisory context-read,
  user-append, provider-call, and reply/error-append flow. After the user event
  is durable, a provider-phase `KeyboardInterrupt` or `SystemExit` in either
  one-step advisory or bounded retrieval attempts a `cancelled`/`interrupted`
  runtime terminal diagnostic, appends no transcript outcome, performs no
  automatic retry, and re-raises the identical exception. The unmatched user
  transcript event remains, and consensus execution is unchanged.
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
  `tests/test_conversation_retrieval.py`, `tests/test_antigravity_parity.py`,
  `tests/test_interactive_consensus.py`, `tests/test_interactive_conversation.py`
  for retained runtime use through the controller-owned persistent Runner and
  its single lifecycle event loop,
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
- **Supported surface:** Internal
  `run_smoke(workspace: Workspace | None = None)`; no package-supported API.
  `None` selects the implicit temporary lifecycle, while a supplied value must
  be an initialized `Workspace`. There is no string-or-path compatibility form.
- **Direct internal dependencies:** `agents`, `conversation_retrieval`,
  `conversation_search`, `conversations`, `debug_log`, `models`,
  `orchestrator`, `session_lifecycle`, and `workspace`.
- **State, resources, and side effects:** Runs a unique temporary or explicitly
  retained workspace diagnostic with a session directive, two
  conversation metadata and transcript sets, bounded search, transcript-read,
  and retrieval activity, trusted in-memory nonpersistent retrieval progress,
  an immutable included-context sidecar, debate and agent state, a result, and
  a manuscript; verifies core-document bytes. Its provider-free backend
  implements the exact lifecycle hooks and returns correlated ready/access
  facts so runtime preparation exercises the canonical contract. Implicit mode
  owns, initializes, and removes one temporary `Workspace` and its bounded debug
  log after return or propagation. Explicit mode borrows the caller's exact
  initialized identity-bearing `Workspace` and leaves it retained. Every fresh
  workspace-bound runtime, catalog, event store, memory service, and inspection
  consumer borrows that exact instance rather than reconstructing or rebinding
  one from its path; the provider-free backend remains a separate fresh service
  without workspace ownership. Logger-handle acquisition is optional and
  guarded independently from the module-private trace and error submissions;
  diagnostic `BaseException` failures cannot replace a smoke report or
  propagated application failure. Every diagnostic handle therefore retains
  the original Workspace identity and either appends there or fails closed
  instead of redirecting into a replacement root. Ordinary smoke artifact I/O
  deliberately retains the established pathname-based, nontransactional
  `Workspace` behavior and may observe a real-directory replacement. `debug_log`
  retains ownership of the diagnostic schema and storage.
- **Primary verification/documentation:** `tests/test_smoke_cli.py`,
  `docs/guides/smoke-test.md`, and `docs/reference/cli.md`.

### `story_writing_agents.workspace`

- **Source:** `src/story_writing_agents/workspace.py`
- **Responsibility:** Provide the secure workspace filesystem and persistence
  boundary.
- **Supported surface:** Package-supported `FilesystemConversationCatalog`,
  `FilesystemConversationEventStore`, `Workspace`, and
  `initialize_workspace`. Package-internal `open_workspace_root_directory` and
  `verify_workspace_root_directory` provide the diagnostic storage boundary and
  are deliberately absent from the project-root facade.
  `Workspace.inspect_debate_context` is the strict, separate context-sidecar
  inspection operation; topology, serialization, and lock-registry details are
  internal.
- **Direct internal dependencies:** `agents`, `conversations`, `errors`, and
  `models`.
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
  ordinary `OSError`. Retained identity protects internal diagnostic storage,
  not ordinary domain operations: public path I/O remains pathname-based and
  nontransactional and may observe a real directory installed later at the same
  root path. The module also owns strict UTF-8 and
  JSON I/O, overrides, agent and debate state, logs, results, per-conversation
  metadata under
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
  `agent_states/orchestrator_state/conversation_defaults` and `debug_logs`;
  existing initialized workspaces need neither directory until the corresponding
  owning feature first creates it lazily. `debug_log`, not `Workspace`, owns the
  files, lock, schema, append, and rotation inside the diagnostic directory. New
  workspaces also receive the canonical eight-key system-default document with
  null Codex model/reasoning, Antigravity model/effort, and workspace
  `default_backend` values, plus canonical seven-key per-agent entries with a
  null backend, language, persona, and both providers' model/effort values.
  Existing configuration bytes are never migrated or rewritten by
  initialization. `cli_context` owns
  the schema stored
  in the conversation-default directory. The backend-owned story-session
  process-access claim remains the cross-process same-session write guard.
  Catalog and absent-transcript reads do not create storage. Transcript access
  validates catalog identity, and appends atomically replace the complete file.
  `FilesystemConversationEventStore.read_context` retains its event lock across
  catalog validation and one authoritative transcript read, then delegates the
  pure fixed-policy selection to `conversations`.
- **Primary verification/documentation:** `tests/test_workspace_and_config.py`,
  `tests/test_debug_log.py`, `tests/test_agent_state.py`,
  `tests/test_lifecycle_atomicity.py`,
  `tests/test_public_api_and_cli.py`, `tests/test_conversation_catalog.py`,
  `tests/conversation_event_store_contract.py`,
  `tests/test_conversation_event_store.py`,
  `docs/reference/workspace-api.md`,
  `docs/reference/configuration/session-storage.md`,
  `docs/reference/conversation-catalog.md`,
  `docs/reference/conversation-transcripts.md`, `docs/design/persistence.md`,
  and `docs/operations/filesystem-safety.md`.

## Consistency Notes

- `Workspace` remains the sole containment and no-symlink authority, owns each
  instance's immutable canonical-root identity and package-internal root
  open/reverification policy, and declares the `debug_logs` scaffold.
  `debug_log` depends one way on that non-facade seam and owns each attempt's
  root and child descriptors, safe event schema, weak per-workspace process
  locks, independent advisory guard, leaf mutation, regular-file and single-link
  validation for guard/active/backup files, active/backup size validation,
  expected rotation state, append rollback, bounded rotation, and cleanup. This
  independent guard intentionally does not reuse the backend's private
  access-claim lock: access claims fail closed, while ordinary diagnostic
  `Exception` failures drop their record.
  Process-control `BaseException` failures deliberately leave the low-level
  surface, and `backend`, `cli`, `interactive_controller._core`, `orchestrator`,
  and `smoke` each guard every trace and error call privately so diagnostics
  never affect caller control flow. The five synchronous provider-free runtime
  operations also attempt `cancelled`/`interrupted` records for application
  `KeyboardInterrupt` or `SystemExit` before bare-reraising the identical
  control. Explicit CLI smoke initializes and retains one identity-bearing
  `Workspace`; smoke and every fresh workspace-bound service borrow that same
  instance, so none can reacquire a replacement identity. Implicit smoke instead
  owns its temporary workspace lifecycle. This diagnostic identity continuity
  does not change ordinary pathname-based, nontransactional Workspace domain
  I/O. CLI and smoke guard diagnostic-handle acquisition; CLI separately marks
  hint eligibility after successful initialization or discovery, so an eligible
  hint does not imply that a handle, record, or file exists. The diagnostics
  module is not re-exported by the project facade, and its import and handle
  construction perform no filesystem operation. A missing-root Workspace whose
  one post-creation identity bind fails never retries and never writes child
  scaffold/defaults on that instance, while an initially unavailable existing
  root retains its distinct established behavior. Backend emission begins only
  after its existing configuration snapshot validates, while runtime handle
  construction emits no record. All five consumers use only the internal append
  surface; none assumes ownership of diagnostic files, locks, schema, or
  rotation.
- `Workspace` initializes the canonical eight-key system and seven-key
  per-agent configuration shapes with nullable workspace and agent provider
  routes but never rewrites existing documents. `ConfigManager` owns full
  system and per-agent schema validation, the exact Codex/Antigravity backend
  vocabulary with legacy Gemini guidance, nullable default and explicit route
  handling, both providers' model/effort validators, and immutable complete
  snapshots. Each `AgentBackend` validates
  and snapshots those values once at construction, passes the complete snapshot
  to the Codex adapter, and projects only the Antigravity values into provider-
  specific primitives. Codex model resolution remains explicit-over-agent-
  over-system and its reasoning remains agent-over-system;
  Antigravity model and effort resolution is explicit-over-agent-over-system-
  over-provider-default and is selected by the bound agent. Provider routing is
  CLI override, explicit agent route, workspace default, then Codex. Provider
  model and effort values do not enter bindings, fingerprints, thread keys,
  lifecycle artifacts, request payloads, or active-backend refresh. A
  backend-bound `StoryRuntime` and its `PromptAssembler` reuse that same
  construction-time snapshot for provider routes, language, persona, and round
  defaults while session and explicit overrides remain live; settings saves
  affect only a later normal lifecycle.
- `interactive_settings` alone owns one call-local exact-byte configuration
  baseline, immutable pending candidate, safe persona catalog, missing-default
  persistence, default-only exact-byte/catalog conflict checking and rebase,
  complete candidate validation, exact stale-state detection, deterministic
  system-then-agent writes, and continued reverse-order exact-text rollback.
  Default choice and authentication facts remain outside it. The controller
  owns focus and semantic interaction only and reaches this unit directly; the
  settings unit has no controller, terminal, backend, runtime, auth, or parser
  edge.
- `provider_auth` is the sole authentication subprocess boundary: it probes
  only the exact native commands at the resolved workspace root, gives login
  the inherited real terminal, reaps its children, and exposes only immutable
  content-free states. `interactive_auth` depends one way on that service and
  the terminal facade to own status presentation, picker/login coordination,
  full refresh, sanitized outcomes, and default selection policy without
  owning either resource. The CLI composes both private modules and sequences
  welcome, initial/effective-provider hard gates, default persistence, then
  delayed context/backend/controller construction; the controller only borrows
  the same coordinator for `/auth` and authenticated settings. Credentials,
  provider output, browser/keyring behavior, terminal mode, and configuration
  files remain respectively provider-native, undisclosed, terminal-owned, and
  settings/config/workspace-owned. Both auth modules remain absent from the
  project facade.
- `interactive_commands` owns canonical command and agent-route metadata, parse
  rules, typed parsed actions, and the pure cursor-aware completion grammar and
  replacement spans derived from that same metadata. Agent-valued completion
  and fuzzy guidance expose only preferred prompt aliases in roster order while
  parsing retains every accepted route alias. `interactive_line_editor`
  preserves its legacy main suggestion projection while delegating actual main
  reads to the shared selectable terminal route; `_controls` owns the fixed
  ordered `/send`, `/cancel` composition projection. The facade delegates all
  raw terminal behavior to the unified terminal driver and creates no registry
  or parser edge. The fixed interactive lifecycle grammar uses
  `/session resume [NAME]` and `/conversation resume [ID]` while preserving the
  existing public `ActionKind` identities and values. The
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
  visually silent. For the separate fixed startup/auth presentation it alone
  owns capability and reduced-motion selection, guarded noncanonical/no-echo
  activation, atomic type-ahead discard, exact restoration before native login,
  bounded timing, fresh-width rendering, right-margin safety, write-progress
  geometry, result line boundaries, SGR reset, and best-effort cleanup. `_core`
  owns only the immutable semantic welcome and notice components plus the
  resource-free startup adapter; static, reduced-motion, callback, dumb, and
  uncertain paths remain meaningful, plain, and delay-free. It receives
  completion, palette, preview, and trusted semantic input collaborators only
  per read,
  resets navigation for each main read, and keeps composition and confirmation
  reads history-free. Cancellable settings prompt input is also history- and
  completion-free: `_controls` owns its pure Escape outcome, `_core` binds the
  resource-free facade adapter, and the sole driver owns its raw/fallback input
  and restoration. The real stream factory binds main, composition,
  confirmation, picker, and cancellable-prompt reads to one submitted-input
  lifecycle: the driver clears each complete capable live frame before submit,
  commit, cancel, interrupt, or EOF returns and attempts the same cleanup on
  failure before restoration. Static completion-only Enter keeps the main read
  open, so it triggers neither frame cleanup nor history append. Eligible main
  submissions append once after capable cleanup and restoration or after an
  ordinary fallback reader returns. Callback and line fallback retain printed
  prompts and input and emit no synthetic `completed` or `cancelled` control
  frame; conversation-subject input remains ordinary. Persistent semantic
  output written after the input boundary remains in scrollback. Explicit Save
  and Cancel confirmations alone carry the controller-owned changed-only
  Current/Pending context, including plain-text changed cues and any invalid-
  answer hint; ordinary editing and whole-flow cancellation emit no snapshot.
  Nested Escape returns without committing. The driver also owns the dedicated
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
  current in-memory attachment, current-focus conversation and sticky-target
  state, borrowed auth-coordinator interaction, full `/auth` refresh/default
  coordination, and freshly authenticated provider assignment and Save gates,
  resource-free facade-only generic picker interaction for settings and for a
  submitted omitted `/session resume` or `/conversation resume` after its fresh
  lifecycle or catalog read succeeds and yields eligible rows; the opened picker
  is live and transient on capable streams and numbered in line fallback. Empty
  results and read failures emit their applicable result or error without
  opening it. Before submission, exact omitted commands on capable input receive
  call-local lifecycle/catalog choice snapshots through `InteractiveMainInput`;
  dynamic-choice Enter submits an explicit identity and bypasses the picker,
  while Escape followed by Enter or nonempty VEOF submits the unchanged omitted
  buffer and follows the fresh post-submit read. That submitted main line's
  history eligibility is independent of whether the read opens a picker or
  returns an empty result or error. The controller also owns cancellable-prompt
  interaction and the settings flow's call-local pending candidate and
  confirmation diff,
  process-local per-session latest accepted STARTED debate IDs, transition and
  compensation ordering, retained runtime and memory-service wiring, one
  private lifecycle `asyncio.Runner`, controller-local interruption/progress
  proof, and outer cleanup arbitration after its one run starts. That boundary
  attempts Runner close, known-access release, backend close, and state clearing
  in order; an uncaught body process control outranks the first cleanup process
  control, later stages still run, and the retained exact object and originating
  traceback propagate only afterward. It alone owns Runner-retention and final
  controller-state clearing decisions.
  `AgentBackend` retains actual session access claims, manifests, and provider-
  thread coordination; provider adapters retain binding, staging, and provider-
  native state, while `provider_process` owns subprocess resources;
  `ConversationCatalog` retains conversation
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
- `AgentBackend` owns provider-neutral structured request/reply schemas, thread
  persistence, per-call timeout, cancellation, and taint coordination. Concrete
  adapters own provider-specific binding, command, staging, protocol parsing,
  and safe diagnostics, while `provider_process` owns bounded subprocess launch,
  concurrent stream draining, timeout/cancellation termination, and cleanup.
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
