# Python Module Index

## Purpose

This is the current navigation map for production Python under
`src/story_writing_agents/`. It groups importable units by responsibility instead of
duplicating implementation detail. The source tree and package exports remain the source
of truth; update this map when a module moves between subsystems, a public facade changes,
or resource ownership/dependency direction changes. Normative boundary rules live in the
[Python Modularity Policy](.codex/agents/runtime_implementer/PYTHON_MODULARITY_POLICY.md).

Paths below are import paths relative to `story_writing_agents`; each maps to the same path
below `src/story_writing_agents/` with `.py` or `/__init__.py`. A package name in an
inventory includes its `__init__.py`.

Importable production units indexed: 143. Direct internal dependency edges indexed: 574.
Directed internal dependency cycles indexed: 0.

## Supported public surfaces

- `story_writing_agents` is the library facade. Its `__all__` is the supported top-level
  API for agent specifications, conversations and retrieval, directives, errors, commands,
  runtime models, session lifecycle, workspace access, and `StoryRuntime`.
- `story_writing_agents.cli:main` backs both installed commands: `story` and
  `story-writing-agents`. `story_writing_agents.__main__` is the module entry point.
- Package facades with intentional re-exports are `agent_context`, `agent_exchanges`,
  `consensus`, `conversation_storage`, `conversations`, `git`, `interactive_controller`,
  `interactive_terminal`, `session_recovery`, `workspace`, and `writing_artifacts`.
- Underscored modules and symbols are private. Consumers must use the owning facade or
  documented public module; tests importing private paths do not promote them to API.

## Dependency direction and boundary rules

The normal runtime direction is:

```text
CLI / interactive shell
  -> runtime orchestration and session policies
  -> domain contracts and package facades
  -> storage, Git, provider, and filesystem adapters
  -> operating system / external tools
```

- Domain contracts must not import CLI, terminal rendering, or concrete persistence.
- Orchestration may compose adapters; adapters must not call back into orchestration.
- Filesystem, SQLite, Git subprocesses, provider processes, authentication, logs, and
  runtime state each remain behind their named owner below.
- Optional RAG infrastructure is loaded through composition/adapters so core imports do
  not require optional packages.
- Runtime import cycles are prohibited. `TYPE_CHECKING`, local imports, or dynamic imports
  must not hide a cycle; dynamic loading is reserved for documented extension boundaries.
- Cross-subsystem access to underscored modules or mutable internals is prohibited. Add a
  narrow owner-facing contract or facade instead of reaching across a private boundary.
- `tests/` is not a production package. Contract tests should prefer supported facades;
  focused tests may import private owners to verify internal invariants, but those imports
  neither create a public API nor justify production dependencies on test helpers.

## Subsystem inventory

### Public facade, entry points, and operational commands

Responsibility: expose the supported Python API, dispatch installed commands, build CLI
context, and provide provider-free smoke verification. The CLI is a top-level adapter and
may depend on runtime, storage, interactive, and workspace facades; none may depend on it.

- `__init__`, `__main__`, `cli`, `cli_context`, `smoke`

### Core agent and runtime contracts

Responsibility: define agent identities, shared request/result models, directives, artifact
proposal contracts, prompt construction, specialist routing, reply provenance, and common
errors/configuration. These are stable inputs to orchestration and adapters; they must not
depend on terminal presentation or concrete persistence.

- `agents`, `models`, `errors`, `config`, `prompts`, `directives`, `artifact_proposals`
- `specialist_routing`, `reply_trace`, `run_inputs`

Primary public access is through the root facade for stable library types. `config` owns
runtime configuration loading; `prompts` owns prompt assembly, not provider execution.

### Runtime orchestration and providers

Responsibility: coordinate lead/specialist execution and isolate provider-specific process,
authentication, recovery, and diagnostic behavior behind provider-neutral contracts.

- `orchestrator`, `backend`, `provider_adapter`, `antigravity_adapter`, `codex_adapter`
- `provider_auth`, `provider_auth_recovery`, `provider_catalog`, `provider_process`
- `provider_error_artifacts`, `debug_log`

`orchestrator` composes domain contracts, storage, consensus, Git story commits, references,
and provider-neutral backends. `provider_process` owns provider subprocess execution;
`provider_auth` owns authentication probes; `provider_error_artifacts` and `debug_log` own
their filesystem outputs. Concrete adapters depend inward on `provider_adapter` contracts.

