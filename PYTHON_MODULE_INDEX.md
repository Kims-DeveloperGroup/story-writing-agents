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

Exact module and import-edge counts are intentionally omitted; the source tree is authoritative.

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
- Cross-subsystem access to underscored modules or mutable internals is prohibited except
  for the two documented controller-to-terminal presentation bridges below. Add a narrow
  owner-facing contract or facade instead of expanding a private boundary.
- `tests/` is not a production package. Contract tests should prefer supported facades;
  focused tests may import private owners to verify internal invariants, but those imports
  neither create a public API nor justify production dependencies on test helpers.

## Subsystem inventory

### Public facade, entry points, and operational commands

Responsibility: expose APIs and CLI context through `__init__`, `__main__`, `cli`, `cli_context`, and provider-free `smoke`.
CLI adapters compose runtime, storage, interactive, and workspace facades; those owners never depend on CLI adapters.
Standalone stdlib-only `tools/evaluate_lead_editorial.py` owns bounded offline capture validation, deterministic blinding,
and descriptive paired reports without runtime imports, provider execution, or workflow authority.

### Core agent and runtime contracts

Responsibility: define agent identities, shared request/result models, directives, artifact
proposal contracts, prompt construction, specialist routing, reply provenance, and common
errors/configuration. These are stable inputs to orchestration and adapters; they must not
depend on terminal presentation or concrete persistence.

- `agents`, `models`, `errors`, `config`, `prompts`, `directives`, `artifact_proposals`
- `specialist_routing`, `reply_trace`, `run_inputs`

Primary access is the root facade. `specialist_routing` owns pure shared `AuthoringIntent`, independent of activity; conversation codecs preserve its immutable task lineage. `config` owns configuration loading. Scope-v3 contribution/dependency declarations and consistent per-specialist roles remain pure routing contracts; historical scope constructors retain default version 2.
`prompts` owns captured policy bytes, stable fingerprints and runtime authority invariants; stages remain invocation data without provider execution.
`models` carries optional diagnostics and keyword-only `AgentRequest.expected_reviewed_proposal` and `review_only` hints, without next-action authority.

### Runtime orchestration and providers

Responsibility: coordinate lead/specialist execution and isolate provider-specific process,
authentication, recovery, and diagnostic behavior behind provider-neutral contracts.

- `orchestrator`, `backend`, `provider_adapter`, `antigravity_adapter`, `codex_adapter`
- `provider_auth`, `provider_auth_recovery`, `provider_catalog`, `provider_process`, `provider_execution_workspace`
- `provider_error_artifacts`, `debug_log`, `structured_output_validation`, `lead_provider_contracts`, `provider_wire`

`orchestrator` composes domain contracts, storage, consensus, Git story commits, references, and provider-neutral backends. Its Apply handoff prepares the Git candidate before atomically publishing the known result with one exact Commit request; proven absence publishes a reprepare result, while ambiguous publication requires inspection.
`provider_process` owns bounded subprocess transport and descriptor-pinned launch/cleanup for trusted native provider processes; it provides no OS filesystem confinement.
Adapters own native command settings and resource validation; `provider_auth` owns probes; diagnostic adapters own their files.
Concrete adapters depend on `provider_adapter`. `provider_execution_workspace` owns the internal exact-worktree capability
borrowed from the workspace binding for both providers; launch uses its duplicated root descriptor. No Git lease spans provider work.
Continuation, structured repair, authentication replay, and deferred acceptance retain the same generation authority.
`structured_output_validation` owns pure, bounded diagnostics from trusted schemas and
source-authored domain findings. Domain validators identify the rejected property and rule;
orchestration and adapters retain findings through exception translation and map domain
fields to provider wire paths. This package-internal boundary owns no provider state,
persistence, or authority and is not a public facade.
`provider_wire` is a stateless stdlib leaf for vocabulary, projections, schemas, diagnostics, and invocation
guidance derived from current schema choices; semantic owners retain domain APIs, storage, and state.
`lead_exploration`, `editorial_state`, and `lead_premise_decisions` own immutable provisional material, USER editorial records, and whole premise choice/cost contracts. Consumers own workflow authority; conversation events/replay retain these facts in the existing store without a new registry or resource owner.
`lead_provider_contracts` owns immutable Lead choice snapshots shared by schema projections,
prompt facts, and closed validation rules. It depends on domain contracts; orchestration
retains confirmation, current-source membership, READ receipt, and lifecycle authority.

### Agent context and source budgeting

Responsibility: plan, account for, persist, resume, and report bounded agent context and
source reads. `service` is the composition boundary; `ledger` owns durable accounting state.

