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
Standalone stdlib-only `tools/evaluate_lead_editorial.py` owns bounded offline capture validation, deterministic blinding, blinded participant grading with packet-bound reviews,
and descriptive paired reports without runtime imports, provider execution, or workflow authority.

### Core agent and runtime contracts

Responsibility: define agent identities, shared request/result models, directives, artifact
proposal contracts, prompt construction, specialist routing, reply provenance, and common
errors/configuration. These are stable inputs to orchestration and adapters; they must not
depend on terminal presentation or concrete persistence.

- `agents`, `models`, `errors`, `config`, `prompts`, `directives`, `artifact_proposals`
- `specialist_routing`, `authoring_admission`, `core_document_readiness`, `reply_trace`, `run_inputs`

Primary access is the root facade. `specialist_routing` owns pure shared `AuthoringIntent`, independent of activity; conversation codecs preserve its immutable task lineage. `config` owns configuration loading. Scope-v3 contribution/dependency declarations and consistent per-specialist roles remain pure routing contracts; historical scope constructors retain default version 2. Historical drafting-input eligibility is pure routing policy; current execution proof remains host-owned. `core_document_readiness` owns pure whole-book criteria, document dependencies and current-source projection; adapters qualify actual USER/READ origins and retain causal captures in existing event stores. Completion grants no execution or write authority. Scope duties and exact legal rosters share immutable pure declarations; admission, readiness, exploration and premise owners declare their intrinsic result rules and current finite coverage codecs.
`prompts` owns captured policy bytes, stable fingerprints and runtime authority invariants; stages remain invocation data without provider execution.
`models` carries optional diagnostics and keyword-only `AgentRequest.expected_reviewed_proposal` and `review_only` hints, without next-action authority. Frozen scope rules carry their version and own current exact specialist rosters shared with event validation.

### Runtime orchestration and providers

Responsibility: coordinate lead/specialist execution and isolate provider-specific process,
authentication, recovery, and diagnostic behavior behind provider-neutral contracts.

- `orchestrator`, `backend`, `provider_adapter`, `antigravity_adapter`, `codex_adapter`
- `provider_auth`, `provider_auth_recovery`, `provider_catalog`, `provider_process`, `provider_execution_workspace`
- `provider_error_artifacts`, `debug_log`, `structured_output_validation`, `lead_provider_contracts`, `provider_wire`

`orchestrator` composes domain contracts, storage, consensus, Git story commits, references, and provider-neutral backends. Its Apply handoff prepares the Git candidate before atomically publishing the known result with one exact Commit request; proven absence publishes a reprepare result, while ambiguous publication requires inspection. The shared reference loader forwards typed delivery proofs; legacy consumers receive context only.
`provider_process` owns bounded subprocess transport and descriptor-pinned launch/cleanup for trusted native provider processes; it provides no OS filesystem confinement.
Adapters own native settings/resources; `provider_auth` owns probes; diagnostic adapters own files. Backend success postchecks normalize admitted records; failure diagnostics retain strict native schemas. `provider_adapter`의 불변 witness는 전송과 native 이력을 연결하며, Codex는 소유한 exact-thread SQLite 후보와 rollout을 한정해 읽고 호출 전후를 검증한다. 설정 resolver·새 store·업무 권한은 추가하지 않는다.
Concrete adapters depend on `provider_adapter`. `provider_execution_workspace` owns the internal exact-worktree capability
borrowed from the workspace binding for both providers; launch uses its duplicated root descriptor. No Git lease spans provider work.
Continuation, structured repair, authentication replay, and deferred acceptance retain the same generation authority.
`structured_output_validation` owns sealed bound contracts derived from one canonical rule graph, exact JSON numbers, construction-time typed-decoder declarations, named host-check declarations and trusted repair facts. Pure owners declare rules and permitted hooks; concrete contracts fix schema identity, validation sequencing and repair. Domain validators supply source-authored property findings; orchestration preserves them through provider path translation and retains language-neutral terminal facts and literal answers. Free authoring-slot ID uniqueness stays with the specialist scope rule owner and supplies a named residual check plus concrete native findings before typed scope construction; fixed slot objects remain structurally unique. Alias-neutral acyclic fingerprints support pure Lead graph factoring. Complete graph fingerprints retain all definition checks; compiler-only selected-root identities share the same fresh recursion for detached plain JSON contexts. Explicit schema-node proofs remain available for independent compiler queries. Each `SchemaRules` lazily owns immutable diagnostic grammar with prepared reference/union metadata and schema-admission facts for eligible repeated findings; fresh bounded payload/query state and schema source/owner consistency preserve mutable exports and diagnostic behavior without repeated implementation attestation. The Source-authored optional records have one logical declaration; strict native presence encoding is compiled transport output. `SchemaRules.provider_projection` binds an exact owner to a checked `ProviderSchemaProjection` (including declared numeric lowering), with immutable native bytes and fresh strict query state. JSON/value diagnostic entrypoints accept either exact owner for caller-scoped reuse; raw mappings retain their original diagnostic path. This package-internal boundary owns no provider state, persistence, authority or public facade. Canonical schema regex compilation shares prepared matchers within the existing immutable diagnostic grammar and fresh-query memo. SchemaRules owns immutable union-scoped identity dispatch and response-record normalization; explicit finite-selection maps retain semantic keys, while provider exports remove internal role annotations and remain strict.

