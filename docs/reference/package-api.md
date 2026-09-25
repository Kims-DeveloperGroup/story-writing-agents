# Package API and exports

The package exposes management, a Python lead-turn/confirmed-consultation and
reviewed-artifact-proposal workflow, generic backend/model contracts,
schema-v2 collaboration components, and a direct safe Git boundary. The
installed shell connects focused Lead input and separately confirmed
consultation, Apply, and local Commit controls through runtime-owned authority;
package imports alone grant none of those effects.

## Facade inventory

The stable inventories in this revision are:

| Import boundary | Exports | Purpose |
| --- | ---: | --- |
| `story_writing_agents` | 106 | Management runtime, workspace/catalog, backend/session, generic models, reply traces, retrieval contracts, parser, and errors |
| `story_writing_agents.conversations` | 158 | Catalog plus schema-v2 events, projection, evidence-grounded lead/specialist/artifact-review values, readable context, and pure validation |
| `story_writing_agents.conversation_search` | 18 | Schema-v2 search, exact citations, paging, and history service |
| `story_writing_agents.conversation_retrieval` | 24 | Low-level provider search/read-history/answer contracts |
| `story_writing_agents.conversation_storage` | 63 | Storage composition, provenance, activation, archive, and transfer contracts |
| `story_writing_agents.git` | 24 | Fixed local topology, external-operation inspection, exact baseline, status, diff-metadata, and log-metadata contracts and adapter |
| `story_writing_agents.session_recovery` | 10 | Pure correlated session-recovery evidence and projection contracts |
| `story_writing_agents.workspace` | 3 | `Workspace`, `initialize_workspace`, and `FilesystemConversationCatalog` |
| `story_writing_agents.writing_artifacts` | 76 | Canonical artifact/reference values plus pure exact change-set, relationship-policy, and artifact-application contracts |
| `story_writing_agents.artifact_proposals` | 16 | Direct-only pure intent, evidence, exact candidate, reviewer-map, and strict candidate-codec contracts |

The production graph has 144 modules, 585 runtime dependency edges, no runtime
cycles, and 587 edges when annotation-only imports are included. The package
root has 106 exports. The conversations facade contributes 120 event exports
and 30 context exports; its bindings are identical to the owner bindings.
Session-transition integration contributes accepted orchestration-facing edges
without adding an assessment facade export.

Import the management facade explicitly:

```python
from story_writing_agents import StoryRuntime, RuntimeSession, Workspace
```

There is no package-root `Runtime` or `Session` alias. `StoryRuntime` and
`RuntimeSession` expose `lead_turn()`, `propose_specialist_advisory()`,
`decide_lead_recommendation()`, and `decide_lead_step()`. The former confirms
one Lead preparation and returns `LeadTurnResult`; the latter executes
confirmed proposals through their separate decision. There is no public raw/unconfirmed specialist dispatch, artifact-write, Git,
or debate-start method.

The root also exports `ReplyTrace` and `ReplyTraceOrigin`. Runtime-produced
reply traces contain only role/action/context/recovery labels; they contain no
provider prompt, hidden reasoning, story bytes, or authority. The root still
exposes generic backend, prompt/request/result, debate model,
session lifecycle, catalog, parser, retrieval, and error contracts. Those are
component seams, not alternate runtime execution entry points. The exact list
is authoritative in `story_writing_agents.__all__` and is checked against the
project module index.

<a id="safe-git-boundary"></a>

## Safe Git boundary

The direct `story_writing_agents.git` facade exports 23 pure contracts and the
sole `GitRepositoryAdapter`. All 24 names are intentionally absent from the
106-name package-root facade. The adapter exposes exactly seven typed operations:

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
adapter with its exact 25-file manifest; callers cannot supply a CLI manifest
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
expand the 106/24/3 export counts and are not a stable application API or
arbitrary Git capability. Installed session Create now composes exact resource
provisioning through `AgentBackend`; application evidence and confirmation
remain owned by their existing workflow boundaries. See
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
106-name package-root facade. Its exact 76-name facade includes immutable path,
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
the 106-name package root. The evidence and result values are frozen, slotted,
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

