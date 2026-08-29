# System design overview

> **Availability:** this page describes implemented runtime ownership,
> dependency direction, filesystem/SQLite conversation selection, and the
> installed interactive controller. The controller now uses the package-internal supplied-text viewer
> for `$`/`$$` prompt editing; the viewer still has no standalone command or
> supported public API. Separately, bare `/exchanges` selects one bounded
> agent-exchange snapshot and hosts the optional isolated `jsonl-viewer`
> package through Story's existing terminal owner. Optional conversation RAG currently has direct Python
> contracts, a deterministic local provider, a disposable generation store, and
> explicit composition plus programmatic runtime/agent integration. Six
> noninteractive local lifecycle/search commands are implemented; interactive
> `/settings`, automatic eligible-turn wiring, and one-turn `/rag QUERY` are
> also available.

Key terms: **controller**—owner of interactive transition order;
**adapter**—provider-specific process/protocol implementation;
**scope**—what a service may read;
**default**—persisted fallback; **focus**—current process-local scope.

`story-writing-agents` separates workflow, provider invocation, terminal
presentation, and filesystem persistence. In this project, “commit” means
writing approved story text; the runtime never invokes Git.

## Runtime and provider ownership

`StoryRuntime` owns validated debate/advisory sequencing, consensus, retrieval
authority, replies, artifacts, and per-agent desired/current/retiring binding
selection. It leases the exact selected binding across each provider-bound
operation, captures authoritative sources, and delegates bootstrap/delta and
bounded SEARCH/READ execution to the agent-context service. The old
`story_session_context` object remains internal capture/direct compatibility;
compact wire v5 is the built-in provider payload. See the
[agent-context design](agent-context.md) and
[Runtime API](../reference/runtime-api.md#one-conversation-turn).

`session_lifecycle` owns immutable provider-neutral lifecycle facts and pure
readiness, with no resource or I/O. `AgentBackend` retains manifests, access
claims, routes, active-call retention, and provider-neutral thread and
read-action coordination. It is the sole authoritative resolver of the active
provider/model/effort profile and sources; adapters receive that profile for
argv. Provider adapters own bindings, staging, and
provider-native state; `provider_process` owns subprocess resources. A
`BoundAgent` is the immutable handle for one definition and binding, not the
runtime selection owner. Codex and Antigravity adapters are implemented; the
workspace default precedes the final Codex fallback. The obsolete backend name
`gemini` is rejected with guidance to use `antigravity`.

Provider threads are partitioned by story session, backend, and agent.
Antigravity adds a stable provider-native state root isolated by session, agent,
and definition fingerprint. Closing a backend preserves thread references and
Antigravity-native state; taint or a changed definition starts a fresh provider
conversation without deleting prior data. See the
[Backend/session contract](../reference/backend-and-sessions.md) and
[Antigravity guide](../guides/antigravity.md).

## Optional conversation RAG ownership

| Module | Responsibility |
| --- | --- |
| `conversation_rag` | Standard-library-only story contracts, fixed projection/retrieval budgets, stable source identities, collaborator protocols, outcomes, reasons, and content-free failures |
| `conversation_rag_local` | Pure fixed local embedding and deterministic vector ranking; no generic package, filesystem, network, credential, subprocess, environment, or lifecycle ownership |
| `conversation_rag_store` | Identity-bound disposable per-session generation construction, full verification, active/status pointers, reader contexts, and confirmed derived-only purge |
| `generic_rag_adapter` | Sole production translation boundary that imports story contracts and the exact optional `generic-rag` API |
| `conversation_rag_composition` | Import-safe deferred generic/local composition, fixed `local` allowlist, version/capability inspection, concrete local store/state/runtime bundle, store-aware fallback, and sanitized diagnostics |
| `conversation_rag_runtime` | Provider/store-neutral no-write inspection, authoritative projection, literal-first merge, direct authorized search, current reread, citations, bounded agent context, and restart-safe state coordination |
| `conversation_rag_cli` | Direct-only immutable request/report vocabulary and six local configuration/lifecycle/search workflows; no interactive controller or storage-adapter ownership |

`generic-rag` has no dependency on this project. The package root does not
re-export these modules, the base install does not need the generic package,
and importing the provider/store/composition path neither probes package
metadata nor imports `generic-rag`. Explicit composition requires the pinned
optional package. `conversation_rag_runtime` consumes only story-owned contracts
and a complete `ConversationStorageServices` authority; it does not import the
generic package, store, provider, controller, or settings. It authorizes active
complete visible turns, schedules projection, reauthorizes stable references,
rereads exact current bytes, and constructs bounded cited agent context.
Provider fragment text and every derived generation remain non-authoritative.
Callers own their workspace, store, lexical collaborator, low-level service,
and restart-safe runtime-state-port lifecycles; fixed local composition can
instead construct and retain those concrete resources for the CLI or another
application. Configuration owns only nullable provider/exact automatic values,
the noninteractive CLI owns its compare-and-set workflow, and interactive
Settings owns one pending guarded edit transaction. The controller reads the
backend construction snapshot once, composes Local exactly once beside a
complete storage bundle, and owns only the process-local pending `/rag` query.
See
[Optional conversation RAG](../guides/conversation-rag.md) for installation,
the weak-local/persistence/runtime boundary, current commands, and interactive
flow.

Provider switches also advance a durable per-agent route generation, including
A→B→A, so each switch starts a fresh target-provider thread. Same-provider
model/effort changes retain that generation and remain eligible for thread
reuse. Native-thread freshness does not erase story-session continuity: a new
contextual epoch bootstraps from current authoritative role-relevant sources.
Compatible READY state can then deliver exact record/source changes instead of
unchanged bodies.

`Workspace` owns generic path-safe persistence, inspection, and atomic
replacement. Its retained root identity is a narrower internal
diagnostic-storage seam; ordinary domain I/O remains pathname-based and may
observe a real-directory replacement at the same root path. The
`writing_artifacts` package owns the stricter canonical-output grammar,
provenance, publication, and recovery boundary; generic workspace writes never
manufacture artifact provenance. See
[Filesystem safety](../operations/filesystem-safety.md).

## Writing-artifact ownership

| Module | Responsibility |
| --- | --- |
| `writing_artifacts.__init__` | Side-effect-free facade for immutable artifact values; no repository construction or root-package export. |
| `writing_artifacts.catalog` | Four exact core identities, part-scoped manuscript grammar, kinds, immutable provenance entries, and catalog schema. |
| `writing_artifacts.references` | Immutable path/range, directive-batch, verified-snapshot, and resource-limit values plus the reference-source eligibility predicate; parsing belongs to `writing_artifacts.directives`, authorization and provider enforcement belong to the authorization/read-action/backend path, and viewing belongs to the independent `interactive_terminal` supplied-text viewer. |
| `writing_artifacts.read_actions` | Provider-neutral, content-free exact request/action contracts; private synchronous coordinator protocol and closeable content-bearing result contract. |
| `writing_artifacts._storage` | Descriptor-relative, no-follow, unique-regular-file I/O, compare-before-publish replacement, root-identity verification, and artifact advisory locking. |
| `writing_artifacts.filesystem_sources` | Catalog-independent deterministic discovery of the four supported core paths and every eligible lowercase-`.md` descendant under `manuscript/`; bounded current-source capture; strict UTF-8/NUL validation; and full physical/hash identity matching. |
| `writing_artifacts.repository` | Facade over filesystem sources plus provenance validation, current catalog projection, create-only manuscript commits, journal roll-forward, catalog-last visibility, fail-closed recovery, and concrete one-use reference coordinators/capabilities. |

The package has no controller, terminal, backend, orchestrator, or generic
`Workspace` dependency. The backend imports only the provider-neutral
`read_actions` boundary; the repository facade remains the sole runtime owner
of source reads, revalidation, capabilities, and evidence, while its private
filesystem-source adapter performs catalog-independent source I/O.
`StoryRuntime` parses and
captures one ordered explicit-reference batch per operation, allocates a fresh
coordinator for every addressed agent/phase, and accepts the operation only
after final revalidation. Built-in prompt assembly binds no mutable core files;
current core documents are contextual P2 sources. Referenced selections are
transient untrusted mandatory provider context rather than ambient context.
The interactive controller validates required manuscript output grammar but
does not own storage or provenance. Exact user-visible states are in
[Canonical writing artifacts](../reference/writing-artifacts.md).

The internal `debug_log` module borrows that containment boundary and owns its
own safe event schema, per-workspace process serialization, independent
cross-process guard, append rollback, and bounded rotation. It has no
project-root export. Constructing its workspace-bound handle performs no I/O;
`StoryRuntime` construction emits nothing, `AgentBackend` starts only after its
complete configuration snapshot loads, and CLI, controller, and smoke emission
starts only after workspace resolution or initialization succeeds.

Ordinary diagnostic records use schema version 1. Backend execution-profile
startup, hot change, applied, confirmed, and recovery records use schema version
2 and intentionally include exact provider/model/effort identities and sources
without story content or provider stderr.

The low-level writer suppresses ordinary `Exception` failures. The five
consumers—CLI, backend, runtime, controller, and smoke—submit only fixed facts
through private guards that also catch diagnostic `BaseException`. Thus
`debug_log` alone owns schema, files, locks, append rollback, and rotation, while
a diagnostic `KeyboardInterrupt` or `SystemExit` cannot replace application
results, exceptions, cancellation, cleanup, output, or exit behavior. Implicit
smoke owns and removes its temporary `Workspace`. Explicit CLI smoke initializes
and retains one identity-bearing `Workspace`; smoke and all fresh workspace-bound
services borrow that same instance, and the CLI does not resolve the path again
for handoff or post-run diagnostics. CLI and smoke logger-handle acquisition and
submissions are independently guarded, so root replacement can omit a record
but cannot redirect it or change the report, stderr, exception, or exit. The CLI
records post-workspace hint eligibility separately from its optional handle, so
a later eligible hint can appear when no diagnostic file exists.

## Agent-exchange diagnostic ownership

Agent exchanges are independent of the content-safe runtime log and the
separate rejected-response artifact channel:

| Owner | Responsibility |
| --- | --- |
| `agent_exchanges.contracts` | Closed immutable outer JSONL schema, request/outcome/scope/failure vocabulary, bounds, counts, digest, inventory, selection, and snapshot values |
| `agent_exchanges.storage` | Sole descriptor-relative append, sequence, timestamp, rotation, global pruning, inventory, and immutable snapshot persistence owner |
| `agent_exchanges.capture` | Fail-open request, exact valid `ProviderResult`, and terminal-outcome sequencing around each physical provider call |
| `interactive_controller._exchange_policy` | Pure inventory deduplication, newest-scope ordering, Conversation/Debate subject projection, and complete roster-agent choices |
| `interactive_controller._exchange_viewer` | Sole lazy optional-package boundary; validates the exact external three-name API and Story field/header projection before terminal acquisition |
| `jsonl-viewer` at the exact optional pin | Independent Story-free immutable JSONL parsing, Simple/Verbose projection, line gutter/navigation, search, rendering, help, and transient view state |
| `interactive_terminal._external_view` | Generic borrowed-host protocol, printable/SGR frame validation, and ordinary-line event adaptation without renderer or Story-domain knowledge |
| `interactive_terminal._driver` | Sole raw-mode, input, signal, geometry, output, alternate-screen, and restoration lifecycle for the hosted view |

The controller core alone composes these boundaries. It reads content-free
inventory, resolves authoritative catalog/debate display subjects, opens two
transient pickers, copies one exact immutable snapshot or empty bytes, prepares
the external operation, and gives it to an unlisted resource-free terminal
adapter. No storage or capture module imports the controller, terminal, viewer,
or optional package. The external package imports no Story module. See the
[persistence contract](persistence.md#agent-exchange-persistence), [inspection
guide](../guides/agent-exchanges.md), and pinned external [design
system](https://github.com/Kims-DeveloperGroup/jsonl-viewer/blob/5d3047aa86212810d867d5b40cb1df3262f1810f/docs/design-system.md).

## Fixed collaboration model

The lead moderates and finalizes but does not vote. Three specialists audit each
revision; all must approve. Blackboard and owner-isolated history stay separate.

## Interactive ownership

| Owner | Responsibility |
| --- | --- |
| `interactive_commands` | Pure grammar, canonical agent-route metadata, and cursor-aware completion projection for finite main-command positions |
| `interactive_controller` | Stable `InteractiveController` import facade; no state or I/O |
| `interactive_controller._core` | Sole controller state, dispatch, transition order, I/O sequencing, resources, cleanup, typed semantic output composition, responsive active/one-shot execution-profile selection, disposable resume and agent-exchange choices/snapshots, settings hot-activation coordination, and fresh post-submit validation/mutation |
| `interactive_controller._exchange_policy` | Pure agent-exchange scope/subject/agent projection with exact inventory correlation and no I/O |
| `interactive_controller._exchange_viewer` | Lazy exact optional `jsonl-viewer` API validation and immutable Story-to-generic viewer specification/operation preparation |
| `interactive_controller._session_policy` | Pure lifecycle validation, next actions, and typed failure classification |
| `interactive_controller._conversation_policy` | Pure catalog, reference, search, transcript, and failure interpretation |
| `interactive_controller._runtime_policy` | Pure progress, result, failure interpretation, and frozen validated inspection facts |
| `interactive_settings` | Package-internal immutable current/pending configuration snapshots, pending desired storage, safe persona catalog, durable provider-route generations, exact conflict detection, and prepared system/agent/optional-route persistence and restoration; conversation-storage selection coordinates readiness and the desired system setting under its lease |
| `cli_context`, catalog, memory service, event store | Defaults, identity, scoped reads, and authoritative events |
| `interactive_terminal` | Stable package-supported facade, package-internal real-CLI factory, and unlisted internal semantic output, controller-main-input, picker, cancellable-prompt, supplied-text viewer, and external-viewer adapters and identities |
| `interactive_terminal._core` | Established terminal API behavior, explicit ordinary/main/composition/confirmation routing, canonical static/dynamic main-choice adaptation, semantic input-view and lifecycle-event construction, call-local reference scope/source-list/text binding, resource-free semantic output/main/picker/prompt/supplied-text/external-viewer adapters, and terminal-leaf coordination |
| `interactive_terminal._external_view` | Story-neutral borrowed `ExternalViewerHost`/operation contracts, strict printable-or-SGR frame validation, and bounded ordinary-line event vocabulary; no terminal resource or renderer ownership |
| `interactive_terminal._model`, `_controls`, `_render` | Immutable semantic/history/completion/dynamic-choice/composer/picker foundation with a distinct ranged-reference palette position; normalized keys including Shift+Arrow; pure line, palette, history, composer, confirmation, prompt, and picker transitions; immediate second-dollar projection and highlight ownership; bounded picker viewport; and pure safe responsive output/input rendering |
| `interactive_terminal._reference_activation` | Private pure provider-span-bounded `$` marker projection, whole-file completion, correlated completion plus picker request for exact `$$`, selected-path correlation, exact marker replacement, and content-free fixed failure contracts; no I/O or retained state. |
| `interactive_terminal._viewer_model` | Private immutable strict-UTF-8 supplied document, active/anchor state, bounded viewport, notice, exit-kind, and content-free inclusive-range result contracts; LF-only line derivation with terminal-local 8 MiB source, 65,536-line, and complete 16 MiB fallback-projection limits |
| `interactive_terminal._viewer_controls` | Pure fresh initialization, ordinary and anchored navigation, normalized commit and noncommit exits, exact ASCII fallback grammar, and bounded contiguous viewport projection |
| `interactive_terminal._viewer_render` | Pure bounded-prefix one-row-per-source-line live and complete numbered fallback projections with visible escaping, line numbers, plain/ANSI state cues, cell-aware live ellipsis clipping, and no fallback clipping |
| `interactive_terminal._input_lifecycle` | Irreversible per-driver `UNDECIDED` to `LIVE`/`FALLBACK` selection, exact source/decoder identity, admitted codec/error policy, persistent one-byte incremental decoding, bounded pending suffix, stable EOF, poison/interrupt behavior, and synchronous input exclusion |
| `interactive_terminal._driver` | Sole real-CLI raw streams, capabilities, width/height, byte/key decoding integration, configured-control validation, pending-suffix-first fixed Escape readiness wait, complete-live-frame cleanup, supplied-text and hosted external-view alternate screens, cursor controls, writes, flushes, restoration, one bounded process-local main-history store, nonmodal ranged-source selection-to-viewer correlation, and dedicated history-free picker/prompt/viewer lifecycles |
| `interactive_line_editor` | Standalone compatibility facade over the same supported main-command reader and one unified per-editor driver/history store |
| `StoryRuntime`, `AgentBackend` | Orchestration/retrieval authority and provider resources |

The supported import remains `from story_writing_agents.interactive_controller import InteractiveController`.
The facade re-exports the core's same class;
shell behavior and project-root exports are unchanged. Only the core retains
collaborators and mutable process state.

On session-lobby exit, the core attempts each applicable stage once in this
order: Runner close, known-access release, backend close, then in-memory state
clearing. An uncaught body `KeyboardInterrupt` or `SystemExit` wins; otherwise
the first cleanup process control wins after every later stage runs. The same
exception object and traceback propagate. The Runner reference remains only
when its close was interrupted by process control; attachment, focus, and memory
always clear and the controller closes. With no retained process control,
ordinary cleanup failure returns `2` and attempts one nonduplicated
`cleanup_failed` display. A diagnostic process control cannot replace an already
retained body or cleanup winner. This outer-boundary arbitration does not
reclassify a Ctrl-C or cancellation already handled by a command.

The supported terminal import remains
`from story_writing_agents.interactive_terminal import InteractiveTerminal`.
Its package facade preserves the exact existing public objects and established
call compatibility; `write_error()` alone adds the optional keyword-only
`debug_log_path=None` presentation seam. Package leaves are internal. The CLI
calls one package-internal factory to construct a stream-backed terminal around
one `RawTerminalDriver`. The
standalone line-editor facade delegates to that same driver implementation and
contains no second raw-terminal lifecycle. Each constructed stream terminal or
standalone editor has one isolated 100-entry process-local main-command store;
the callback-only terminal and controller have none.

The semantic models, responsive plain/fixed-ANSI renderer, and capability
profiles are implemented. Orientation, status/inspection, central errors and
recovery, and accepted management, advisory, retrieval, and consensus output
use them. Full/context help, roster, session/conversation lists, search,
transcript, and focused history now use them too. The controller sends each
dense row as one positioned `ListView` callback: session, conversation, and
search rows use `Record` where applicable, while transcript and history rows use
attributed `Message` values. Empty and interrupted outcomes remain explicit.
Composition and confirmation now build
ordered semantic input views, and the four composer lifecycle records use
semantic `open`, `error`, `completed`, and `cancelled` notices. No internal
composer `write_status` site remains. Capable main reads now project canonical
command grammar into a semantic selectable palette; fallback, composition,
confirmation, and subject reads do not. Exact omitted resume commands may add
controller-owned live rows to that capable main palette. The controller owns
their ordering, initial highlight, catalog/lifecycle reads, and fresh validation;
the terminal owns only input state and presentation. There is no public theme API.

The capable main frame also receives a controller-provided active or exact
leading-`@agent` target header. With Message details Off, that header contains
only the full target display name and direct replies avoid execution-profile
lookup and metadata. On adds backend-authoritative provider/model/effort facts
and confirmation to the live header, plus a compact Provider / Model / Effort
and Thread row beneath replies. Exceptional pending and launch-override state
stays textual. `/agents` and `/status` retain full sources and confirmation in
both modes; model reply prose
is never used to infer execution identity.

The generic picker foundation is also implemented entirely inside the terminal
package. It keeps opaque choice identities out of rendered text, separates
selection from highlight, exposes at most seven choices in a live viewport,
and uses a complete numbered list with exact retry grammar in line fallback.
Its resource-free adapter binds these pure controls and renderers to the sole
driver without history or completion state. Global `/settings` consumes the
picker. A submitted omitted resume command consumes it only after a fresh
lifecycle/catalog read succeeds with eligible rows; empty and failed reads
produce the applicable result or error without a picker. Capable streams render
an opened picker live and transient, while line fallback renders the numbered
form. Dynamic pre-Enter choice commit instead submits an explicit identity and
bypasses the picker. Settings also uses the internal cancellable-prompt seam.
None is supported public API.

The ranged text viewer is another unlisted internal seam, but it is independent
from the picker and composer. Its adapter accepts exact caller-supplied text,
one display name, and an optional one-based initial line. Private pure modules
own immutable document/range/viewport state, independent source/line/complete-
fallback capacities, bounded-prefix live rendering, and untruncated accepted
fallback rendering. One input-lifecycle leaf admits byte-backed live input only
for registry-resolved canonical ASCII, UTF-8, and ISO-8859-1 codecs with the
built-in strict or surrogateescape handler, retains the exact byte source and
decoder across every input route, and irreversibly selects live or fallback on
the first actual read. The sole driver owns Shift+Arrow decoding, exact
configured-control classification, bounded width/height observation, raw mode,
one alternate screen, and cleanup. The result contains only `selected`,
`cancelled`, `interrupted`, or `eof` plus an inclusive tuple for selection.
Selection and cancellation close the viewer after cleanup; every call drops its
source, display, anchor, selection, and viewport state on return or failure.

The installed controller now supplies that caller boundary. It projects only
the cursor-active provider-text span, lists and securely reads current safe
reference sources through the writing-artifact repository, enforces its
separate limit, calls the stateless viewer once per ordered selection, attaches
the path to returned line numbers, and restores the immutable originating
draft/cursor. Exact `$$` projects ranged-source choices on the second dollar;
the input controls own the nonmodal highlight and the driver converts
Enter/Tab into viewer entry. The private activation leaf owns only pure marker
interpretation, correlation, and edit results. Normal submission still crosses
the parser/runtime boundary exactly once, where mandatory per-agent reads
apply. The viewer itself continues to discover no file and construct no
reference.

The agent-exchange JSONL viewer is a different unlisted integration. The
controller owns inventory, subject, scope, agent, optional-package, and snapshot
policy. It passes exact immutable bytes plus generic header/search fields to the
external package. `InteractiveExternalViewer` retains only a call capability;
`_external_view` defines the Story-neutral host/event boundary; the sole driver
owns raw and alternate-screen resources. The hosted renderer cannot open Story
files, import Story, call a provider, or retain shell focus. Closing drops every
picker and viewer-local state and restores the prior shell input route.

Every capable main, subject, composition, confirmation, picker, or prompt read
clears its complete live frame before returning on submit, commit, cancel, interrupt,
or EOF and attempts the same cleanup before propagating a failure. Static
completion-only Enter remains inside the read. Plain
fallback retains printed prompt/input and adds no synthetic final control frame.
Persistent semantic cancellation, validation, discard, error, orientation, and
result output is written after that boundary and remains visible.
Conversation-subject input remains history- and completion-free. It uses the
same cleared live frame after the driver selects `LIVE`, or the ordinary
callback after `FALLBACK`; submitted subject semantics are unchanged.

The controller owns settings focus, semantic presentation, authentication,
adapter preflight, runtime quiescence, storage coordination,
persistence/activation order, and truthful compensation outcomes. It delegates
the call-local immutable baseline, pending candidate, persona catalog,
route-generation calculation, exact stale-state checks, deterministic prepared
writes, and rollback to the package-internal `interactive_settings` module.
Conversation-storage selection coordinates readiness, optional explicit
installation, provisioning, parity, and the complete desired-system-setting
guard under its lease.

Confirmed Apply Changes hot-applies runtime-affecting settings, including
execution profiles, to the current backend/runtime without a restart. A
Message details-only Apply uses prepare, verify, and commit without provider,
storage, backend, runtime, route, or thread activation. Desired storage is
persisted for the next shell. A Conversation-RAG-only Apply likewise commits
only system configuration, without authentication, storage readiness,
backend/runtime activation, route/thread work, composition, or optional-package
probing; the saved policy starts with the next shell. The current
controller/runtime storage and RAG bundles remain unchanged. Launch overrides
remain higher precedence and read-only. Direct disk edits have no watcher and
are reconciled only by a newly launched backend; other running shells retain
their captured state.

The core gives immutable facts to policy validators and receives validated
values, typed failures/errors, actions, or safe summaries. Leaves retain
nothing, perform no I/O, and have no sibling or core back-reference.

```mermaid
flowchart LR
    accTitle: Interactive conversation ownership
    accDescr: One startup-selected storage bundle supplies the controller, runtime, and current-session memory service; runtime alone authorizes provider-requested reads.
    S[Active storage services bundle] --> C
    S --> R
    S --> M
    C[Stateful controller core] --> R[Retained StoryRuntime]
    C --> M[Current-session memory service]
    C --> K[Conversation catalog]
    R -->|authorized search and read| M
    R --> B[Agent backend]
    R --> E[One shared event store]
    M --> E
    M --> K
```

The CLI resolves one filesystem or SQLite storage bundle before provider
authentication and injects that immutable active bundle through the core,
retained runtime, and attachment-scoped memory. Direct Python runtime
construction still defaults to a filesystem bundle unless its caller supplies
another complete bundle. Runtime alone authorizes provider-requested
search/read; backend receives only the handler and framed observations. The
core builds destination memory before session switch commit and clears it on
pause, abort, or cleanup. Direct search/transcript stays local. The core reads
the active event store only for history/status; it never appends, calls
`read_context`, or calls a bound agent. Lower layers retain storage, runtime,
terminal, session, thread, and provider ownership.

## Dependency direction

Controller dependency is exactly facade → stateful core → three leaves. Leaves
depend only on lower-layer contracts. Lifecycle consumers use canonical
`session_lifecycle`; `models` does not forward its objects. The runtime-policy
leaf returns frozen validated inspection facts to the core and does not format
terminal text.

CLI constructs one unified stream-backed terminal before controller
construction. The controller core imports the terminal facade's unlisted
component identities and resource-free semantic adapter; its policy leaves use
facade error contracts rather than terminal package leaves. Terminal core
depends on its pure controls, renderer, model, sole driver, the driver's
one-way input-lifecycle leaf, and canonical command/agent metadata. It binds
static completion plus a call-local controller
choice projection and palette renderer, then composition and empty confirmation
preview providers per read, without retaining prompt mode. Main reads use the
driver's pure selectable
palette/history transition; composition, confirmation, and subject use
history-free semantic input views with call-local injected renderers. The
package-internal picker and cancellable-prompt adapters
likewise supply their transitions and live/fallback renderers call-locally to
the sole driver. The controller uses those seams for `/settings` and for a
submitted omitted resume command whose fresh read returns eligible rows; it
short-circuits empty and failed reads before picker input. It calls
`interactive_settings` directly for validation and persistence; that module
depends only on agents, configuration, errors, and workspace. The controller
sends one typed component per established
callback through the adapter and otherwise depends downward on runtime,
presentation, and storage. Retrieval's handler port prevents
backend-to-orchestrator access.

The viewer implementation path flows only from the terminal facade/core to
`_viewer_controls`, `_viewer_model`, `_viewer_render`, and the existing driver.
Viewer controls and rendering depend only on terminal models; the driver
depends on the viewer model for typed state and results. The sibling private
`_reference_activation` leaf depends on terminal models plus immutable
writing-artifact reference/parser contracts. The controller alone bridges
call-local provider-span, catalog, and verified-text callbacks to terminal
input; the viewer leaves still have no controller, workspace, repository,
runtime, backend, provider, parser, or composer dependency.

The separate agent-exchange view path flows from controller core to pure
`_exchange_policy`, durable `agent_exchanges` facade contracts, and the lazy
`_exchange_viewer` adapter. That adapter alone imports the optional external
package and prepares a callable against the terminal package's private
`_external_view` host protocol. Terminal core and driver consume only that
generic callable; they import no exchange schema or controller policy. The
external package has no dependency back to Story, so the direction remains
acyclic and its pin is packaging metadata rather than a vendored import path.

This supports deterministic collaborators, not a dynamic plugin system. Exact
imports live in [Package API](../reference/package-api.md), lifecycle meaning in
[Session lifecycle](session-lifecycle.md), and conversation contracts in
[Conversation search](../reference/conversation-search.md),
[Conversation transcripts](../reference/conversation-transcripts.md), and
[Bounded retrieval](../reference/conversation-retrieval.md).