`provider_wire` owns stateless vocabulary, projections, schemas, diagnostics and guidance. It consumes public core fingerprints for component rebasing, contextual Lead scope projection/inverse decoding and lossless final-document annotation sharing. Lead and service compose graphs; domain owners retain APIs, storage and state. `SchemaSelectionInput`은 현재 전체 ID 쌍·카탈로그·전달 참조의 단일 닫힌 선언이며 현재 국면 안내를 한 번 생성한다.

`lead_exploration`, `editorial_state`/`editorial_scope`, `lead_premise_decisions`, and `lead_task_transitions` own immutable whole/ranged provisional material with explicit intent, USER editorial records with canonical coverage/causal relations and exact application observations, and whole premise choice/cost contracts and USER task/discussion/source receipts. Exploration shares pure specialist intent without an upward dependency. Consumers own workflow authority; conversation events/replay retain these facts in the existing store without a new registry or resource owner.

`lead_provider_contracts` owns pure Lead rule owners and complete frozen semantic indices. Physical policy, exact finite choices, typed codecs and named checks derive schema IDs together; current duties, source edges and family details depend on domain and wire owners. Orchestration captures pure Lead planning seeds, stage, language and logical eligibility and constructs the typed factory before request composition. The factory captures its authorization declaration during construction; response decoding checks actual call/offer/repair/replay binding and invokes the captured validator with live authorization context. Current scope and prospective targets retain distinct owner constraints; substantive assignment and artifact eligibility remain in the shared rule graph. Complete indices use Wire-owned lossless annotation sharing after graph factoring. Orchestration and service retain confirmation, current-source membership, READ receipt and lifecycle authority. Current wire binds version-3 rosters; orchestration preserves reviewer-agent/input-alias pairs from actual receipts. The conversations-owned `lead_outcome_scope_paths` law shares actual kind/scope eligibility between frozen graphs, durable evidence validation and current authorized receipt checks; only qualified ordinary factual ANSWER uses REVIEW without action authority, while non-WORKFLOW CRITIQUE is unavailable. Common-scalar fingerprint groups retain original logical families, exact decoder members and contiguous physical cost partitions. Shared family details carry explicit member variant identities; direct selection retains the original member validation and inverse codec.
Empty-core evidence shares a pure eligibility rule with final runtime checks. Artifact operations consume shared source protection and canonical destination-kind rules before frozen binding. `lead_schema_groups`는 단일 레지스트리, 불변 카탈로그·선택 기록, 전체 bound member의 생성 연결과 하위 구조 검증 계약을 따르는 typed 제어 owner를 소유한다. `lead_provider_contracts`는 호출자가 보유한 준비된 의미 계획과 codec 근거를 유지하고 선택 그룹의 모든 partition을 컴파일한다. 전체 READ 사실과 신뢰된 factory 선언이 카탈로그의 근거가 되며, orchestration은 현재 요청·작업 revision·범위 근거와 기존 공유 호출 한도를 소유한다.

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