This contract classifies active correlated workflows only and rejects an idle
shape as `unsupported_shape`. The separate package-internal transition
assessor described below supplies the stable idle proof. There is no codec,
pre-v2 reader, migration, or legacy compatibility path. The internal
reconciler gathers active owner facts; lifecycle integration preserves its
result and enforces the returned operation set. Neither behavior is exported
by this facade. See
[Correlated session-recovery projection](../operations/recovery-and-limitations.md#correlated-session-recovery-projection)
for every state, safe action, and sample input/output.

<a id="correlated-session-recovery-reconciliation"></a>

### Read-only owner reconciliation

Internal integration code can use the one-name direct module:

```python
from story_writing_agents.session_recovery.reconciliation import (
    SessionRecoveryReconciler,
)

reconciler = SessionRecoveryReconciler(workspace_root, collaboration_store)
result = reconciler.inspect(
    lifecycle_state=lifecycle_state,
    conversation_id=conversation_id,
    reviewed_proposal=reviewed_proposal,
    artifact_application=artifact_application,
)
```

The exact signatures are:

```text
SessionRecoveryReconciler(
    workspace_root: Path,
    collaboration_store: CollaborationEventStore,
) -> None
inspect(
    *,
    lifecycle_state: SessionLifecycleState,
    conversation_id: str,
    reviewed_proposal: SessionReviewedProposalEvidence | None = None,
    artifact_application: SessionArtifactApplicationEvidence | None = None,
) -> SessionRecoveryResult
```

`SessionRecoveryReconciler` is the direct module's only export. It is absent
from `story_writing_agents.session_recovery` and the project root. Construction
performs no I/O and retains only the normalized absolute workspace root and
the caller's collaboration-store port; it owns no repository, adapter, lock,
history, journal, process, capability, or lifecycle.

Inspection first validates inputs and derives the session only from the
supplied lifecycle value. Owner observation proceeds only for exact
`ready + held_here`; the reconciler never acquires, reopens, pauses, switches,
or releases access. It then performs this closed sequence:

1. Read collaboration history, session resources, and the complete session-
   history snapshot as generation A.
2. When application evidence was supplied, ask the artifact repository for
   that exact application ID. Never reconstruct or submit an Apply request.
3. Inspect the external Git operation and status in the exact linked session
   worktree, then uniquely select candidate, commit, and checkpoint records by
   their complete session/proposal/application/candidate/correlation facts.
4. Only when generation A claims a committed story operation, read the current
   linked-worktree log; other states perform no log read.
5. Read collaboration, resources, history, external Git, and status again as
   generation B. Compare each owner independently and reject mixed
   generations.
6. Repeat correlation selection, verify any claimed commit against stable
   status and the first current-`HEAD` log entry, construct
   `SessionRecoveryEvidence`, and delegate ordinary state precedence to the
   pure `reconcile_session_recovery()` classifier.

The artifact lookup uses that owner's recovery-before-read behavior. It may
complete or verify an already-journaled application before returning its
original immutable result. That is owner-controlled maintenance, not a new
workflow mutation: the reconciler never calls `apply_artifact_changes()`,
starts provider work, reconstructs authority, replays an operation, stages or
commits Git, mutates lifecycle state, cleans evidence, or executes the
projected next action. The result remains content-free. See the
[stable owner flow and user scenarios](../operations/recovery-and-limitations.md#session-recovery-owner-reconciliation).

<a id="session-transition-assessment-api"></a>

### Story-session transition assessment

Internal integration code can inspect whether an idle or active story session
has enough stable evidence for a later transition:

```python
from story_writing_agents.session_recovery.transition_assessment import (
    SessionTransitionAssessment,
    SessionTransitionAssessmentState,
    SessionTransitionAssessor,
    SessionTransitionNextAction,
    SessionTransitionOperation,
    SessionTransitionScope,
)

assessor = SessionTransitionAssessor(workspace_root)
assessment = assessor.inspect(
    lifecycle_state=lifecycle_state,
    conversation_id=conversation_id,
    collaboration_store=collaboration_store,
    reviewed_proposal=reviewed_proposal,
    artifact_application=artifact_application,
)
```

Those six names are the direct module's exact exports. None is exported by
`story_writing_agents.session_recovery` or the project root. The exact
signatures are:

```text
SessionTransitionAssessor(workspace_root: Path) -> None
inspect(
    *,
    lifecycle_state: SessionLifecycleState,
    conversation_id: str | None = None,
    collaboration_store: CollaborationEventStore | None = None,
    reviewed_proposal: SessionReviewedProposalEvidence | None = None,
    artifact_application: SessionArtifactApplicationEvidence | None = None,
) -> SessionTransitionAssessment
```

The package-supported backend consumes this direct-module boundary through:

```text
AgentBackend.assess_session_transition(
    *, lifecycle_state: SessionLifecycleState,
    requested_operation: SessionTransitionOperation,
    conversation_id: str | None = None,
    collaboration_store: CollaborationEventStore | None = None,
    reviewed_proposal: SessionReviewedProposalEvidence | None = None,
    artifact_application: SessionArtifactApplicationEvidence | None = None,
) -> SessionTransitionAssessment
```

The method validates the exact operation enum, constructs one call-local
assessor, delegates once, requires matching session identity, and returns the
same assessment object. The operation is then consumed by the CLI/controller
against `allowed_operations`; it is not forwarded as execution authority.

Construction accepts one normalized absolute `Path`, retains only that root,
and performs no I/O. Active inspection requires both `conversation_id` and
`collaboration_store`. It creates one call-local `SessionRecoveryReconciler`,
delegates exactly once, preserves that exact nested recovery result, and does
not reread or reinterpret its owners. Idle inspection requires both active
inputs to be absent and rejects reviewed-proposal or application evidence. It
first requires lifecycle `ready + held_here`, then observes session resources,
complete session history, external Git, and linked-worktree status as
generation A and again as generation B. It returns `idle_ready` only when the
two generations agree and prove ready resources, no external Git operation, a
clean exact session branch and tip, closed story-commit history, matching story
and checkpoint tips (or their initial tips when no commit exists), and no
running, uncertain, recovery-required, staged, or preserved-index operation.

`SessionTransitionAssessment` contains exactly `session_id`, optional
`conversation_id`, `scope`, `state`, `next_action`, optional `owner`,
`failure_code`, ordered `allowed_operations`, and an optional exact
`recovery_result`. It contains no story/provider content, proof path, branch or
ref, proof or repository/worktree identity digest, process identity, live
handle, or executable capability. Inspection does not acquire or release a
claim, call a provider, apply an artifact, run a Git command that mutates
state, pause a session, or perform any allowed operation or process-lifecycle
action. It adds no codec or persisted format, so there is no migration or
compatibility reader.

Installed Create/Reopen and interactive destination attachment consume
`enter_destination`; interactive switching additionally consumes
`leave_source`; Pause consumes `pause_session`. An eligible persisted active
conversation default supplies the conversation/store pair. Missing or archived
defaults use idle evidence. Noninteractive JSON exposes only the fixed outer
fields and the fixed nested `SessionRecoveryResult`; interactive denial exposes
only the six content-safe presentation fields. Neither path reconstructs
reviewed evidence, confirmation choices, story bytes, or a capability, and no
provider runs during assessment. The result remains transition evidence, not
Apply, commit, or provider authority. See the
[canonical permission matrix and examples](../operations/recovery-and-limitations.md#session-transition-assessment).

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

Contextual authoring adds these exports to the same conversations facade:

```python
from story_writing_agents.conversations import (
    ArtifactDraftSource,
    ArtifactObjective,
    ArtifactObjectiveCandidateBinding,
    ArtifactObjectiveFacts,
    ArtifactObjectiveFactsV2,
    ArtifactObjectiveNextStep,
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    ArtifactObjectiveState,
    LeadObjectiveAction,
    LeadObjectiveIntent,
    artifact_objective_context,
)
```

These additions do not change the package-root facade. They describe the
owner-conversation objective, whole-message draft-source digests, exact
candidate binding, and `open`/`satisfied`/`cancelled` projection. Only matching
successful Apply satisfies an objective; no value grants execution authority.
Recommendation preparation adds `LeadRecommendationActionKind`,
`LeadRecommendedAction`, `LeadRecommendationState`,
`LeadRecommendationRequestPayload`, `LeadRecommendationDecision`,
`LeadRecommendationDecisionPayload`, `LeadRecommendationStartedPayload`, and
`LeadRecommendationResultPayload` to this same facade. Confirm-origin objectives
use v2 provenance; an existing objective retains its immutable v1 or v2 origin.

`LeadTurnResult` has trailing optional `recommendation_request=None` and
`trace=None` fields.
A pending recommendation uses `submit_recommendation_decision`, with proposal,
Apply, and Commit controls absent. The preceding optional `story_commit_request`
rediscloses an exact pending Commit; `artifact_application_request` carries
Apply disclosure. The minimal variant-based provider `lead-outcome-v5` contract is distinct from these richer Python result values;
see [Runtime API](runtime-api.md#contextual-authoring-objectives).

Pure `AuthoringScope`, `AuthoringActivity`, `ReviewBasis`,
`SpecialistAssignmentMode`, and canonical domain/routing helpers are direct-module
APIs in `story_writing_agents.specialist_routing`; they are not package-root
exports. Existing `ArtifactReviewDomain` imports through artifact-proposal and
relationship-policy modules remain compatible. Durable scope/basis codecs are
exported by `story_writing_agents.conversations`, which continues to own their
stored shapes. See [Agent contracts](agent-contracts.md#authoring-scope-and-assignments).

Both runtime advisory facades accept keyword-only `authoring_scope` and
`review_basis`. Missing/workflow scope raises `ValueError`; a selected single
specialist must cover every affected domain. Use the complete
[advisory migration example](runtime-api.md#propose_specialist_advisory).

The lead and specialist values are strict public inputs and results. Every
fresh provider lead judgment carries exact runtime-issued evidence; the runtime
persists a typed judgment or a content-free validation-failure closure. Scope
and evidence status are provider semantic declarations, while runtime enforces
their structural receipt matrix without NLP. Construct a `LeadStepDecision`
from the exact proposal identity, digest, revision, and confirmation-request ID
returned by `lead_turn()`; conversational text is not confirmation. A confirmed
consultation records typed draft/review replies and a typed lead synthesis
that preserves scope and evidence when all assignments succeed. See
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

Ordinary focused shell input uses the Python Lead workflow, which owns
proposal, confirmation, rejection, revision, supersession,
reconfirmation, confirmed specialist execution, typed synthesis,
artifact-candidate/review records, result, finalization, and outcome-unknown
history writes. Confirmation is authority only for the disclosed read-only
consultation.

Artifact Apply and local Git Commit have separate exact confirmations in the
current workflow. The lead coordinates the requested change; runtime code
executes the disclosed filesystem and Git effects. An authoring objective is
completed only by its matching successful Apply result. It grants no
confirmation or remote-publication authority.

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
