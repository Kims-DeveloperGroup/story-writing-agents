# Package API and exports

The package exposes management, generic backend/model contracts, direct
schema-v2 collaboration components, and a direct safe Git boundary. An
exported provider, debate, or Git component does not create an installed
story-execution command.

## Facade inventory

The stable inventories in this revision are:

| Import boundary | Exports | Purpose |
| --- | ---: | --- |
| `story_writing_agents` | 104 | Management runtime, workspace/catalog, backend/session, generic models, retrieval contracts, parser, and errors |
| `story_writing_agents.conversations` | 81 | Catalog plus schema-v2 events, projection, readable context, and pure validation |
| `story_writing_agents.conversation_search` | 18 | Schema-v2 search, exact citations, paging, and history service |
| `story_writing_agents.conversation_retrieval` | 24 | Low-level provider search/read-history/answer contracts |
| `story_writing_agents.conversation_storage` | 63 | Storage composition, provenance, activation, archive, and transfer contracts |
| `story_writing_agents.git` | 24 | Fixed local topology, external-operation inspection, exact baseline, status, diff-metadata, and log-metadata contracts and adapter |
| `story_writing_agents.session_recovery` | 10 | Pure correlated session-recovery evidence and projection contracts |
| `story_writing_agents.workspace` | 3 | `Workspace`, `initialize_workspace`, and `FilesystemConversationCatalog` |
| `story_writing_agents.writing_artifacts` | 76 | Canonical artifact/reference values plus pure exact change-set, relationship-policy, and artifact-application contracts |

The production graph has 119 modules, 431 dependency edges, no cycles, and 104
package-root exports. The conversations owners contribute 54 event exports and
20 context exports; their facade bindings are identical to their owner
bindings.

Import the management facade explicitly:

```python
from story_writing_agents import StoryRuntime, RuntimeSession, Workspace
```

There is no package-root `Runtime` or `Session` alias. `StoryRuntime` and
`RuntimeSession` expose no direct provider turn or debate-start method.

The root still exposes generic backend, prompt/request/result, debate model,
session lifecycle, catalog, parser, retrieval, and error contracts. Those are
component seams, not alternate runtime execution entry points. The exact list
is authoritative in `story_writing_agents.__all__` and is checked against the
project module index.

<a id="safe-git-boundary"></a>

## Safe Git boundary

The direct `story_writing_agents.git` facade exports 23 pure contracts and the
sole `GitRepositoryAdapter`. All 24 names are intentionally absent from the
104-name package-root facade. The adapter exposes exactly seven typed operations:

```python
from story_writing_agents.git import GitRepositoryAdapter

adapter = GitRepositoryAdapter(absolute_workspace_root)
topology = adapter.inspect_topology()
external_operation = adapter.inspect_external_operation()
inspection = adapter.inspect_baseline(manifest)
created = adapter.create_baseline(manifest)
status = adapter.status()
diff = adapter.diff_metadata()
log = adapter.log_metadata()
```

The boundary can create or verify only the exact fixed `main` root commit and
return bounded content-free repository metadata. It has no arbitrary command
runner, provider, remote, migration, session branch/worktree, or later-commit
operation. The internal schema-v2 workspace initializer now composes this
adapter with its exact 22-file manifest; callers cannot supply a CLI manifest
or use that initialization authorization for a later commit.
See [Safe Git adapter and story baseline](git-adapter.md) for the manifest,
identity, topology, retry, security, and sample input/output contracts.