`service` owns provider delivery, exact payload UTF-8 capacity diagnostics and verified native continuity; `backend` enforces native-schema and combined schema-plus-payload input budgets from `conversations.session_context`. Logical payload and member-schema preflights retain their limits. Host retains integrity receipts and complete-history coverage. `request_compaction` shares only when properties and bytes decrease before accounting; it owns no durable state or resources. Genuine mandatory-reference proofs enter current READ offers only through the typed prelude factory and reservation checks; their complete metadata is bound into the offer digest.
Actual status snapshots stay separate from READ and are pinned in offer and repair digests.
`BoundCallContract` retains exact call/offer and outer/inner relationships. 생성 연결에는 제공한 전체 owner/member, 실제·eligibility 스냅샷과 repair/replay 근거를 보존한다. typed 제어 결과는 업무 승인에 사용할 수 없으며 같은 전체 사실의 준비된 의미 계획은 호출자가 명시적으로 전달하여 재사용한다. 서비스는 `ContextGroupInvocationState`를 request/result/failure facts로 전달하고 실제 호출 원장의 잔여량으로 국면·컨텍스트·교정·replay를 제한한다. 선택 제어는 업무 승인·체크포인트 전에 별도 진행하며, 종료 이력은 실패 상태로도 보존한다. 서비스의 불변 `ContextDeliverySnapshot`은 실제 입력만 고정하고 transport와 typed 수락을 분리한다. 논리 add/replace/remove와 native witness가 맞는 경우에만 delta를 사용하며, 미확인은 전체 bootstrap 또는 STOP이다. 기존 불변 ledger record로 실제 append와 reserve 전 인계를 검증하며 새 원장 형식이나 저장소를 추가하지 않는다. Raw JSON and decoded-value entry points share owner-issued normalized values, typed/domain validation and live host checks; raw parsing and original-byte limits remain at the wire entry.
Stable trusted schema/decoder implementations are not re-attested for each response.

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

`catalog` owns catalog behavior; `events` owns typed exploration request/report/adoption captures and schema-v2 event contracts; `context` enforces exact USER adoption input and fresh intent-bound work; `session_context` qualifies pinned same-session report sources across owners, and `context` projects exact Commit authority or typed reprepare from durable Apply history; `session_context` projects exact session history and owns the fixed UTF-8 delivery cap/content-free capacity exception, without storage bookkeeping or native-thread ownership; `events` also owns immutable qualified premise captures, strict basis-v2 codecs, and the pure factual-READ scope law exposed through `conversations` (with the existing `lead_provider_contracts` import retained); `context` validates first-confirm task pins and inherits both physical-call and round counters through the existing confirmed recommendation handoff.
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
- `consensus.serialization`, `consensus.runtime_adapter` Source-bound authoring admission is preserved in phase/submission codecs and requalified by the owning runtime port before Confirm/dispatch; new USER rulings require fresh semantic input. Explicit authoring targets/readiness use current codecs and fresh runtime dispatch checks; REVIEW_ONLY stays completion-free.
- `consensus.event_store`, `consensus.material_store`

Execution and serialization depend on contracts/projection; stores own durable workspace records. Contracts must not import execution or stores.
Consensus contracts bind shared pure intent and INPUT_ADOPTION/REVIEW_COMPLETION phases; execution uses existing material ports and confirmation capabilities without providers. The supported facade re-exports the owned intent enum. Review-only audit report handles retain approving observations for ordered synthesis through existing immutable material storage; older missing reports remain explicitly unavailable.
Serialization writes v4; AUTHORING requires current typed admission. Historical AUTHORING bytes lacking it are rejected without compatibility or backfill. Existing result/projection owners retain review-only synthesis reports for inspection without granting authority.
Results may carry ordered provider validation details; historical serialized results without the optional field remain valid.