- `agent_context`, `agent_context.accounting`, `agent_context.actions`
- `agent_context.continuation`, `agent_context.ledger`, `agent_context.manifest`
- `agent_context.planning`, `agent_context.progress`, `agent_context.service`
- `agent_context.request_compaction`, `agent_context.sources`

Dependencies flow from `service`, `sources`, and `progress` toward manifest/action/planning
contracts. Filesystem source access is delegated to writing-artifact and workspace owners. Optional
validation diagnostics preserve rejection, repair, checkpoint, and authority decisions.

`service` owns provider delivery and verified native continuity; host retains integrity receipts and complete-history coverage. `request_compaction` shares only when properties and bytes decrease before accounting; it owns no durable state or resources.

### Agent exchange capture

Responsibility: model, capture, locate, and persist provider exchange records. The package
facade is public; capture adapts provider output and safe validation findings into
terminal records for each physical original or repair call, while storage owns workspace
persistence. Context and direct backend finalizers attach the actual rejection findings.

- `agent_exchanges`, `agent_exchanges.contracts`, `agent_exchanges.capture`
- `agent_exchanges.storage`, `agent_exchange_paths`

### Conversations and retrieval

Responsibility: own conversation metadata/events/context, literal search policy, and bounded
retrieval actions. `conversations` is the main conversation facade; retrieval consumes
search/storage contracts without owning persistence.

- `conversations`, `conversations.catalog`, `conversations.context`
- `conversations.events`, `conversations.session_context`
- `conversation_search`, `conversation_literal_search`, `conversation_retrieval`

`catalog` owns catalog behavior; `events` owns typed exploration request/report/adoption captures and schema-v2 event contracts; `context` enforces current-focus provenance and fresh drafting, and `context` projects exact Commit authority or typed reprepare from durable Apply history; `session_context` projects exact session history without storage bookkeeping or native-thread ownership; `events` also owns immutable qualified premise captures and strict basis-v2 codecs; `context` validates first-confirm task pins and inherits both physical-call and round counters through the existing confirmed recommendation handoff.
fresh delivery is complete, while compatible continuation carries unseen facts. Search implementations must return public
search/retrieval contracts rather than expose storage internals. Conversation events and
retrieval may carry bounded validation details associated with the physical provider call;
older records without them remain valid. The `conversations` facade exports the additive structured Commit preparation request; prose cannot authorize Commit.

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

Execution and serialization depend on contracts/projection; stores own durable workspace records. Contracts must not import execution or stores.
Consensus contracts bind shared pure intent and INPUT_ADOPTION/REVIEW_COMPLETION phases; execution uses existing material ports and confirmation capabilities without providers. The supported facade re-exports the owned intent enum. Review-only audit report handles retain approving observations for ordered synthesis through existing immutable material storage; older missing reports remain explicitly unavailable.
Serialization writes v4 and strictly admits historical v3 AUTHORING shapes/digests. Existing result/projection owners retain review-only synthesis reports for inspection without granting authority.
Results may carry ordered provider validation details; historical serialized results without the optional field remain valid.

### Writing artifacts and references

Responsibility: parse artifact references, authorize reviewed changes, model change sets and
relationships, read sources, and apply/persist artifacts through a repository boundary.
`writing_artifacts` is the supported facade; underscored storage/evidence modules are private.

- `writing_artifacts`, `writing_artifacts.application_contracts`
- `writing_artifacts.authorization`, `writing_artifacts.catalog`, `writing_artifacts.change_sets`, `writing_artifacts.directives`
- `writing_artifacts.read_actions`, `writing_artifacts.references`, `writing_artifacts.relationship_policy`, `writing_artifacts.runtime_references`
- `writing_artifacts.filesystem_sources`, `writing_artifacts.repository`, `writing_artifacts._reference_evidence`, `writing_artifacts._application_store`, `writing_artifacts._storage`

`repository` owns artifact filesystem mutation and strict core absence batches; private stores own durable metadata.
Session callers borrow descriptor-bound repositories from the workspace binding; root configuration remains the control plane.
Runtime references retain exact session-owned evidence and release their generation pin at terminal use.
Callers must not import `_storage`, `_application_store`, or `_reference_evidence`.

### Git resources, history, checkpoints, and story commits

Responsibility: isolate fixed Git commands and contracts, own repository locking, provision
session resources, record session history/checkpoints, and prepare/execute local story
commits. `git` re-exports the general adapter/contracts; workflow-specific modules expose
only their intentional contract paths.

