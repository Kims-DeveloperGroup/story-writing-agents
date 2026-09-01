# Package API and exports

The package exposes management, a Python lead-turn/confirmed-consultation and
reviewed-artifact-proposal workflow, generic backend/model contracts, and
schema-v2 collaboration components. This does not connect story execution to
the installed shell or grant artifact Apply or Git authority.

## Facade inventory

The stable inventories in this revision are:

| Import boundary | Exports | Purpose |
| --- | ---: | --- |
| `story_writing_agents` | 104 | Management runtime, workspace/catalog, backend/session, generic models, retrieval contracts, parser, and errors |
| `story_writing_agents.conversations` | 117 | Catalog plus schema-v2 events, projection, evidence-grounded lead/specialist/artifact-review values, readable context, and pure validation |
| `story_writing_agents.conversation_search` | 18 | Schema-v2 search, exact citations, paging, and history service |
| `story_writing_agents.conversation_retrieval` | 24 | Low-level provider search/read-history/answer contracts |
| `story_writing_agents.conversation_storage` | 63 | Storage composition, provenance, activation, archive, and transfer contracts |
| `story_writing_agents.workspace` | 3 | `Workspace`, `initialize_workspace`, and `FilesystemConversationCatalog` |
| `story_writing_agents.writing_artifacts` | 62 | Canonical artifact/reference values plus pure exact change-set and relationship-policy contracts |
| `story_writing_agents.artifact_proposals` | 16 | Direct-only pure intent, evidence, exact candidate, reviewer-map, and strict candidate-codec contracts |

The production graph has 100 modules, 379 dependency edges, no cycles, and 104
package-root exports. The conversations facade contributes 86 event exports
and 24 context exports; its bindings are identical to the owner bindings.

Import the management facade explicitly:

```python
from story_writing_agents import StoryRuntime, RuntimeSession, Workspace
```

There is no package-root `Runtime` or `Session` alias. `StoryRuntime` and
`RuntimeSession` expose `lead_turn()`, `propose_specialist_advisory()`, and
`decide_lead_step()`. Confirmed proposals execute through the decision method;
there is no public raw/unconfirmed specialist dispatch, artifact-write, Git,
or debate-start method.

The root still exposes generic backend, prompt/request/result, debate model,
session lifecycle, catalog, parser, retrieval, and error contracts. Those are
component seams, not alternate runtime execution entry points. The exact list
is authoritative in `story_writing_agents.__all__` and is checked against the
project module index.

Canonical artifact value contracts live in
`story_writing_agents.writing_artifacts`, but are intentionally absent from the
104-name package-root facade. Its exact 62-name facade includes immutable path,
catalog, kind, reference, and filesystem `ArtifactSourceIdentity` values;
content-free authorization, operation history/state, read-request/action,
`ReferenceOperationFailure`, and parser contracts; and the 16 pure exact
change-set symbols and 15 pure relationship-policy symbols below.
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

The reviewed-candidate construction boundary is direct-only:

```python
from story_writing_agents.artifact_proposals import (
    ArtifactIntentEvidence,
    ArtifactPatchIntent,
    ArtifactProposalIntent,
    ArtifactReviewCandidate,
    artifact_review_specialists,
    build_artifact_review_candidate,
    deserialize_artifact_review_candidate,
    serialize_artifact_review_candidate,
)
```

The exact 16-name module API also includes create, replace, rename, and delete
intent values plus closed proposal errors/schema version. It consumes pure
writing-artifact change-set and relationship-policy contracts and maps closed
review domains to canonical specialists. It has no package-root or
`writing_artifacts` facade export and performs no I/O, provider call,
confirmation, Apply, write, publication, or Git operation. Runtime owns
complete-state capture and passes it into candidate construction. See
[Agent contracts](agent-contracts.md#reviewed-artifact-proposal-contracts).

<a id="structured-collaboration-history-boundary"></a>

## Structured collaboration history boundary

Use the static conversations facade for schema-v2 event and context contracts:

```python
from story_writing_agents.conversations import (
    ArtifactProposalResult,
    ArtifactProposalState,
    ArtifactReviewCandidatePayload,
    ArtifactReviewDecision,
    ArtifactReviewedProposal,
    CollaborationContextBootstrap,
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
    CollaborationEventBatch,
    CollaborationEventStore,
    CollaborationHistoryProjection,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationLeadSynthesisPayload,
    CollaborationReadableContext,
    CollaborationReadableRecord,
    CollaborationSpecialistAssessment,
    LeadDecisionResult,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadStepDecision,
    LeadTurnResult,
    project_collaboration_history,
    project_collaboration_readable_history,
    select_bounded_collaboration_context,
)
```

The lead and specialist values are strict public inputs and results. Every
fresh provider lead judgment carries exact runtime-issued evidence; the runtime
persists a typed judgment or a content-free validation-failure closure. Scope
and evidence status are provider semantic declarations, while runtime enforces
their structural receipt matrix without NLP. Construct a `LeadStepDecision`
from the exact proposal identity, digest, revision, and confirmation-request ID
returned by `lead_turn()`; conversational text is not confirmation. A confirmed
consultation records typed specialist replies and a typed, evidence-preserving
lead synthesis when all assignments succeed. See
[Runtime API](runtime-api.md) for signatures and state outcomes.

For artifact intent, runtime creates an exact candidate and a normal
specialist-consultation proposal. Confirm authorizes only review of that
candidate. All reviewers receive the same candidate, and a successful lead
`propose` synthesis creates `ArtifactReviewedProposal` with all Apply/write/Git
flags false. The result survives restart and storage transfer as collaboration
history; it is not executable authority.

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
#454 owns the later whole-workspace rejection boundary.

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
`lead_turn()` does not consume that collaborator. Direct callers
use `ConversationRagRuntimeService.search_authorized()`; users use the
provider-free `story rag` lifecycle/search commands.

## Low-level retrieval boundary

The root and direct retrieval module expose the generic bounded
`search_conversations` → `read_history` → `answer` protocol, including
`ConversationRetrievalHistoryRecord`. `BoundAgent.converse_with_retrieval()` is
a direct component seam. It does not create/focus a conversation or append a
collaboration event. The installed lead workflow does not invoke it.

## Ownership of collaboration and later story writes

The public shell is inspection-only for collaboration history. The Python lead
workflow owns proposal, confirmation, rejection, revision, supersession,
reconfirmation, confirmed specialist execution, typed synthesis,
artifact-candidate/review records, result, finalization, and outcome-unknown
history writes. Confirmation is authority only for the disclosed read-only
consultation.

Artifact Apply and Git Commit remain separate future operations with separate
exact confirmations. The lead is their planned semantic trigger and
user-facing coordinator; runtime code, not the provider, will execute the
filesystem and Git effects.

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