### Agent context and source budgeting

Responsibility: plan, account for, persist, resume, and report bounded agent context and
source reads. `service` is the composition boundary; `ledger` owns durable accounting state.

- `agent_context`, `agent_context.accounting`, `agent_context.actions`
- `agent_context.continuation`, `agent_context.ledger`, `agent_context.manifest`
- `agent_context.planning`, `agent_context.progress`, `agent_context.service`
- `agent_context.sources`

Dependencies flow from `service`, `sources`, and `progress` toward manifest/action/planning
contracts. Filesystem source access is delegated to writing-artifact and workspace owners.

### Agent exchange capture

Responsibility: model, capture, locate, and persist provider exchange records. The package
facade is public; capture adapts provider output and storage owns workspace persistence.

- `agent_exchanges`, `agent_exchanges.contracts`, `agent_exchanges.capture`
- `agent_exchanges.storage`, `agent_exchange_paths`

### Conversations and retrieval

Responsibility: own conversation metadata/events/context, literal search policy, and bounded
retrieval actions. `conversations` is the main conversation facade; retrieval consumes
search/storage contracts without owning persistence.

- `conversations`, `conversations.catalog`, `conversations.context`
- `conversations.events`, `conversations.session_context`
- `conversation_search`, `conversation_literal_search`, `conversation_retrieval`

`catalog` owns catalog behavior, `events` owns schema-v2 event contracts, and
`session_context` derives session-scoped context. Search implementations must return public
search/retrieval contracts rather than expose storage internals.

### Conversation storage

Responsibility: select and compose conversation stores, implement filesystem/SQLite stores,
archive and migrate data, and provision storage resources. Public contracts and composition
are re-exported by `conversation_storage`; backend details stay internal to the subsystem.

- `conversation_storage`, `conversation_storage.contracts`
- `conversation_storage.composition`, `conversation_storage.selection`
- `conversation_storage.filesystem`, `conversation_storage.filesystem_archive`
- `conversation_storage.sqlite`, `conversation_storage.sqlite_database`
- `conversation_storage.sqlite_search_index`, `conversation_storage.archive`
- `conversation_storage.migration`, `conversation_storage.provisioning`

`filesystem` and `sqlite` own their data layout and handles. `migration` may coordinate both
backends through contracts; conversation/domain modules must not import a concrete backend.

### Retrieval-augmented conversation search

Responsibility: define the RAG contract, provide local and optional generic-RAG adapters,
persist indexes, compose them, expose runtime retrieval, and support RAG administration.

- `conversation_rag`, `conversation_rag_local`, `conversation_rag_store`
- `conversation_rag_composition`, `conversation_rag_runtime`, `conversation_rag_cli`
- `generic_rag_adapter`

Core runtime depends on `conversation_rag_runtime`, not concrete optional packages.
`conversation_rag_composition` is the only normal construction boundary for optional
adapters; `conversation_rag_store` owns index persistence.

### Consensus workflow

Responsibility: define consensus state, execute phases, project and serialize events, adapt
runtime collaborators, and persist event/material records. `consensus` is the supported
facade; contracts and projections remain infrastructure-neutral.

- `consensus`, `consensus.contracts`, `consensus.execution`, `consensus.projection`
- `consensus.serialization`, `consensus.runtime_adapter`
- `consensus.event_store`, `consensus.material_store`

Execution depends on contracts/projection; serialization depends on contracts/projection;
stores own durable workspace records. Contracts must not import execution or stores.

### Writing artifacts and references

Responsibility: parse artifact references, authorize reviewed changes, model change sets and
relationships, read sources, and apply/persist artifacts through a repository boundary.
`writing_artifacts` is the supported facade; underscored storage/evidence modules are private.

- `writing_artifacts`, `writing_artifacts.application_contracts`
- `writing_artifacts.authorization`, `writing_artifacts.catalog`
- `writing_artifacts.change_sets`, `writing_artifacts.directives`
- `writing_artifacts.read_actions`, `writing_artifacts.references`
- `writing_artifacts.relationship_policy`, `writing_artifacts.runtime_references`
- `writing_artifacts.filesystem_sources`, `writing_artifacts.repository`
- `writing_artifacts._reference_evidence`, `writing_artifacts._application_store`
- `writing_artifacts._storage`