- `git`, `git.contracts`, `git.adapter`, `git.process`, `git.repository_lock`
- `git.session_contracts`, `git.session_resources`, `git.session_history_contracts`, `git.session_history`
- `git.session_checkpoint_contracts`, `git.session_checkpoints`, `git.story_commit_contracts`, `git.story_commit_candidates`, `git.story_commits`

`git.process` owns fixed-profile children, bounded nonblocking read-only pipes and call-local selectors; `repository_lock` owns cross-process serialization.
Resource/history/checkpoint/commit services depend inward on those owners; general contracts do not import workflow services. `session_resources` delegates bounded fresh loose-object frames and shared format policy to package-internal `inspection_objects`; it owns Git fallback and unchanged raw-tree/root verification. Loose-ref/object proofs retain named descriptors and inspection-local state.
Generation capture and pinning remain opaque internal contracts. Bound history and story-commit services validate
exact generation without exporting descriptors or general mutation authority.
`git.session_resources` proves the literal top-level `.agents` namespace and every case variant
absent from the exact linked-worktree index under a retained lease and generation. Its optional
effective-ignore proof uses fixed `git check-ignore --quiet --no-index -- .agents/`; index and
generation facts bracket both checks. This owner never mutates Git for role projection.

### Workspace, runtime state, and session lifecycle

Responsibility: validate workspace schema/security, initialize workspaces, derive runtime
state paths, manage session lifecycle, and assess/reconcile recovery transitions.

- `workspace`, `workspace.core`, `workspace.schema`, `workspace.initialization`, `workspace.session_role_projection`, `workspace._policy_baselines`, `runtime_state_paths`, `session_lifecycle`
- `session_recovery`, `session_recovery.contracts`, `session_recovery.transition_assessment`, `session_recovery.reconciliation`

`workspace.core` owns path validation and session source/configuration views; `initialization` owns creation,
session artifact bindings, guarded repositories, and opaque generation-pin composition. Views borrow retained session
roots through bounded owner contracts. `schema` owns schema-v2 validation and current initializer defaults; `_policy_baselines` retains immutable published bytes for exact initialization compatibility.
The binding calls the Git owner's index and effective-ignore proof after exclusion preparation
and again after role publication. `session_role_projection` owns create-only, read-only native
role files and the private Git exclusion: `/.agents/` must be its final nonempty record, and
bounded positive short writes finish on the retained descriptor before fsync and readback.
Recovery contracts point inward; reconciliation borrows opaque Git generation authority.
For known Apply, recovery projects either the exact extant Commit request or typed reprepare, while Commit still requires its single-use structured decision.
Workspace readers accept optional bounded validation details and legacy entries without them.

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
The existing `_lead_policy` to `interactive_terminal._collaboration_view` and
`_exchange_viewer` to `interactive_terminal._external_view` imports are narrow internal
presentation bridges, not general private-access permission.

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
Collaboration notices render bounded validation details and scope-v3 duties, dependency choices and qualified
captured sources before confirmation without owning runtime authority.
The external viewer integration is lazy. `transient_only` refuses line fallback before content is rendered; `ExternalViewUnavailableError` identifies that refusal. Borrowed plain-line hosts remain for views that allow fallback. The driver owns timed viewer input polling, scoped lobby echo suppression, and temporary default SIGTERM/SIGHUP dispositions through original-terminal restoration; required signal ownership is acquired on the main thread. Individual reads retain noncanonical-mode ownership, and the persistent reader owns partial decoding across idle intervals. `InteractiveInputLifecycle` lends original terminal settings to native login and restores them after lobby cleanup. Private model/render/control modules are not APIs.

### Interactive support

Responsibility: parse shell commands, manage line editing, authenticate providers, and edit
interactive settings. These adapters are composed by the controller and terminal.

- `interactive_auth`, `interactive_commands`, `interactive_line_editor`
- `interactive_settings`

`interactive_commands` is re-exported selectively by the root facade. Authentication and
settings modules may use provider/config/workspace owners but must not own their state.
The authentication coordinator borrows `InteractiveInputLifecycle` only around native login; terminal settings remain driver-owned.

## Maintenance checks

1. When production module boundaries change, compare the inventory with `src/story_writing_agents/**/*.py`, treating each package `__init__.py` as its package path.
2. Verify affected `__all__` declarations and `pyproject.toml` entry points.
3. Inspect runtime imports for cycles, reversed adapter dependencies, and private imports.
4. Confirm single ownership of stateful resources and no import-time I/O, authentication, subprocesses, or startup; document only implemented state; put design proposals in the change brief.
