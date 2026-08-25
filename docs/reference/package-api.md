# Package API and exports

> **Availability:** the 120-name inventory below is exported from the
> implemented `story_writing_agents` package facade. The optional SQLite API has
> a separate owning module and is explicitly documented after that inventory;
> it is not a facade export. Context and progress types are Python APIs; an
> export does not imply a CLI command.

Key terms: **package facade**—the names re-exported at the package root;
**public import**—a supported symbol imported from that facade; **alias**—a
second name for the same object; **enum**—a type with a fixed value set.

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
revalidates the lease, and writes only through that root. This does not add
automatic selection or make activation transactional with other operations.

See [Filesystem safety](../operations/filesystem-safety.md#composed-conversation-storage-provenance)
for the operational boundary.

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

The owning module exports exactly:

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
    compose_sqlite_conversation_archive,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
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

Schema v1 owns exactly nine explicit objects: three tables, three indexes, and
three insert triggers. Ordered event reads use the event table's composite
primary-key autoindex, not a redundant explicit event-order index. Addressed
turn lookup uses the unique turn-slot index.

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
SQLiteConversationArchiveAdapter(
    workspace: Workspace | str | os.PathLike[str],
    *,
    mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
    mutation_timeout_seconds: float = 5.0,
)
preflight_sqlite_conversation_storage(
    workspace: Workspace | str | os.PathLike[str],
    /,
) -> ConversationStoragePreflight
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

Preflight is observational: it never creates or repairs storage. On a capable
host a missing database reports `provisioning_needed`; an unavailable or
too-old `sqlite3` runtime reports `unavailable`; a valid database reports
`ready`. Unsupported schema versions, malformed storage, and unsafe paths raise
`WorkspaceVersionUnsupportedError`, `WorkspaceFormatError`, and
`WorkspaceSecurityError`. Calling `require_ready()` on a non-ready result
raises the corresponding conversation-storage readiness error. Every returned
preflight has `backend == ConversationStorageBackend.SQLITE`, and successful
provisioning returns `ready`.

Existing-database preflight is exhaustive: it verifies database/schema
identity, runs SQLite integrity and global foreign-key checks, rejects orphan
events, and reconstructs every metadata and event row. Ordinary catalog and
event-store calls use bounded identity verification plus operation-local row
validation instead. They do not certify unrelated database contents. In
particular, append reads the indexed maximum sequence and at most the addressed
turn's one user row and one outcome row rather than reconstructing the complete
transcript. Database `TEXT` is strict UTF-8; malformed stored text encountered
by verification or the requested operation maps to a content-free
`WorkspaceFormatError`, while a caller-owned
`UnicodeDecodeError` is not reclassified as storage corruption.

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
search = services.search  # ReferenceConversationSearch
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
same-provenance catalog, event store, `ReferenceConversationSearch`, and shared
mutation coordinator. Direct adapters implement the catalog and transcript
contracts documented on their reference pages. A caller may explicitly pass
`services.event_store` to the existing `StoryRuntime` constructor for its
transcript/context port; that does not select SQLite for the shell or wire the
catalog and search services into the runtime.

`SQLiteConversationArchiveAdapter` is a privileged integration boundary. Its
payload and record types are private, adapter-owned implementation details, so
it is not a supported serialization format or migration API. There is no
public archive-payload constructor to document or persist independently.
Export validates every metadata and event row globally and rejects orphan
events. Import validates the complete destination before mutation and again
after mutation in the same transaction; orphan or malformed rows and divergent
existing identities fail without a partial import.

On platforms without POSIX `fcntl`, valid existing storage can still be
preflighted and read observationally. The supported mutation coordinator cannot
acquire a lease, so provisioning and supported writes fail with
`ConversationStorageCoordinationError` before changing the database or
conversation domain state. The lazy coordinator guard keeps its separate
documented lifecycle. Issue #261 owns full non-POSIX coordinator/activation
readiness and the future confirmed detection, installation, and
settings-selection flow.

None of these names is re-exported by `story_writing_agents` or
`story_writing_agents.conversation_storage`. The installed shell and runtime
continue to use filesystem conversation storage. This module adds no
`/settings` selection, automatic install or provisioning, default activation,
migration, FTS, RAG, MySQL, or new citation behavior. Issue #261 owns later
integration of detection, installation, and provider selection into the
user-facing settings/runtime flow; direct preflight here only reports
capability.

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
- [Conversation search and cited transcript pages](conversation-search.md)
- [Bounded conversation retrieval](conversation-retrieval.md)
- [Interactive command parser](interactive-command-parser.md)
- [Identifiers and errors](configuration/identifiers-and-errors.md)

Lower-level modules may contain implementation helpers. The package facade and
the explicitly documented SQLite owning module are the supported public import
surfaces; both match their own `__all__` values and neither re-exports the
other.