`repository` owns artifact filesystem mutation and delegates durable metadata to private
stores. Runtime callers use authorization/application contracts and public references; they
must not import `_storage`, `_application_store`, or `_reference_evidence`.

### Git resources, history, checkpoints, and story commits

Responsibility: isolate fixed Git commands and contracts, own repository locking, provision
session resources, record session history/checkpoints, and prepare/execute local story
commits. `git` re-exports the general adapter/contracts; workflow-specific modules expose
only their intentional contract paths.

- `git`, `git.contracts`, `git.adapter`, `git.process`, `git.repository_lock`
- `git.session_contracts`, `git.session_resources`
- `git.session_history_contracts`, `git.session_history`
- `git.session_checkpoint_contracts`, `git.session_checkpoints`
- `git.story_commit_contracts`, `git.story_commit_candidates`, `git.story_commits`

`git.process` alone executes Git subprocess profiles. `repository_lock` owns cross-process
serialization; resource/history/checkpoint/commit services depend on those lower-level
owners and must not be imported by general contracts.

### Workspace, runtime state, and session lifecycle

Responsibility: validate workspace schema/security, initialize workspaces, derive runtime
state paths, manage session lifecycle, and assess/reconcile recovery transitions.

- `workspace`, `workspace.core`, `workspace.schema`, `workspace.initialization`
- `workspace._policy_baselines`, `runtime_state_paths`, `session_lifecycle`
- `session_recovery`, `session_recovery.contracts`
- `session_recovery.transition_assessment`, `session_recovery.reconciliation`

`workspace.core` owns workspace path validation and access; `initialization` owns creation;
`schema` owns schema-v2 validation. `_policy_baselines` is private immutable policy data.
Session recovery contracts point inward and must not depend on interactive presentation.

### Interactive controller

Responsibility: coordinate shell commands and runtime policies while keeping conversation,
exchange, lead, session, and runtime concerns in focused private policy modules.
`interactive_controller.InteractiveController` is the supported facade.

- `interactive_controller`, `interactive_controller._core`
- `interactive_controller._conversation_policy`, `interactive_controller._exchange_policy`
- `interactive_controller._exchange_viewer`, `interactive_controller._lead_policy`
- `interactive_controller._runtime_policy`, `interactive_controller._session_policy`

Only `_core` composes the private policies. Other subsystems must not import those policies;
the controller may depend on runtime/storage/terminal facades, never the reverse.

### Interactive terminal and presentation

Responsibility: own terminal models, rendering, controls, input lifecycle, external viewer,
live composition, reference activation, collaboration presentation, and the terminal driver.
`interactive_terminal` is the supported presentation facade.

- `interactive_terminal`, `interactive_terminal._core`, `interactive_terminal._model`
- `interactive_terminal._driver`, `interactive_terminal._input_lifecycle`
- `interactive_terminal._controls`, `interactive_terminal._render`
- `interactive_terminal._advisory_progress`, `interactive_terminal._collaboration_view`
- `interactive_terminal._external_view`, `interactive_terminal._live_composer`
- `interactive_terminal._reference_activation`, `interactive_terminal._viewer_model`
- `interactive_terminal._viewer_controls`, `interactive_terminal._viewer_render`

Presentation modules may consume public runtime/domain contracts but never own runtime state.
The external viewer integration is lazy. `transient_only` refuses line fallback before content is rendered; `ExternalViewUnavailableError` identifies that refusal. Borrowed plain-line hosts remain for views that allow fallback. Private model/render/control modules are not APIs.

### Interactive support

Responsibility: parse shell commands, manage line editing, authenticate providers, and edit
interactive settings. These adapters are composed by the controller and terminal.

- `interactive_auth`, `interactive_commands`, `interactive_line_editor`
- `interactive_settings`

`interactive_commands` is re-exported selectively by the root facade. Authentication and
settings modules may use provider/config/workspace owners but must not own their state.

## Maintenance checks

When production modules or boundaries change:

1. Compare this inventory with all `*.py` files below `src/story_writing_agents/`, treating
   each package `__init__.py` as its package import path.
2. Verify affected `__all__` declarations and `pyproject.toml` entry points.
3. Inspect runtime imports for cycles, reversed adapter dependencies, and private imports.
4. Confirm stateful resources still have one owner and import-time code performs no external
   I/O, authentication, subprocess execution, or application startup.
5. Update only the implemented current state; design proposals belong in the change brief.
