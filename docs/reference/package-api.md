# Package API and exports

> **Availability:** the 120-name inventory below is exported from the
> implemented `story_writing_agents` package facade. Separate sections document
> archive additions to the supported `story_writing_agents.conversation_storage`
> subpackage, pure literal-search mechanics, and the optional SQLite owning
> modules plus direct-only conversation migration, provisioning, and selection;
> optional conversation RAG also uses five supported direct modules, with the
> generic adapter available only through its pinned extra. None is a
> package-root facade export. Context and progress types are Python
> APIs; an export does not imply a CLI command.

Key terms: **package facade**—the names re-exported at the package root;
**public import**—a supported symbol imported from its documented owning
package; **alias**—a second name for the same object; **enum**—a type with a
fixed value set.

Import public contracts from the package root:

```python
from story_writing_agents import StoryRuntime, Workspace
```

The exact 120-name export inventory is:

- roster: `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`, `LEAD_ORCHESTRATOR`,
  `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`, `EXECUTION_SPECIALIST`;
- backend: `AgentBackend`, `BoundAgent`;
- session lifecycle: `SessionDurableState`, `SessionAccessState`,
  `SessionLifecycleState`, and `provider_work_is_ready`;
- agent, conversation-turn, and debate models: `PromptFileReference`,
  `AgentDefinition`, `AgentRequest`, `AgentReply`, `ConversationRequest`,
  `ConversationReply`, `ConversationResult`, `AgentStateEntry`,
  `DebateMessage`, `DebateResult`, `InputKind`, `Phase`, `Verdict`,
  `DebateStatus`, `DebateContextMode`, `DebateContextRequest`,
  `DebateFailureKind`, `DebateProgressPhase`, `DebateProgressEvent`, and
  `DebateProgressSink`;
- runtime: `StoryRuntime`, `RuntimeSession`, `Runtime`, `Session`;
- directives: `DirectiveKind`, `RuntimeDirective`, `parse_directive`,
  `apply_directive`;
- workspace: `Workspace`, `initialize_workspace`,
  `FilesystemConversationCatalog`, `FilesystemConversationEventStore`;
- conversation catalog: `ConversationCatalog`, `ConversationMetadata`,
  `ConversationStatus`, `normalize_conversation_subject`,
  `CONVERSATION_METADATA_SCHEMA_VERSION`,
  `CONVERSATION_SUBJECT_NORMALIZATION_VERSION`, and
  `MAX_CONVERSATION_SUBJECT_LENGTH`;
- conversation transcripts: `ConversationEventStore`, `ConversationEvent`,
  `ConversationEventAppend`, `ConversationEventKind`,
  `ConversationContextPolicy`, `ConversationContextWindow`,
  `CONVERSATION_EVENT_SCHEMA_VERSION`, and `CONVERSATION_CONTEXT_POLICY`;
- conversation search and cited reads: `ConversationMemoryPolicy`,
  `CONVERSATION_MEMORY_POLICY`, `ConversationSearchScope`,
  `ConversationSearchQuery`, `ConversationSearchMatchKind`,
  `ConversationCitation`, `ConversationSearchHit`, `ConversationSearchResult`,
  `ConversationSearch`, `ReferenceConversationSearch`,
  `ConversationMemoryService`, `ConversationTranscriptCursor`, and
  `ConversationTranscriptPage`;
- bounded retrieval: `CONVERSATION_RETRIEVAL_POLICY`,
  `ConversationRetrievalPolicy`, `ConversationRetrievalActionKind`,
  `ConversationRetrievalAction`, `ConversationRetrievalSearchAction`,
  `ConversationRetrievalReadAction`, `ConversationRetrievalAnswerAction`,
  `ConversationRetrievalObservation`, `ConversationRetrievalSearchObservation`,
  `ConversationRetrievalReadObservation`, `ConversationRetrievalSearchItem`,
  `ConversationRetrievalTranscriptEvent`, `ConversationRetrievalSource`,
  `ConversationRetrievalRequest`, `ConversationRetrievalReply`,
  `ConversationRetrievalResult`, `ConversationRetrievalActionHandler`,
  `ConversationRetrievalProgressPhase`, `ConversationRetrievalProgressEvent`,
  and `ConversationRetrievalProgressSink`;
- interactive parser: `ActionKind`, `InteractiveAction`, `CommandSpec`,
  `COMMANDS`, `parse_interactive_line`;
- errors: `StoryWritingAgentsError`, `WorkspaceSecurityError`,
  `WorkspaceFormatError`, `WorkspaceVersionUnsupportedError`,
  `ConfigurationError`, `DirectiveError`,
  `SessionIdError`, `ConversationNotFoundError`,
  `ConversationExistsError`, `ManuscriptExistsError`,
  `BackendProtocolError`, `SessionExistsError`, `SessionNotFoundError`,
  `SessionPausedError`, `SessionAccessBlockedError`,
  `SessionOwnershipUnknownError`, `DebateExistsError`, `ProgressObserverError`,
  `ConversationRetrievalError`, `ConversationRetrievalSemanticError`,
  `ConversationRetrievalBudgetError`, and `ConversationRetrievalObserverError`.

`Runtime` is exactly an alias of `StoryRuntime`; `Session` is exactly an alias
of `RuntimeSession`. They do not provide different behavior.

Canonical artifact value contracts live in
`story_writing_agents.writing_artifacts`, but are intentionally absent from the
120-name package-root facade. In addition to immutable path, catalog, kind,
reference, and filesystem `ArtifactSourceIdentity` values, the subpackage
exports content-free authorization, operation
history/state, read-request/action, `ReferenceOperationFailure`, and parser
contracts. Filesystem-backed `WritingArtifactRepository`, content-bearing read
results, private capabilities, runtime coordinators, the ranged viewer, and
interactive activation values remain internal. The installed controller uses
that private UI boundary for `$`/`$$` prompt editing without adding a public
selection API or standalone artifact command. See
[Canonical writing artifacts](writing-artifacts.md).

<a id="conversation-literal-search-mechanics"></a>

## Conversation literal-search mechanics