### Writing artifacts and references

Responsibility: parse artifact references, authorize reviewed changes, model change sets and
relationships, read sources, and apply/persist artifacts through a repository boundary.
`writing_artifacts` is the supported facade; underscored storage/evidence modules are private. `relationship_policy` owns shared source-operation/kind rules; `catalog` declares exact kind-path grammar for host and Lead.

- `writing_artifacts`, `writing_artifacts.application_contracts`
- `writing_artifacts.authorization`, `writing_artifacts.catalog`, `writing_artifacts.change_sets`, `writing_artifacts.directives`
- `writing_artifacts.read_actions`, `writing_artifacts.references`, `writing_artifacts.relationship_policy`, `writing_artifacts.runtime_references`
- `writing_artifacts.filesystem_sources`, `writing_artifacts.repository`, `writing_artifacts._reference_evidence`, `writing_artifacts._application_store`, `writing_artifacts._storage`

`repository` owns artifact filesystem mutation and strict core absence batches; private stores own durable metadata. The public `read_actions.ReferenceReadResult` preserves actual authorized delivery proofs beside closeable bytes; no lower reference owner imports context or backend code.
Session callers borrow descriptor-bound repositories from the workspace binding; root configuration remains the control plane.
Runtime references retain exact session-owned evidence and release their generation pin at terminal use. `references.coalesce_reference_aliases` owns physical alias coverage; `authorization` projects exact logical-alias proofs from captured bytes through the public `runtime_references` facade without another READ.
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
The existing `_lead_policy` to `interactive_terminal._collaboration_view` and `_exchange_viewer` to `interactive_terminal._external_view` imports are narrow presentation bridges, not general private access. Exchanges validate the pinned four-export viewer contract and use its owned terminal transport. Transcript message paging uses one validated immutable store snapshot and preserves raw event-sequence anchors.

### Interactive terminal and presentation

Responsibility: own terminal models, rendering, controls, input lifecycle, external viewer,
live composition, reference activation, collaboration presentation, and the terminal driver.
`interactive_terminal` is the supported presentation facade. `InteractiveTranscriptViewer` owns transient transcript viewport state without storage ownership.

- `interactive_terminal`, `interactive_terminal._core`, `interactive_terminal._model`
- `interactive_terminal._driver`, `interactive_terminal._input_lifecycle`
- `interactive_terminal._controls`, `interactive_terminal._render`
- `interactive_terminal._advisory_progress`, `interactive_terminal._collaboration_view`
- `interactive_terminal._external_view`, `interactive_terminal._transcript_view`, `interactive_terminal._live_composer`
- `interactive_terminal._reference_activation`, `interactive_terminal._viewer_model`
- `interactive_terminal._viewer_controls`, `interactive_terminal._viewer_render`

Presentation consumes domain contracts without owning runtime state. Private `_box.Box` shares Unicode cell geometry.
`_markdown` owns sanitized prose; `_response_usage` joins exact scoped call identities. The facade's pure `project_conversation_messages` selects and deduplicates restored/transcript messages; detailed history retains event panels. Live/history/transcript share frames.
The main-input facade owns queued semantic alerts until the next read. `_output_history` owns semantic message/JSON order, group identity and navigation.
The driver owns terminal I/O, geometry and input lifecycle; borrowed views own transient rows. Lazy `open_owned` lends validated LIVE transport; jsonl-viewer owns exchange raw/screen/cursor/mouse modes. Callback/fallback refuse before content.
Resumable idle preserves partial decoding/typeahead. Main-thread termination and `InteractiveInputLifecycle` retain
native-auth handoff and lobby cleanup. Model/render/control internals are not public APIs.

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