Separate direct modules implement the package-internal #504 session-resource
contracts, fixed Git process owner, and inspect/list/provision/require-ready
operations. Three more direct modules own the common-Git repository lease,
pure session-history contracts, and durable session-history storage; the fixed
process owner also exposes closed profiles and the exact internal checkpoint
index value. Three #509 direct modules now own pure story-commit values,
candidate preparation/revalidation, and exact decision/execution/recovery.
Two #510 direct modules own the closed session-runtime path/delta contracts and
exact internal-ref checkpoint execution/recovery; they reuse the shared
history, lease, and typed internal-checkpoint process profile.
None is re-exported by the project, Git, or workspace facade; they do not
expand the 104/22/3 export counts and are not a stable application API or
arbitrary Git capability. #455 will compose session resources, #491 will
produce applied-change evidence, and #492 will own the confirmation UI. See
[Package-internal session Git resources](git-adapter.md#package-internal-session-git-resources),
[exact story commits](git-adapter.md#package-internal-exact-story-commits), and
[session checkpoints](git-adapter.md#package-internal-session-checkpoints).

The three-name `story_writing_agents.workspace` facade remains unchanged:

```python
from story_writing_agents.workspace import (
    FilesystemConversationCatalog,
    Workspace,
    initialize_workspace,
)
```

`Workspace.initialize()` and `initialize_workspace(root)` intentionally have
no prompt-overwrite parameter. Pure marker/journal/scaffold ownership and the
descriptor-bound initialization/readiness/discovery lifecycle live in two
package-internal modules with empty exports. They do not enlarge the workspace
or package-root facade.

Canonical artifact value contracts live in
`story_writing_agents.writing_artifacts`, but are intentionally absent from the
104-name package-root facade. Its exact 76-name facade includes immutable path,
catalog, kind, reference, and filesystem `ArtifactSourceIdentity` values;
content-free authorization, operation history/state, read-request/action,
`ReferenceOperationFailure`, and parser contracts; and the 16 pure exact
change-set symbols, 15 pure relationship-policy symbols, and 14 pure artifact-
application contract symbols below.
Filesystem-backed `WritingArtifactRepository`, content-bearing read results,
private capabilities, runtime coordinators, the ranged viewer, and interactive
activation values remain internal. The current management controller does not
activate `$`/`$$` prompt editing or expose a public selection API or standalone
artifact command. See [Canonical writing artifacts](writing-artifacts.md).

The supported change-set imports are:

```python
from story_writing_agents.writing_artifacts import (
    ARTIFACT_CHANGE_SET_SCHEMA_VERSION,
    ArtifactChangeOperation,
    ArtifactChangeOperationKind,
    ArtifactChangeSet,
    ArtifactChangeSetError,
    ArtifactChangeSetErrorCode,
    ArtifactCreateOperation,
    ArtifactDeleteOperation,
    ArtifactPatchOperation,
    ArtifactRenameOperation,
    ArtifactReplaceOperation,
    ArtifactTextHunk,
    build_artifact_change_set,
    deserialize_artifact_change_set,
    project_artifact_change_set,
    serialize_artifact_change_set,
)
```

These frozen values and pure functions build, project, and byte-canonically
serialize complete-state-bound proposals. They perform no filesystem,
provider, lead-agent, relationship-policy, confirmation, apply, publication,
or Git operation. Their exact fields, byte-offset rules, signatures, digest
model, failure codes, and core/manuscript examples are documented under
[Exact in-memory artifact change sets](writing-artifacts.md#exact-in-memory-artifact-change-sets).

The supported relationship-policy imports are:

```python
from story_writing_agents.writing_artifacts import (
    ARTIFACT_RELATIONSHIP_COMPANION_RULES,
    ARTIFACT_RELATIONSHIP_SCHEMA_VERSION,
    MAX_RELATIONSHIP_ARTIFACTS,
    MAX_RELATIONSHIP_ARTIFACT_UTF8_BYTES,
    MAX_RELATIONSHIP_EDGES,
    MAX_RELATIONSHIP_STATE_UTF8_BYTES,
    ArtifactRelationshipEdge,
    ArtifactRelationshipError,
    ArtifactRelationshipErrorCode,
    ArtifactRelationshipKind,
    ArtifactRelationshipValidation,
    ArtifactReviewDomain,
    ArtifactReviewFact,
    ArtifactReviewReason,
    validate_artifact_relationships,
)
```

This module depends only on the canonical artifact catalog and exact
change-set contracts. It projects and compares complete in-memory relationship
graphs, enforces permanent core identities, and derives artifact-only review
facts. It owns no filesystem, repository, provider, agent, confirmation,
apply, publication, or Git behavior. Exact fields, limits, grammar, graph
deltas, review domains, errors, and all seven user workflows are documented
under [Canonical artifact relationship validation](writing-artifacts.md#canonical-artifact-relationship-validation).

<a id="correlated-session-recovery-api"></a>

## Correlated session-recovery contracts

`story_writing_agents.session_recovery` is a ten-name package facade for a
pure, read-nothing recovery projection:

```python
from story_writing_agents.session_recovery import (
    SessionArtifactApplicationEvidence,
    SessionRecoveryError,
    SessionRecoveryEvidence,
    SessionRecoveryFailureCode,
    SessionRecoveryNextAction,
    SessionRecoveryOwner,
    SessionRecoveryResult,
    SessionRecoveryState,
    SessionReviewedProposalEvidence,
    reconcile_session_recovery,
)

result = reconcile_session_recovery(evidence)
```

These are the facade's exact ten exports. They are intentionally absent from
the 104-name package root. The evidence and result values are frozen, slotted,
exactly typed, and provider-free. `reconcile_session_recovery()` accepts only
one already assembled `SessionRecoveryEvidence`; it reads no lifecycle store,
conversation history, artifact repository, filesystem, Git repository, or
session. It calls no provider and cannot mutate, replay, continue, abort,
reset, clean, or execute a recovery action.

The caller supplies facts from the existing authoritative owners: lifecycle,
collaboration history, session Git resources, external-Git inspection,
session history, repository status and commit metadata, plus any reviewed
proposal, artifact application, story-commit candidate/operation, or
checkpoint operation. `SessionReviewedProposalEvidence` binds the exact
session/conversation/history tail to proposal and candidate revisions and
digests, the change set, relationship validation, correlation ID, and any
application-confirmation request and visible choices.
`SessionArtifactApplicationEvidence` is the mandatory session-bound wrapper
around an `ArtifactApplicationResult`; a bare workspace result is insufficient
to establish `applied_not_committed`. Both authoritative terminal application
results are valid: `applied` must match the exact unstaged affected paths,
while idempotent `unchanged` must accompany a clean Git status. Dirty, staged,
or unmerged status beside `unchanged` is a repository contradiction, not new
work to infer. The relationship-validation digest is also an owner-supplied
fact: the classifier compares it across reviewed and application evidence and
checks the embedded change-set/validation state digests, but does not privately
recompute the tagged relationship digest.

After exact shape checks, classification is deterministic and content-free.
Cross-owner contradictions, missing predecessors, repository mismatches, or a
non-`none` external Git operation fail closed. A recovery-required owner
outranks unresolved outcomes; an unresolved owner outranks ordinary workflow
progress. Otherwise the result reports the furthest exactly correlated closed
state, so a proved commit supersedes its earlier staged/application/review
facts. The result carries only correlation identifiers, its responsible owner,
one failure code, and one safe next-action label. It carries no story text and
the label is guidance, never executable authority.

`GitSessionHistoryInspection` remains authoritative even when a caller omits
the optional detailed commit or checkpoint record. A compact operation entry
in `recovery_required` or `outcome_unknown` therefore still projects that
state with `story_commit` or `checkpoint` ownership. When a detailed record is
supplied, it must match exactly one compact entry. A preserved checkpoint index
without its matching detailed checkpoint record instead fails closed as
`missing_predecessor` owned by `checkpoint`; the classifier does not guess its
operation state from the index.

This contract classifies active correlated workflows only. A fresh idle
session remains a session-lifecycle concern and is rejected as
`unsupported_shape`. There is no codec, pre-v2 reader, migration, or legacy
compatibility path. Issue #527 will gather and reconcile owner facts, while
#528 will decide how a runtime lifecycle or CLI presents and enforces a result;
neither behavior is implemented by this facade. See
[Correlated session-recovery projection](../operations/recovery-and-limitations.md#correlated-session-recovery-projection)
for every state, safe action, and sample input/output.

<a id="structured-collaboration-history-boundary"></a>

## Structured collaboration history boundary

Use the static conversations facade for schema-v2 event and context contracts:

```python
from story_writing_agents.conversations import (
    CollaborationContextBootstrap,
    CollaborationEventBatch,
    CollaborationEventStore,
    CollaborationHistoryProjection,
    CollaborationReadableContext,
    CollaborationReadableRecord,
    project_collaboration_history,
    project_collaboration_readable_history,
    select_bounded_collaboration_context,
)
```

The selected `ConversationStorageServices` has same-provenance bindings for
one catalog, one `CollaborationEventStore`, one aggregate
`CollaborationHistorySearch`, and one mutation coordinator. The event-store
port is:

```python
append_batch(batch: CollaborationEventBatch) -> CollaborationAppendReceipt
read(session_id: str, conversation_id: str) -> tuple[CollaborationEvent, ...]
read_context(session_id: str,
             conversation_id: str) -> CollaborationHistoryProjection
```

Readable history and state projection are separate. Default
`select_bounded_collaboration_context()` output is a content-free bootstrap;
only explicit known event IDs add whole records under the 24-record/32,768-byte
limit. It does not include current input, private payload, or provider
authority.

Use the direct search module for current provider-free search and paging:

```python
from story_writing_agents.conversation_search import (
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchScope,
    CollaborationHistoryService,
)

service = CollaborationHistoryService(
    CollaborationHistorySearchScope("novel", include_archived=False),
    conversation_storage_services=services,
)
result = service.search(CollaborationHistorySearchQuery("opening constraint"))
page = service.read_history(conversation_id, max_records=24)
```

Search matches only closed readable event fields. Subjects are display-only.
Citations bind an exact event, field, and code-point range. Page cursors bind
the exact last sequence and tail-batch digest and fail if history appends.

The concrete stores are supported direct-module APIs only:

```python
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLiteCollaborationEventStore,
)
```

They are not re-exported by the package root, workspace facade, or storage
facade. Normal applications should use a complete composed service bundle.
Filesystem composition is the runtime default; selected SQLite composition
uses the same v2 port.

If the filesystem owner sees `transcript.jsonl`, or the SQLite owner sees any
row in `conversation_events`, it rejects the owning boundary before mutation.
Strict schema-v2 workspace readiness also rejects unsupported whole-workspace
state before normal activation.

See [Collaboration history](collaboration-history.md),
[transcripts](conversation-transcripts.md), and
[search](conversation-search.md).

<a id="conversation-storage-composition"></a>

## Conversation-storage composition

`story_writing_agents.conversation_storage` exports contracts and filesystem
composition, including:

```python
compose_filesystem_conversation_storage(workspace) -> ConversationStorageServices
compose_filesystem_conversation_archive(workspace) -> FilesystemConversationArchiveAdapter
```

`ConversationStorageServices` validates complete same-provenance bindings and
the mutation coordinator workspace key. `StoryRuntime` accepts only this
complete bundle, not a loose concrete event store.

<a id="optional-sqlite-conversation-storage"></a>

## Optional SQLite conversation storage

The direct SQLite owner provides catalog, v2 collaboration store, aggregate
search, archive adapter, preflight/provisioning, and complete composition.
Use its exact inspected signatures:

```python
from story_writing_agents.conversation_storage.sqlite import (
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    provision_sqlite_conversation_storage,
)
```

Preflight is observational. Provisioning and writes require the mutation
coordinator. The authoritative database is schema version 2; unsupported
owning-boundary rows fail before mutation. The private FTS companion is only a
non-authoritative candidate accelerator; every result is reauthorized against
canonical events.

<a id="conversation-storage-transfer-api"></a>

## Archive and transfer

The storage facade exposes canonical archive values, filesystem archive
composition, parity/receipt values, and mutation leases. The direct transfer
module coordinates source/destination adapters. Transfer carries exact
schema-v2 histories and excludes derived FTS/RAG data.

<a id="conversation-storage-selection-api"></a>

## Selection and activation

Selection is persistent desired configuration; activation is the verified
bundle used by a new shell. It is separate from transfer. Both filesystem and
SQLite activation now produce a complete same-provenance v2 service bundle.

<a id="optional-conversation-rag-boundary"></a>

## Optional conversation RAG boundary

Conversation RAG uses direct modules for contracts, local provider, derived
generation storage, composition, runtime authorization, and CLI workflows. It
builds exactly one compact readable-fields JSON document per canonical event.
Derived documents and vectors are never authority; returned ranges are
reauthorized and reread from the selected v2 service.

`StoryRuntime` may receive `conversation_rag_service` as a collaborator, but
its management facade has no provider turn that consumes it. Direct callers
use `ConversationRagRuntimeService.search_authorized()`; users use the
provider-free `story rag` lifecycle/search commands.

## Low-level retrieval boundary

The root and direct retrieval module expose the generic bounded
`search_conversations` → `read_history` → `answer` protocol, including
`ConversationRetrievalHistoryRecord`. `BoundAgent.converse_with_retrieval()` is
a direct component seam. It does not create/focus a conversation or append a
collaboration event. The installed lead workflow does not invoke it.

## Ownership of later writes

The active public shell is inspection-only for collaboration history. #452
owns later lead proposal/confirmation/reconfirmation writes; #453 owns later
specialist execution/result/finalization/outcome-unknown writes. Neither
workflow is implied by the storage/API exports described here.

## Reference map

- [Runtime management API](runtime-api.md)
- [Workspace API](workspace-api.md)
- [Canonical writing artifacts](writing-artifacts.md)
- [Collaboration history](collaboration-history.md)
- [Conversation transcripts](conversation-transcripts.md)
- [Conversation search](conversation-search.md)
- [Conversation retrieval](conversation-retrieval.md)
- [Conversation archives and transfer](conversation-archives.md)
- [SQLite conversation storage](../guides/sqlite-conversation-storage.md)
- [Conversation RAG](../guides/conversation-rag.md)