Provider-neutral literal-search mechanics are a supported direct-module API,
not part of the 120-name package-root or 64-name conversation-storage facades.
The module is pure, owns no resource, performs no I/O, and exports exactly:

```python
from story_writing_agents.conversation_literal_search import (
    CONVERSATION_LITERAL_NORMALIZATION_VERSION,
    build_conversation_literal_snippet,
    classify_conversation_literal_subject,
    conversation_literal_sort_key,
    normalize_conversation_literal_query,
    normalize_conversation_literal_text,
)
```

The normalization version is exactly `1`. Public call signatures are:

```python
normalize_conversation_literal_text(value: str) -> str
normalize_conversation_literal_query(
    value: str,
    *,
    max_characters: int,
) -> str
classify_conversation_literal_subject(
    normalized_subject: str,
    normalized_query: str,
) -> str | None
build_conversation_literal_snippet(
    searchable: str,
    normalized_query: str,
    *,
    max_characters: int,
    max_utf8_bytes: int,
) -> str
conversation_literal_sort_key(
    match_kind: str,
    normalized_subject: str,
    conversation_id: str,
) -> tuple[int, str, str]
```

Normalization is strict-UTF-8 NFKC plus case folding, replacement of every
Unicode category-C character with a space, and whitespace collapse. Query
normalization additionally rejects raw category-C characters and enforces
the caller's exact positive character bound before and after normalization.
Subject classification returns `exact_subject`, `subject_prefix`,
`subject_substring`, or `None`; the snippet and sort helpers implement the
same deterministic public behavior used by both reference and SQLite search.
See [Conversation search](conversation-search.md#search-contract).

<a id="optional-conversation-rag-boundary"></a>

## Optional conversation RAG boundary

> **Availability:** these are direct Python APIs. Issues #262/#283 provide the
> contracts, deterministic local provider, disposable generation store, generic
> adapter, and explicit local composition. They are not connected to
> `StoryRuntime`, authoritative conversation stores, agents, the CLI, `/rag`,
> or `/settings`; existing literal retrieval and `/search` are unchanged.

Provider-neutral story contracts live in
`story_writing_agents.conversation_rag`. The module imports only the standard
library, performs no I/O, and is safe in the RAG-free base installation. Its
exact 35-name `__all__` is:

- policy and outcomes: `CONVERSATION_RAG_POLICY`, `ConversationRagPolicy`,
  `ConversationRagOutcome`, `ConversationRagReason`,
  `ConversationRagProjectionStateAvailability`,
  `ConversationRagProjectionStatus`, `ConversationRagProjectionOutcome`, and
  `ConversationRagProjectionFailureStage`;
- source and vector values: `ConversationRagDocumentKey`,
  `ConversationRagDocumentIdentity`, `ConversationRagSourceDocument`,
  `ConversationRagSourceReference`, `ConversationRagEmbeddingIdentity`,
  `ConversationRagEmbeddingVector`, `ConversationRagProviderCandidate`, and
  `ConversationRagVectorRecord`;
- state, request, and result values:
  `ConversationRagProjectionCheckpointEntry`,
  `ConversationRagProjectionCheckpoint`, `ConversationRagProjectionState`,
  `ConversationRagProjectionRequest`, `ConversationRagProjectionResult`,
  `ConversationRagRetrievalRequest`, `ConversationRagCandidate`, and
  `ConversationRagRetrievalResult`;
- caller-owned ports: `ConversationRagEmbedder`,
  `ConversationRagVectorIndexWriter`, `ConversationRagVectorIndexResetter`,
  `ConversationRagVectorIndexReader`, `ConversationRagLexicalRetriever`,
  `ConversationRagCollaborators`, and `ConversationRagService`; and
- failures: `ConversationRagError`, `ConversationRagContractError`,
  `ConversationRagStateError`, and `ConversationRagOperationError`.

The fixed policy is:

| Field | Exact value |
| --- | ---: |
| `max_candidates` | `20` |
| `max_conversations` | `256` |
| `max_events` | `10_000` |
| `max_fragment_codepoints` | `800` |
| `fragment_overlap_codepoints` | `80` |
| `max_embedding_batch_size` | `32` |

`ConversationRagService` has four positional-only operations:

```python
project_documents(request, state) -> ConversationRagProjectionResult
rebuild_projection(request, state) -> ConversationRagProjectionResult
retrieve_semantic(request, state) -> ConversationRagRetrievalResult
retrieve_hybrid(request, state) -> ConversationRagRetrievalResult
```

Projection input can contain authorized source text, but public retrieval
output does not. `ConversationRagCandidate` carries only a stable source
reference and contiguous rank. Provider fragment text is non-authoritative
adapter input and is dropped after identity/range validation. A future runtime
must reauthorize and reread the current range before display or agent use.

The exact generic translation surface requires the optional package and
exports three names:

```python
from story_writing_agents.generic_rag_adapter import (
    GenericRagCapabilitySnapshot,
    GenericRagConversationService,
    inspect_generic_rag_capabilities,
)
```

`generic_rag_adapter` is the sole production module importing both projects.
It maps to the exact `generic-rag` 0.1.0 owning-module API, borrows caller-owned
collaborators, validates returned types and stable identities, removes provider
text from story results, and translates ordinary failures to content-free story
errors. It does not acquire, close, discover, or persist collaborators. Process
control propagates.

The deterministic local provider is a separate pure direct module with exactly
five exports:

```python
from story_writing_agents.conversation_rag_local import (
    LOCAL_CONVERSATION_RAG_DIMENSIONS,
    LOCAL_CONVERSATION_RAG_MODEL_ID,
    LOCAL_CONVERSATION_RAG_PROVIDER_ID,
    LocalConversationRagEmbedder,
    LocalConversationRagVectorRanker,
)
```

The constants are exactly `256`, `"story-conversation-rag-local-hash-v1"`, and
`"local"`. `LocalConversationRagEmbedder.identity` returns that fixed model and
dimension pair. Its public operations are:

```python
LocalConversationRagEmbedder.embed(
    texts: tuple[str, ...],
    /,
) -> tuple[ConversationRagEmbeddingVector, ...]
LocalConversationRagVectorRanker.rank(
    request: ConversationRagRetrievalRequest,
    embedding: ConversationRagEmbeddingVector,
    records: Iterable[ConversationRagVectorRecord],
    /,
) -> tuple[ConversationRagProviderCandidate, ...]
```

Embedding pins later codepoint assignments through Unicode 3.2 classification,
uses shared strict normalized text, hashes whole tokens and boundary-padded
codepoint 1/2/3-grams through a fixed SHA-256 feature domain, and L2-normalizes
the 256 integer buckets. It accepts at most 32 texts of at most 800 codepoints
each. Ranking scans at most 65,536 exact local-dimension records, deduplicates
identical stable sources, rejects conflicts/cross-session records, retains only
positive similarity, orders ties by the full stable source key, and returns no
scores. Neither class owns state or performs I/O, network, credential,
subprocess, clock, randomness, locale, timezone, hash-seed, or environment
discovery. This intentionally weak deterministic baseline has no semantic-
quality guarantee.

Disposable persistence is supported from one identity-bound direct module with
exactly eight exports:

```python
from story_writing_agents.conversation_rag_store import (
    CONVERSATION_RAG_STORE_SCHEMA_ID,
    ConversationRagGenerationBuilder,
    ConversationRagGenerationStore,
    ConversationRagStoreCounts,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreSnapshot,
    ConversationRagStoreStatus,
)
```

`CONVERSATION_RAG_STORE_SCHEMA_ID` is exactly
`"story-conversation-rag-store-v1"` and versions only the persisted store
envelopes. It is not the projection-content schema. The caller owns
`ConversationRagProjectionRequest.schema_id`; the store preserves that value as
the generation's `projection_schema_id` and as the checkpoint's `schema_id`.
Status values are `missing`, `ready`, `stale`, `corrupt`, `incompatible`, and
`provider_failed`.
Failure values are `unsupported`, `busy`, `unsafe`, `corrupt`, `incompatible`,
`provider_failed`, `confirmation_required`, and `operation_failed`.
`ConversationRagStoreCounts` contains these exact nonnegative fields:

```text
conversations, events, documents, fragments, source_map_records,
vector_records, generations, orphan_generations, source_utf8_bytes,
payload_bytes, generation_bytes
```

The store constructs without I/O and retains the supplied identity-bound
`Workspace`. Its public operations are:

```python
ConversationRagGenerationStore(workspace: Workspace, /)
store.inspect(
    session_id: str,
    provider_id: str,
    embedding: ConversationRagEmbeddingIdentity,
    /,
    *,
    expected_projection_schema_id: str = CONVERSATION_RAG_STORE_SCHEMA_ID,
) -> ConversationRagStoreSnapshot
store.begin_generation(
    provider_id,
    request,
    state,
    /,
    *,
    rebuild: bool,
) -> ConversationRagGenerationBuilder
store.open_active_reader(snapshot, ranker, /) -> context manager
store.set_status(session_id, status, /) -> None
store.purge(session_id, /, *, confirmed: bool) -> ConversationRagStoreCounts
```

`expected_projection_schema_id` is additive and keyword-only. It must be an
exact nonblank, non-whitespace strict-UTF-8 string and is validated before any
store I/O. The default preserves the original fail-closed expectation for
generations whose projection schema was the store-v1 value. Callers using a
different projection format must supply its exact ID.

The snapshot's `status` describes compatibility with the provider, embedding,
and projection schema requested by this inspection; its `state` describes the
verified checkpoint itself. A completely verified generation with one of
those identity mismatches returns `incompatible` with a `present` checkpoint
and safe provider, generation, and count fields. An incompatible store envelope
or otherwise unverifiable identity exposes no trusted checkpoint. Advisory
`ready`, `stale`, or `provider_failed` state cannot mask an identity mismatch.

The builder is an operation-scoped context manager. Its `state` is the exact
store-loaded state; it implements `replace_document()`, `delete_document()`,
and `reset_session()` for the generic projection bridge, and
`publish(result, /)` completely verifies and commits a successful target.
Generation construction uses the request's projection schema. Incremental
projection requires a compatible active generation; an explicit rebuild can
replace a verified incompatible generation without relabeling it.
An active reader is likewise call-scoped and exposes `search(request,
embedding, /)` only while its exact snapshot remains current. Advisory status
accepts only `stale` or `provider_failed`. Purge requires exact
`confirmed=True` and removes only one session's derived active/status and
generation contents.

The store owns
`agent_states/orchestrator_state/conversation_rag/sessions/<session>/`, with
owner-only modes, descriptor-relative no-follow access, bounded POSIX locks,
canonical strict-UTF-8 JSON/JSONL, digest/count/range reconstruction, and
complete active-generation verification. `active.json` is the sole commit
record. Its atomic rename is the final publish operation after precommit file
and directory synchronization; no post-rename durability is claimed, so an
immediate crash can retain either the old or new valid pointer. A reported
precommit failure preserves the old pointer byte-for-byte. Rebuild restarts
from the beginning, inspection does not clean, and later mutation removes only
validated orphans. Deep/noncanonical JSON, non-finite or overflowing numbers,
and inconsistent persisted records become sanitized corruption. Public
snapshots/errors/status contain no query or source text, although the private
derived source-map file contains plaintext fragments. See
[Persistence](../design/persistence.md#optional-conversation-rag-derived-persistence)
and [Filesystem safety](../operations/filesystem-safety.md#conversation-rag-generation-store-safety).

Deferred composition is safe to import without `generic-rag` and exports six
names:

```python
from story_writing_agents.conversation_rag_composition import (
    CONVERSATION_RAG_PROVIDER_ALLOWLIST,
    ConversationRagCompositionError,
    ConversationRagCompositionFailure,
    LocalConversationRagComposition,
    compose_conversation_rag,
    compose_local_conversation_rag,
)
```

The allowlist is exactly `("local",)`. Calling
`compose_conversation_rag(provider, collaborators, /)` validates that value,
requires installed distribution version `0.1.0`, performs the deferred static
adapter import, verifies its capability manifest, and constructs the service.
It does not dynamically discover providers or inspect environment
configuration. Sanitized failure values are `provider_unsupported`,
`dependency_missing`, `version_incompatible`, and
`capability_incompatible`; dependency details do not cross the error boundary.

`compose_local_conversation_rag(store, lexical_retriever, /)` validates the
same optional dependency immediately and returns
`LocalConversationRagComposition`. Its `service` property is the composition
itself and `store` is the retained caller-owned generation store. The fixed
embedder/ranker and supplied lexical retriever are combined with a fresh
call-local generic adapter for every projection or retrieval that reaches
generic work. Successful projection publishes one fully verified inactive
generation. Retrieval with a present caller state inspects against that
checkpoint's projection schema, then opens only the exact verified active
snapshot. A different stored schema therefore returns `literal_fallback` with
reason `incompatible`; drift from the same-schema caller checkpoint returns
reason `stale`. Missing, stale, corrupt, provider-failed, or failed
storage/provider state also returns `literal_fallback` with a fixed content-free
reason. A missing or incompatible optional package at initial composition
raises the sanitized composition error before store I/O.

These modules implement only direct local provider and disposable derived-state
behavior. They do not authorize sources, schedule projection, reread or cite
authoritative content, inject agent context, or implement the user interface.
Installation, immutable-pin verification, caller ownership, the redistribution
restriction, persistence boundary, and planned agent/user flow are in
[Optional conversation RAG](../guides/conversation-rag.md).

<a id="conversation-storage-subpackage"></a>

## Conversation-storage subpackage

Canonical conversation archive values, deterministic serialization, and the
privileged filesystem archive adapter are supported from
`story_writing_agents.conversation_storage`, not from the 120-name package-root
facade. The subpackage has an exact 64-name `__all__`; the archive addition is
the archive module's 22-name surface plus the filesystem adapter's two-name
surface:

```python
from story_writing_agents.conversation_storage import (
    ConversationArchive,
    FilesystemConversationArchiveAdapter,
    compose_filesystem_conversation_archive,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)
```

These imports add no raw archive CLI command and perform no scan or mutation.
The separate storage migration CLI composes archive adapters rather than
serializing caller-owned archive bytes. Exact
constants, value fields, callable signatures, error hierarchy, bounds, wire
format, lease usage, and filesystem behavior are in
[Canonical conversation archives](conversation-archives.md#public-archive-api).

The filesystem adapter captures a private immutable root/key rather than
retaining the caller's `Workspace` or public catalog/event-store collaborators.
After pure payload validation where applicable, each call validates its lease
before creating one private operation-local collaborator set. This archive
topology is independent of composed conversation services and SQLite's database
owner; it changes neither root-facade exports nor ordinary runtime defaults.

The export inventory and public signatures are unchanged by runtime diagnostics.
Public `AgentBackend` and `StoryRuntime` operations use an internal
workspace-bound writer and may append bounded operational records, including
for reads that do not mutate provider-session, conversation, debate, or story
state. No debug-log class, enum, configuration, or telemetry surface is exported
from the package root. See [Runtime API](runtime-api.md#runtime-diagnostic-side-effect),
[Backend and sessions](backend-and-sessions.md#backend-diagnostic-side-effect),
and [Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

<a id="conversation-storage-composition"></a>

## Conversation-storage composition

The conversation-storage composition APIs are supported direct-subpackage
imports; they are not re-exported by the 120-name project facade:

```python
from story_writing_agents.conversation_storage import (
    WorkspaceConversationStorageMutationCoordinator,
    compare_and_set_conversation_storage_activation,
    compose_filesystem_conversation_storage,
    load_conversation_storage_activation_state,
)
```

`compose_filesystem_conversation_storage(workspace)` returns one
`ConversationStorageServices` bundle whose catalog, event store, reference
search, provenance token, and mutation coordinator derive from the same
construction-pinned canonical absolute root. Its services remain concrete
`FilesystemConversationCatalog`, `FilesystemConversationEventStore`, and
`ReferenceConversationSearch` instances. Later caller-root mutation or
inherited collaborator rebinding cannot redirect their I/O. Composition with
an existing `Workspace` performs no conversation-storage I/O, creates no
guard, and writes no activation state. A path-like argument still invokes
ordinary `Workspace` construction.

That pinning does not change standalone construction. A directly instantiated
`FilesystemConversationCatalog(workspace)` or
`FilesystemConversationEventStore(workspace)` retains the established borrowed
workspace behavior, and `Workspace` itself remains mutable and pathname-based.
The optional SQLite adapter has its own separately pinned database owner.

`WorkspaceConversationStorageMutationCoordinator(workspace)` likewise captures
its root without I/O. Its key, the guard lazily opened by an outer `borrow`, and
every lease identify that root despite later caller mutation.

Activation functions pin one root per call. Compare-and-set validates its lease
against that root before constructing an operation workspace or accessing a
path, so a mismatch raises `ConversationStorageCoordinationError` without
filesystem I/O. A valid call reads state, applies the existing checks,
revalidates the lease, and writes only through that root. Desired selection and
coordinated startup are owned by the separate direct module below; these
low-level functions do not read configuration or select a backend themselves.

Composed filesystem catalog mutations and event append borrow the bundle's
coordinator. Direct standalone filesystem adapters retain their established
uncoordinated compatibility behavior, so callers must stop or otherwise
coordinate those low-level writers around migration, selection, and startup.

See [Filesystem safety](../operations/filesystem-safety.md#composed-conversation-storage-provenance)
for the operational boundary.

<a id="conversation-storage-migration-api"></a>

## Conversation storage migration

Provider-free migration and exact parity verification are supported from one
direct-only module. Its seven names are absent from both the 120-name package
root and 64-name conversation-storage facade:

```python
from story_writing_agents.conversation_storage.migration import (
    CONVERSATION_STORAGE_MIGRATION_SCHEMA_VERSION,
    CONVERSATION_STORAGE_MIGRATION_CHECKPOINT_DIRECTORY,
    ConversationStorageMigrationOperation,
    ConversationStorageMigrationOutcome,
    ConversationStorageMigrationReport,
    migrate_conversation_storage,
    verify_conversation_storage,
)
```

The schema version is exactly `1`; the checkpoint directory is exactly
`"agent_states/orchestrator_state/conversation_storage_migrations"`.
Importing this module performs no I/O and does not load `sqlite3`. Its public
workflow signatures are:

```python
migrate_conversation_storage(
    workspace: Workspace,
    *,
    source_backend: ConversationStorageBackend | str,
    destination_backend: ConversationStorageBackend | str,
    dry_run: bool = False,
) -> ConversationStorageMigrationReport
verify_conversation_storage(
    workspace: Workspace,
    *,
    source_backend: ConversationStorageBackend | str,
    destination_backend: ConversationStorageBackend | str,
) -> ConversationStorageMigrationReport
```

Backend strings are exactly `filesystem` and `sqlite`. Source and destination
must differ, the source must equal the current activation state, and SQLite
must already be provisioned/ready. One outer workspace mutation lease covers
fresh source/destination archive exports, deterministic planning, transfer,
checkpointing, and fresh final exact parity. The operations preserve all
canonical metadata and events, including archived records and runtime errors;
they exclude FTS and every non-conversation workspace channel. They never
install/provision SQLite, change configuration or activation, delete a source,
or establish dual write.

`ConversationStorageMigrationOperation` values are `migrate` and `verify`;
`ConversationStorageMigrationOutcome` values are `completed`, `work_required`,
and `failed`. The frozen report fields are:

```text
operation, outcome, source_backend, destination_backend, dry_run, parity,
create, identical, conflict, unsupported, destination_only,
source_conversations, source_events, checkpoint, failure
```

`to_dict()` returns the fixed nested schema-v1 object, `to_json()` returns its
compact key-sorted representation without a trailing LF, and `exit_code`
returns `0`, `1`, or `2` under the CLI contract. `checkpoint` is `none`,
`resumed`, `advanced`, or `completed`; `failure` is `None` on nonfailed
reports. The workflow catches content/storage/coordination failures into a
content-free report; invalid Python argument types or backend values rejected
before workflow entry, and process-control exceptions, retain their ordinary
exception behavior.

Migration plans every identity as create, identical, conflict, unsupported, or
destination-only. Conflict and unsupported data block all operations;
destination-only blocks migration, while otherwise valid create or
destination-only state makes verify return mismatch. Filesystem-to-SQLite
transfer is one complete transaction. SQLite-to-filesystem uses a complete
global preflight, create-only complete-conversation publication, and a
per-conversation checkpoint callback. Final source and destination exports must
equal the initial source bytes.

Use the public CLI for the installed workflow and the direct functions when an
application already owns a `Workspace`; neither route activates the destination.
See the [exact command/report contract](cli.md#conversation-storage-migration),
[archive semantics](conversation-archives.md#conversation-storage-migration),
and [checkpoint recovery](../operations/recovery-and-limitations.md#conversation-storage-migration-recovery).

<a id="conversation-storage-selection-api"></a>

## Conversation storage provisioning and selection

Host/database preparation and desired-selection/startup coordination are two
separate supported direct-module surfaces. Neither is re-exported by the
120-name root or 64-name conversation-storage facade, and importing either
module performs no I/O, launches no process, or loads `sqlite3`.

Provisioning exports exactly four names:

```python
from story_writing_agents.conversation_storage.provisioning import (
    ConversationStorageInstallationPlan,
    ConversationStorageProvisioningFailure,
    ConversationStorageProvisioningResult,
    ConversationStorageProvisioner,
)
```

`ConversationStorageInstallationPlan.argv` is exactly one allowlisted tuple;
its `command` property is the literal display form. The provisioner exposes:

```python
ConversationStorageProvisioner(
    *,
    platform_system=platform.system,
    os_release_reader=_read_os_release,
    environment: Mapping[str, str] | None = None,
    executable_lookup=shutil.which,
    process_runner=subprocess.run,
)
ConversationStorageProvisioner.probe_runtime() -> ConversationStoragePreflight
ConversationStorageProvisioner.plan_installation() -> ConversationStorageInstallationPlan | None
ConversationStorageProvisioner.install(
    workspace: Workspace,
    plan: ConversationStorageInstallationPlan,
    *,
    diagnostic_stream: TextIO,
) -> ConversationStorageProvisioningResult
ConversationStorageProvisioner.ensure_ready(
    workspace: Workspace,
    *,
    lease: ConversationStorageMutationLease,
) -> ConversationStorageProvisioningResult
```

The constructor collaborators are deterministic integration/test seams, not an
expanded installer policy. Planning recognizes only the exact platforms and
commands in the [CLI contract](cli.md#conversation-storage-selection).
`installation_disallowed` reports whether a recognized nonempty CI marker is
present. Installation runs one exact tuple without a shell, custom environment,
timeout, retry, privilege escalation, or automatic flag. `ensure_ready()`
preflights and, only when absent, provisions one empty database under the
caller's lease; it never installs host packages or migrates conversations.

Selection exports exactly seven names:

```python
from story_writing_agents.conversation_storage.selection import (
    CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION,
    ConversationStorageSelectionFailure,
    ConversationStorageSelectionReport,
    ConversationStorageStartup,
    activate_configured_conversation_storage,
    inspect_conversation_storage_parity,
    select_conversation_storage,
)
```

The schema version is `1`. Its public workflow signatures are:

```python
inspect_conversation_storage_parity(
    workspace: Workspace,
    *,
    lease: ConversationStorageMutationLease,
    source_backend: ConversationStorageBackend | str,
    target_backend: ConversationStorageBackend | str,
    source_generation: int,
) -> ConversationStorageMigrationReadiness
select_conversation_storage(
    workspace: Workspace,
    desired_backend: ConversationStorageBackend | str,
    *,
    install_missing: bool = False,
    yes: bool = False,
    installation_consent: Callable[[ConversationStorageInstallationPlan], bool] | None = None,
    diagnostic_stream: TextIO | None = None,
    configuration_baseline: ConfigurationBaseline | None = None,
    configuration: ConfigurationSnapshot | None = None,
    precommit_guard: Callable[[], None] | None = None,
    provisioner: ConversationStorageProvisioner | None = None,
    mutation_timeout_seconds: float = 30.0,
) -> ConversationStorageSelectionReport
activate_configured_conversation_storage(
    workspace: Workspace,
    *,
    configuration: ConfigurationSnapshot | None = None,
    mutation_timeout_seconds: float = 30.0,
) -> ConversationStorageStartup
```

`select_conversation_storage()` prepares readiness, optionally obtains explicit
installation consent, provisions an absent empty SQLite database, freshly
compares canonical archives, and performs the final exact-byte configuration
CAS. It saves desired state only. `configuration_baseline` and `configuration`
must be supplied together; the Settings integration uses them for its complete
pending pair and uses `precommit_guard` for the settings-owned fresh persona
catalog conflict check. Any guard failure becomes the content-free
`configuration_conflict` report before the CAS.

`ConversationStorageSelectionReport.to_dict()` returns the exact counts-free
schema-v1 object, `to_json()` returns compact key-sorted JSON without a trailing
LF, and `exit_code` is `0` or `2`. The complete fields, state and failure
vocabularies, installer mappings, command workflow, and privacy boundary are
centralized in the [CLI selection reference](cli.md#conversation-storage-selection).

`activate_configured_conversation_storage()` is the next-shell owner. Under one
outer lease it reloads configuration, composes the active bundle, proves fresh
target readiness/parity, composes the target bundle, and compare-and-sets the
activation marker. `ConversationStorageStartup` binds that complete services
bundle to its activation outcome. Pre-CAS failure preserves activation and
does not fall back; later unrelated shell failure does not undo a successful
CAS. Neither workflow imports migration, consumes its report/checkpoint, deletes
a source, or establishes live switching or dual write.

<a id="optional-sqlite-conversation-storage"></a>

## Optional SQLite conversation storage

SQLite conversation storage is an explicit direct-module Python API. Importing
the module performs no I/O and does not load `sqlite3`; the standard-library
module is loaded and probed only when an operation is requested. SQLite 3.7.16
or later is required. Each adapter's database owner reads the canonical
`Workspace.root` once at construction and pins its workspace key and fixed
database path. Later mutation of the supplied `Workspace.root` does not
retarget database operations. Direct adapter construction performs no database
I/O and keeps `sqlite3` lazy; resolving a string or path-like argument still
follows ordinary `Workspace` construction. The `compose_*` functions perform
their documented readiness preflight.

The owning module exports exactly 14 names:

```python
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_APPLICATION_ID,
    SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS,
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION,
    SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION,
    SQLiteConversationArchiveAdapter,
    SQLiteConversationCatalog,
    SQLiteConversationEventStore,
    SQLiteConversationSearch,
    compose_sqlite_conversation_archive,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    probe_sqlite_conversation_storage_runtime,
    provision_sqlite_conversation_storage,
)
```

The constants are exact:

| Constant | Value |
| --- | --- |
| `SQLITE_CONVERSATION_STORAGE_APPLICATION_ID` | `0x53574143` (`SWAC`) |
| `SQLITE_CONVERSATION_STORAGE_BUSY_TIMEOUT_SECONDS` | `5.0` |
| `SQLITE_CONVERSATION_STORAGE_DATABASE_PATH` | `"discussion_logs/conversation_storage.sqlite3"` |
| `SQLITE_CONVERSATION_STORAGE_MINIMUM_VERSION` | `(3, 7, 16)` |
| `SQLITE_CONVERSATION_STORAGE_SCHEMA_VERSION` | `1` |

Schema v1 owns exactly ten explicit objects: three tables, four indexes, and
three insert triggers. Ordered event reads use the event table's composite
primary-key autoindex, not a redundant explicit event-order index. Addressed
turn lookup uses the unique turn-slot index; bounded context uses the
recent-outcome index. Required authoritative lexical state consists of
canonical normalized subjects, canonical searchable event content, and one
nonnegative generation. FTS and its shadow objects never enter the main
database.

The public call signatures are:

```python
SQLiteConversationCatalog(
    workspace: Workspace | str | os.PathLike[str],
    *,
    mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
    mutation_timeout_seconds: float = 5.0,
)
SQLiteConversationEventStore(
    workspace: Workspace | str | os.PathLike[str],
    *,
    mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
    mutation_timeout_seconds: float = 5.0,
)
SQLiteConversationSearch(
    workspace: Workspace | str | os.PathLike[str],
    *,
    mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
    mutation_timeout_seconds: float = 5.0,
)
SQLiteConversationSearch.search(
    scope: ConversationSearchScope,
    query: ConversationSearchQuery,
) -> ConversationSearchResult
SQLiteConversationArchiveAdapter(
    workspace: Workspace | str | os.PathLike[str],
    *,
    mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
    mutation_timeout_seconds: float = 5.0,
)
SQLiteConversationArchiveAdapter.export_archive(
    lease: ConversationStorageMutationLease,
) -> ConversationArchive
SQLiteConversationArchiveAdapter.import_archive(
    lease: ConversationStorageMutationLease,
    payload: ConversationArchive,
) -> ConversationArchiveImportReceipt
preflight_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
) -> ConversationStoragePreflight
probe_sqlite_conversation_storage_runtime() -> ConversationStoragePreflight
provision_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    lease: ConversationStorageMutationLease,
) -> ConversationStoragePreflight
compose_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    mutation_timeout_seconds: float = 5.0,
) -> ConversationStorageServices
compose_sqlite_conversation_archive(
    workspace: Workspace | str | os.PathLike[str],
    /,
    *,
    mutation_timeout_seconds: float = 5.0,
) -> SQLiteConversationArchiveAdapter
```

The explicit runtime probe is observational and touches no workspace: it
functionally exercises SQLite through the active Python interpreter without a
standalone executable or FTS5 gate. Database preflight is also observational:
it never creates or repairs storage. On a capable host a missing database
reports `provisioning_needed`; an unavailable or too-old `sqlite3` runtime
reports `unavailable`; a valid database reports `ready`. Unsupported schema
versions, malformed storage, and unsafe paths raise
`WorkspaceVersionUnsupportedError`, `WorkspaceFormatError`, and
`WorkspaceSecurityError`. Calling `require_ready()` on a non-ready result
raises the corresponding conversation-storage readiness error. Every returned
preflight has `backend == ConversationStorageBackend.SQLITE`, and successful
provisioning returns `ready`. The private FTS companion is neither inspected nor
required for main-database readiness; provisioning may attempt its initialization
only after the authoritative database is ready.

The separately supported direct database-owner module now has 12 exports and
adds the lower-level equivalent probe:

```python
from story_writing_agents.conversation_storage.sqlite_database import (
    probe_sqlite_runtime,
)

probe_sqlite_runtime() -> None
```

The lower probe returns normally on readiness and raises its content-free
database-unavailable error otherwise. Use the adapter-level
`probe_sqlite_conversation_storage_runtime()` for a typed capability result
unless an integration already depends on the database-owner module.

Existing-database preflight is exhaustive: it verifies database/schema
identity, runs SQLite integrity and global foreign-key checks, rejects orphan
events, and reconstructs every metadata and event row. Ordinary catalog and
event-store calls use bounded identity verification plus operation-local row
validation instead. They do not certify unrelated database contents. Append
reads the indexed maximum sequence and at most the addressed turn's one user
row and one outcome row. `read_context()` obtains the exact complete-turn total
and materializes at most the newest 25 addressed pairs—50 events—to retain 24
and detect the boundary. Full `read()` still reconstructs the complete
transcript. Database `TEXT` is strict UTF-8; malformed stored text or a required
lexical projection that differs from its public source maps to a content-free
`WorkspaceFormatError`, while a caller-owned `UnicodeDecodeError` is not
reclassified as storage corruption.

Every effective catalog/event mutation and archive import updates the required
main-database lexical values and advances its generation atomically. Failure
rolls back both domain and lexical state; an already-in-target-state status
call or an exact archive reimport is generation-stable. A best-effort companion
refresh runs only after the main commit under the still-valid lease, so its
failure cannot roll back authoritative work and exact search remains available.

For initial creation, provision only when the application deliberately wants a
new empty SQLite destination. Provisioning never installs SQLite and does not
copy or migrate filesystem conversations:

```python
from story_writing_agents import Workspace
from story_writing_agents.conversation_storage import (
    ConversationStorageCapability,
    WorkspaceConversationStorageMutationCoordinator,
)
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)

workspace = Workspace("/path/to/initialized-story")

# Observe capability without creating the database.
status = preflight_sqlite_conversation_storage(workspace)

# This branch is an explicit application decision for an empty SQLite target.
if status.capability is ConversationStorageCapability.PROVISIONING_NEEDED:
    coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
    with coordinator.borrow(timeout_seconds=5.0) as lease:
        status = provision_sqlite_conversation_storage(workspace, lease=lease)

status.require_ready()
services = compose_sqlite_conversation_storage(workspace)
catalog = services.catalog
event_store = services.event_store
search = services.search  # SQLiteConversationSearch
```

Provisioning also verifies a valid existing target and a complete competing
race winner exhaustively. Its only recovery behavior is cleanup of one provable
interrupted no-overwrite publication: under the supplied active lease, the
target and exactly one provisioning-pattern sibling must be the same regular
two-link inode with no sidecars or ambiguity. The intact two-link database is
exhaustively validated before provisioning removes only that sibling alias.
Provisioning synchronizes and then exhaustively validates the recovered
single-link target again. Preflight and reads remain observational. Symlinks,
wrong or multiple candidates, unrelated inodes, extra links, sidecars, and
invalid target content fail closed; this is not a general repair facility. See
the
[recovery guidance](../operations/recovery-and-limitations.md#sqlite-conversation-storage-recovery)
for the operator response.

Composition verifies readiness but never provisions. It returns one
same-provenance catalog, event store, `SQLiteConversationSearch`, and shared
mutation coordinator. Direct adapters implement the catalog and transcript
contracts documented on their reference pages. Pass the complete bundle when
the runtime and memory service must share its catalog, events, search, and
coordination provenance:

```python
from story_writing_agents import (
    ConversationMemoryService,
    ConversationSearchScope,
    StoryRuntime,
)

runtime = StoryRuntime(
    workspace,
    conversation_storage_services=services,
)
memory = ConversationMemoryService(
    ConversationSearchScope("novel"),
    conversation_storage_services=services,
)
```

Direct `StoryRuntime(workspace)` continues to compose filesystem services; it
does not read desired or active selection itself. The installed shell performs
the separate startup activation workflow and injects its resulting complete
bundle.

`SQLiteConversationSearch.search(scope, query)` owns one stable main-database
read transaction for the complete public 256-conversation/10,000-event scan.
It derives exact normalization, ranking, citations, snippets, counters,
truncation, and ordering from authoritative rows. Optional FTS5 candidates are
hints only: false positives, false negatives, score/order differences, absence,
staleness, corruption, unsafe shape, runtime mismatch, or operational failure
cannot change the result because exact fallback covers the full bounded scan.
Search performs no repair.

The sole companion owner is another supported direct-module integration
surface, absent from both facades. Importing it performs no I/O and does not
load `sqlite3`; its exact five-name export inventory is:

```python
from story_writing_agents.conversation_storage.sqlite_search_index import (
    SQLITE_CONVERSATION_SEARCH_INDEX_PATH,
    SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION,
    SQLiteConversationSearchIndex,
    SQLiteConversationSearchIndexDocument,
    SQLiteConversationSearchIndexError,
)
```

The path is exactly
`"discussion_logs/.conversation_storage_fts5.sqlite3"` and the companion
schema version is exactly `1`. Integration signatures are:

```python
SQLiteConversationSearchIndex(
    workspace_key: str,
    *,
    normalization_version: int,
)
SQLiteConversationSearchIndexDocument(
    session_id: str,
    conversation_id: str,
    sequence: int,
    searchable_content: str,
)
SQLiteConversationSearchIndex.candidates(
    *,
    source_generation: int,
    session_id: str,
    conversation_ids: tuple[str, ...],
    normalized_query: str,
    maximum: int,
) -> frozenset[tuple[str, int]]
SQLiteConversationSearchIndex.rebuild(
    *,
    source_generation: int,
    documents: Iterable[SQLiteConversationSearchIndexDocument],
    validate_publication: Callable[[], None],
) -> None
```

Construction accepts only an already-canonical absolute workspace key, pins
the fixed path, and performs path arithmetic only. `candidates()` and
`rebuild()` live-probe FTS5 and map companion-specific failures to the
content-free `SQLiteConversationSearchIndexError`. Rebuild can create an absent
resource or update only a recognized safe one through the owner's contained
publication checks; it is not a delete, main-database migration, readiness, or
search-authority API. Ordinary application search should use
`SQLiteConversationSearch` or SQLite composition rather than managing hints.

`SQLiteConversationArchiveAdapter` is a privileged integration boundary over
the public canonical `ConversationArchive` value; it does not define a second
payload or wire format. Export validates every metadata and event row globally
and rejects orphan events. Import round-trips the canonical value, validates the
complete destination before mutation and again after mutation in the same
transaction, and preserves exact statuses and every event kind. Orphan or
malformed rows and divergent existing identities fail without a partial import.
The migration coordinator composes this adapter with the filesystem archive
adapter rather than exposing database internals.

The amended exact schema remains version `1`; it is the first published
contract for this adapter. A database created by older unpublished pre-Issue-257
development code can fail the new exact fingerprint. This API defines neither
a v2 schema nor migration or compatibility behavior for that development
artifact.

On platforms without POSIX `fcntl`, valid existing storage can still be
preflighted and read observationally. The supported mutation coordinator cannot
acquire a lease, so provisioning and supported writes fail with
`ConversationStorageCoordinationError` before changing the database or
conversation domain state. The lazy coordinator guard keeps its separate
documented lifecycle. Desired selection, host installation through selection,
configuration save, and startup activation also require that coordinator and
therefore fail closed on those platforms.

None of these names is re-exported by `story_writing_agents` or
`story_writing_agents.conversation_storage`. The installed shell uses SQLite
only after the separate desired-selection and next-shell activation workflow;
direct runtime construction remains filesystem by default. The adapter module
itself adds no automatic installation, configuration selection, activation,
RAG, MySQL, or new citation behavior. Explicit migration is owned by the
separate direct migration module/CLI and operates only after SQLite is ready;
it does not select the destination. The private local FTS5 companion supplies
non-authoritative hints only and is not a backend, readiness requirement,
migration input, or FTS-only search mode.

## Enum values

| Enum | Exact values |
| --- | --- |
| `InputKind` | `prompt`, `draft` |
| `Phase` | `draft`, `audit`, `propose_revision`, `synthesize`, `finalize` |
| `Verdict` | `approve`, `conflict` |
| `DebateStatus` | `consensus`, `deadlock`, `failed` |
| `DebateContextMode` | `included`, `fresh` |
| `DebateProgressPhase` | `started`, `drafted`, `audit_started`, `audit_completed`, `synthesized`, `artifacts_committed`, `consensus`, `deadlock`, `failed`, `interrupted` |
| `DebateFailureKind` | `preflight`, `protocol`, `context`, `workspace`, `artifact_conflict`, `artifact_failure`, `internal` |
| `DirectiveKind` | `language`, `persona`, `reset` |
| `SessionDurableState` | `absent`, `ready`, `paused` |
| `SessionAccessState` | `claimable`, `held_here`, `blocked_by_live_local_owner`, `ownership_unknown` |
| `ConversationStatus` | `active`, `archived` |
| `ConversationSearchMatchKind` | `exact_subject`, `subject_prefix`, `subject_substring`, `transcript` |
| `ConversationRetrievalActionKind` | `search_conversations`, `read_transcript`, `answer` |
| `ConversationRetrievalProgressPhase` | `search_started`, `search_completed`, `read_started`, `read_completed` |

These are `StrEnum` values. Use members such as `Verdict.APPROVE`; a plain
string like `"approve"` is not accepted where reply validation requires the
enum member.

There are no alternate lifecycle summaries, projections, or package-root
aliases. See [Backend and sessions](backend-and-sessions.md#lifecycle-value-contracts).

## Reference map

- [Backend and sessions](backend-and-sessions.md)
- [Agent contracts](agent-contracts.md)
- [Runtime API](runtime-api.md)
- [Workspace API](workspace-api.md)
- [Canonical writing artifacts](writing-artifacts.md)
- [Conversation catalog](conversation-catalog.md)
- [Conversation transcripts](conversation-transcripts.md)
- [Canonical conversation archives and storage migration](conversation-archives.md)
- [Conversation search and cited transcript pages](conversation-search.md)
- [Bounded conversation retrieval](conversation-retrieval.md)
- [Optional conversation RAG setup and delivery plan](../guides/conversation-rag.md)
- [Interactive command parser](interactive-command-parser.md)
- [Identifiers and errors](configuration/identifiers-and-errors.md)

Lower-level modules may contain implementation helpers. The package-root facade
matches its `__all__`; archive names are supported only through the explicitly
documented conversation-storage subpackage and its owning-module surfaces.
The explicitly documented literal-search, SQLite adapter, SQLite companion,
conversation-migration, provisioning, selection, and conversation-RAG modules
are also supported direct-module surfaces and match their own `__all__` values.
None is re-exported by the package-root or conversation-storage facade.

The exact current inventories are package root `120`, conversation storage
`64`, conversation search `13`, SQLite database owner `12`, SQLite adapter
`14`, literal-search mechanics `6`, SQLite companion owner `5`, conversation
migration `7`, provisioning `4`, selection `7`, conversation RAG contracts
`35`, generic RAG adapter `3`, local RAG provider `5`, RAG generation store `8`,
and RAG composition `6`. The production module inventory is `74`.
