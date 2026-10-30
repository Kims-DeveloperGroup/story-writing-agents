# Runtime and lead-orchestration API

`StoryRuntime` is the public management, configuration, inspection, and lead
front-channel facade. `RuntimeSession` binds those operations to one session
ID. The runtime can obtain one strict lead outcome, disclose a consultation,
construct an exact complete-state-bound artifact candidate, and execute its
one-to-three specialist assignments after an exact structured confirmation.
Artifact review can produce an immutable reviewed proposal. A separate exact
Apply decision can apply that proposal, and a later separate exact Commit
decision can create its local session-branch commit. The runtime also exposes
a stepwise, separately confirmed consensus workflow through Python. Review and
consensus finalization alone never apply an artifact, grant Apply authority, or
perform Git work.

The installed shell calls the session-bound lead methods for ordinary focused
input and constructs exact decisions through structured controls. It does not
expose raw specialist dispatch or a `run` command.

## Construction

```python
StoryRuntime(
    workspace: Workspace | str | pathlib.Path,
    backend: AgentBackend | None = None,
    *,
    conversation_storage_services: ConversationStorageServices | None = None,
    conversation_rag_service: ConversationRagRuntimeService | None = None,
)
```

With no conversation arguments, construction composes the filesystem storage
bundle. An injected `ConversationStorageServices` must be complete and carries
the one catalog, schema-v2 event store, search, mutation, and provenance
boundary. A RAG service is an optional separate collaborator; `lead_turn` does
not consume it.

Construction performs no provider call. Lead conversation requires a backend
whose configured session/provider lifecycle is ready when binding occurs.
`backend=None` remains valid for provider-free management and inspection, but
provider-backed lead and confirmed consensus execution then raise
`BackendProtocolError`.

### Control workspace and exact session artifacts

Construct `StoryRuntime` from the initialized `main` workspace. Runtime
configuration, directives, conversation storage, diagnostics, and other
control-plane state remain rooted there. The construction-compatible
`StoryRuntime.artifacts` repository likewise names that control root, but it
has no session authority and must not be used as an active session's artifact
repository.

Every artifact-sensitive runtime call instead derives the session ID already
present in its public arguments or durable identity, freshly requires that
session's exact READY branch/ref/sibling-worktree resources, and creates a
call-local binding pinned to the validated worktree's physical
`(st_dev, st_ino)` identity. Each outer synchronous artifact transaction opens
a short cooperating-runtime guard: it holds the common-Git lease, performs one
complete semantic READY inspection before the body, opens the no-follow named
root at the pinned identity, and performs one complete READY plus physical-root
check before a result escapes. Same-thread nested guards and repository
operations reuse that lease and descriptor and check only live thread/lease
authority plus the named physical root; they do not repeat the whole READY
scan. Long-lived reference and source adapters instead retain an access
factory and acquire a new outer transaction for each operation. Borrowed Story
Commit access performs one complete validation before and after its body
through the live history owner. Apply performs an additional explicit complete
READY check immediately before `apply_artifact_changes()` starts. All of these
exit checks run after success, `RuntimeError`, or cancellation. If the body and
exit check both fail, binding loss is authoritative; a clean postcheck
preserves the original exception, and cleanup completes in either case. The
authoritative binding failure may note only the superseded exception's type;
it carries no body/provider message or content. The
binding keeps one stable exact repository object private. Retaining it grants
no later or cross-thread authority. The underlying Git lease, session history,
artifact access, and Commit authority are affine to a
package-internal, process-aware opaque token for the creator's thread state,
not its numeric identifier. A later thread state cannot inherit authority after
numeric identifier reuse. Foreign-thread surface calls, including explicit
close, fail; context-manager teardown instead privately revokes tokens and
closes descriptors/leases without granting foreign authority.

The binding performs that creator check at every READY, repository-callback,
session-source, Story Commit service-composition, and borrowed access factory
before touching its operation lock, acquiring a Git lease, invoking a READY
callback, opening a descriptor, or reaching repository/source I/O. Factory
setup retains ownership
of each new descriptor and lease until the access object activates; every
`BaseException` before transfer closes and unwinds those resources while
preserving the primary failure. In a forked child, access revoke skips the
copied binding lock and closes only the child's worktree-descriptor copy. Git
lease exit closes the child's common-Git descriptor without `LOCK_UN`, leaving
the parent's exclusion intact.

Binding composition also captures one physical session-generation identity
under its initial READY lease: the control root, common Git directory,
per-worktree administrative directory, session worktree, and the bounded
`.git` and `commondir` indirection files with exact metadata and digests. An
opaque generation pin retains six non-inheritable descriptors for those four
directories and two files. Acquisition and recheck take only a short repository
identity lock, repeat that bounded no-follow relationship proof, and release
the lock before returning; they run no Git subprocess or semantic READY,
manifest, policy, or artifact scan. The pin exposes no descriptor, traversal,
content-read, mutation, or repository authority and is not a substitute for a
guarded READY transaction. Generation directory/file opens and duplicates,
and initial binding composition, retain local descriptor ownership until
transfer. Every `BaseException` closes those local descriptors without
replacing the primary failure. Batch cleanup attempts every close despite an
earlier `BaseException`; descriptor closes, lock/lease releases, and required
postchecks are exhausted while the active primary remains authoritative.

Direct Lead and Lead-synthesis invocations acquire this pin before any
session-derived definition, source, or provider work and retain it through the
durable result append. Synthesis transfers the same opaque pin to its
persistence owner; direct Lead work retains it around its own publication.
Both recheck the physical generation and close the pin after persistence, and
failure, cancellation, malformed output, binding loss, or a non-transferred
exit closes it exactly once. The identity pin may span a provider `await`, but
no Git lease or artifact access does. The generation identity, binding, and pin
may be acquired or revalidated only by their creator process and opaque thread
token. Foreign-thread or forked-child use fails before repository locking or
filesystem inspection; a child may only close its inherited descriptor copies
through the idempotent, child-safe close path. If provider or persistence work
fails while the physical-generation recheck also detects loss, generation loss
is authoritative and enters the existing recovery boundary. Any retained
diagnostic about the displaced body/provider failure is limited to its
content-free exception type, never its message or payload.
Lead and collaboration pin finalization is primary-aware: it preserves an
active failure and cannot skip the remaining bound-agent or sibling cleanup.

The continuity pin alone grants no process working-directory authority. For
each Codex or Antigravity story invocation, the same `SessionArtifactBinding`
separately mints an opaque call-local execution capability: the provider enters
the retained session-root descriptor under the fail-closed Darwin boundary.
Codex receives native project root `--cd .`; Antigravity uses the exact CWD as
its native project root without an adapter-control fallback. That placement
grants no direct manuscript/core-document read authority; host-owned context,
reference, SEARCH, and READ services deliver only selected story content in
provider payloads and observations. Direct
BoundAgent entry points retain the execution-workspace owner around the full
logical call and revalidate currentness when it exits, including after apparent
body success. Exit loss fails that return; if another body failure is already
active, it remains primary and receives only a content-free failure-type note.
See the
[Provider session execution boundary](backend-and-sessions.md#codex-session-execution-boundary).
Antigravity additionally requires the exact protected session role projection;
Codex continues direct developer-instruction injection and needs no worktree
configuration file.

This covers explicit and contextual references, source status and
fingerprints, proposal and review
revalidation, Apply and application-ledger access, recovery reads, consensus
artifact material, and the interactive attachment picker. A
`RuntimeSession` forwards the same session ID; switching between two ready
sessions or using them concurrently cannot borrow the control root or the
other session's repository.

Lead current-state capture reads canonical core snapshots through the exact
session binding. Its runtime overrides remain control-plane state: the same
control workspace owns `set_runtime_overrides()`, directives, and their reads,
so an uncommitted stale copy in a session worktree is never treated as current.

The interactive picker performs its path inventory and selected-file read as
separate guarded operations. The range viewer receives only the bytes returned
by that guarded read; neither the picker nor viewer retains repository access.

Binding is validation only. It never provisions resources, calls a provider,
or performs artifact I/O. Absent, moved, mismatched, staged, or otherwise
non-ready resources fail closed with a content-free binding failure before
provider or artifact access; runtime never falls back to `main`, the process
working directory, or a previously used session. Use Session Create or Reopen
to establish the lifecycle precondition before calling an artifact-sensitive
method. Existing public constructor and method signatures are unchanged.

The common-Git lease serializes only operations that cooperate with this
runtime. A persistent noncooperating Git change is rejected by a later READY
checkpoint, but the runtime does not claim continuous exclusion of arbitrary
external Git that changes and restores state between checkpoints. Stop all
workspace processes before manual inspection or recovery.

Provider context keeps the same ownership split. Focused conversation remains
in the configured conversation store. Active-debate and invoking-agent-state
reads use a package-internal, descriptor-relative `SessionSourceWorkspace`
derived from the same call-local binding; it exposes no generic path, write, or
Git authority. Source paths must be canonical relative POSIX text. Each allowed
read retains the complete no-follow descriptor chain from the session root
through every leaf ancestor, accepts only one unique regular-file leaf, and
revalidates every original parent-child edge after the leaf observation and
before returning. It rejects FIFOs/devices/symlinks/hard links and
identity/metadata drift, caps each file at 8 MiB, and checks embedded debate or
agent-state ownership. A compound agent-state or debate read retains its full
directory chain and rechecks the complete leaf-directory inventory: names plus
device, inode, type, link count, size, nanosecond modification time, and
nanosecond change time. Exactly 8,192 entries are accepted per inventoried
directory; an additional entry, parent replacement, or any inventory drift
fails closed. A compound agent-state read additionally accepts at most 8 MiB
across all of its JSONL files and at most 10,000 records. Debate JSONL likewise
accepts at most 10,000 records. Both readers scan records lazily; record 10,001 fails before
line slicing, JSON parsing, or retention. The exact limits are accepted and the
first excess byte or record fails closed. A missing agent-state parent produces
an empty result only after the retained existing ancestor prefix and the absent
edge are observed twice and remain exact. A missing leaf is likewise accepted
only after its full ancestor chain is revalidated. Creation between the failed
open and return fails closed. A missing, replaced, or unsafe bound root fails
closed as a content-free `source_unavailable` instead of borrowing control-root
or sibling-session state.

Artifact-sensitive definitions capture the canonical ordered behavior-policy
bytes from that guarded session worktree. Validated configuration, runtime
overrides, backend and route selection, and persona remain owned by the control
workspace; session policy capture does not redirect those authorities.

## Phase-confirmed consensus methods

```python
StoryRuntime.submit_consensus_prompt(
    session_id: str, conversation_id: str, prompt: str,
    *, authoring_assessment: AuthoringAssessment | None = None,
    authoring_direction: str | None = None,
) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded
StoryRuntime.submit_consensus_draft(
    session_id: str, conversation_id: str, draft: str,
    *, intent: AuthoringIntent = AuthoringIntent.AUTHORING,
    authoring_assessment: AuthoringAssessment | None = None,
    authoring_direction: str | None = None,
) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded
StoryRuntime.inspect_consensus(
    session_id: str, conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
StoryRuntime.load_workflow(
    session_id: str, conversation_id: str, workflow_id: str,
) -> ConsensusStoredWorkflow | None
await StoryRuntime.decide_consensus(
    decision: ConsensusRuntimeDecision,
) -> ConsensusRuntimeDecisionResult
await StoryRuntime.confirm_consensus(
    decision: ConsensusRuntimeDecision,
) -> ConsensusRuntimeDecisionResult
StoryRuntime.interrupt_consensus(
    session_id: str, conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
StoryRuntime.resume_consensus(
    session_id: str, conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
StoryRuntime.request_next_consensus_phase(
    session_id: str, conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
StoryRuntime.continue_consensus_deadlock(
    session_id: str,
    conversation_id: str,
    source_workflow_id: str,
    source_result_event_id: str,
    expected_source_tail_sequence: int,
    *,
    mode: ConsensusContinuationMode,
    ruling: str,
    added_revision_rounds: int,
    authoring_assessment: AuthoringAssessment | None = None,
) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded
```

`RuntimeSession` exposes the same names without `session_id`. Its decision
methods reject a decision belonging to another session. Import their values
from `story_writing_agents.consensus`:

```python
from story_writing_agents.consensus import (
    AuthoringIntent,
    ConsensusDecision,
    ConsensusContinuationMode,
    ConsensusDurability,
    ConsensusNextAction,
    ConsensusPhase,
    ConsensusPhaseState,
    ConsensusRuntimeDecision,
    ConsensusRuntimeDecisionResult,
    ConsensusRuntimeInspection,
)
```

### Official authoring admission

Import the pure admission values directly; they are not package-root exports:

```python
from story_writing_agents.authoring_admission import (
    AuthoringAssessment,
    AuthoringAdmissionNeeded,
)
```

Official drafting requires an assessment of the available story foundations,
USER direction, bounded creative room, protected constraints, and each required
decision. Each evidence row names actual offered source IDs and explains their
relevance. A `sufficient` status, nonempty text, or confidence statement alone
cannot qualify. Direction and creative delegation must cite USER input; selected
material and READ results cannot impersonate USER decisions.

The direct specialist advisory and AUTHORING consensus intake methods accept
keyword-only `authoring_assessment`. Missing or inadequate assessments return
`AuthoringAdmissionNeeded` before an authoring proposal or phase request is
created. Inspect its `questions`, `offered_sources`, and `available_source_ids`,
reuse facts already supplied, ask only for missing information or choices, and
resubmit with the resulting semantic assessment. Source presence and byte counts
prove availability, rather than literary adequacy. An empty READ remains offered
absence evidence; an adequate literal USER brief can supply foundations for an
empty target.

Consensus prompt and draft intakes also accept optional `authoring_direction`:
literal USER text stored separately from supplied material. Offered aliases are
`user_input` for a literal prompt, `input_material` for a supplied draft or selected
artifact, `user_direction` for this additional USER input, and `ruling_input` for a
fresh deadlock ruling. Inspect the offered sources before constructing an
assessment. Supplied material cannot supply USER direction merely because the
intent is AUTHORING. REVIEW_ONLY rejects `authoring_direction`.

An assessment is untrusted task input. The host binds it to exact source bytes,
original authority, request, task or workflow, revision, and scope, then checks
freshness again before confirmed dispatch. It grants no confirmation, review,
Apply, or Commit authority. A changed synthesis-deadlock ruling requires a fresh
assessment; the previous ruling's qualification is not copied. REVIEW_ONLY
consensus and concrete review advisory do not require an authoring assessment.
Ordinary Lead and exploration adoption obtain this assessment through the existing
structured front channel.

A qualified AUTHORING submission creates a durable DRAFT request without calling a provider. Confirming it runs the Execution Specialist, transforming even a supplied
draft into newly authored `r0` before audit. For a supplied draft, opt in with
`intent=AuthoringIntent.REVIEW_ONLY` to create an INPUT_ADOPTION request instead.
Its exact confirmation adopts the verified original UTF-8 bytes without provider
work. Separate confirmations run all three AUDIT participants and SYNTHESIS, then
provider-free REVIEW_COMPLETION. Review-only workflows cannot repair, finalize an
artifact proposal or publish; revision requires a fresh AUTHORING submission. `request_next_consensus_phase()` creates, but does not execute, the
successor request. `confirm_consensus()` accepts Confirm only;
`decide_consensus()` also accepts Reject and Revise.

`inspect_consensus()` is the user-facing projection. `load_workflow()` is the
read-only exact event-history view needed to copy the current request event's
correlation fields into a decision; `RuntimeSession` has no wrapper for this
lower-level view.

### Inspection and decision values

```python
ConsensusRuntimeInspection(
    submission: ConsensusWorkflowSubmission,
    projection: ConsensusProjection,
    capability_available: bool,
    recovery_required: bool = False,
)

ConsensusRuntimeDecision(
    execution_identity: ConsensusExecutionIdentity,
    workflow_id: str,
    phase_request_id: str,
    proposal_id: str,
    revision_id: str,
    request_digest: str,
    request_event_id: str,
    request_event_sequence: int,
    current_tail_sequence: int,
    confirmation_id: str,
    decision: ConsensusDecision,
    revision_request: str | None = None,
)

ConsensusRuntimeDecisionResult(
    inspection: ConsensusRuntimeInspection,
    execution: ConsensusExecutionReceipt | None,
    decision_durability: ConsensusDurability,
)
```

A decision must match the current durable request event, request digest, and
tail sequence exactly. Confirm additionally requires the private same-process
one-use capability. Reject closes the phase without a provider call. Revise
requires nonempty `revision_request`, closes the old request as `superseded`,
and permits an explicit replacement request for the same phase.

The projection states are `awaiting_confirmation`, `confirmed`, `running`,
`completed`, `interrupted`, `failed`, `outcome_unknown`, `recovery_required`,
`rejected`, `superseded`, and `deadlocked`. `next_action` is one of
`request_decision`, `start_confirmed_phase`, `wait_for_phase`,
`request_next_phase`, `inspect_failure`, `operator_recovery`,
`continue_deadlock`, or `none`.
`publication_allowed` is always false. A terminal AUTHORING SYNTHESIS deadlock reports
`next_action=continue_deadlock`; a REPAIR or FINALIZATION fallback deadlock
reports `next_action=inspect_failure`.

A REVIEW_ONLY synthesis deadlock reports `next_action=inspect_failure` and
requires fresh AUTHORING work for a requested revision.

For REVIEW_ONLY, `projection.review_findings` exposes the actual terminal
synthesis report after completion and restart. It remains untrusted report text
and cannot authorize candidate replacement, repair, Apply or Git Commit.

`ConsensusAudit.report_material` and `ConsensusProviderResult.report_material`
are optional keyword-only immutable handles for actual REVIEW_ONLY AUDIT reports.
The mapper verifies their digest against the original reply bytes and excludes
runtime fallback text. Conflicts reuse their critique handle; approval reports
retain their own material. Synthesis reads available reports in auditor order,
including approval observations. Earlier version-4 audits may lack the handle;
absence is disclosed without inferred findings. AUTHORING and non-AUDIT phases
reject report metadata. These handles grant no candidate, repair or publication
authority, and terminal `review_findings` remains synthesis text only.

The normal AUTHORING phase graph is DRAFT → AUDIT → SYNTHESIS. Lead synthesis selects
REPAIR → REAUDIT → SYNTHESIS, FINALIZATION, or deadlock. Every phase is a new
request and confirmation. Audits invoke all three specialists against the same
revision; the lead synthesizes and finalizes but does not vote. Finalization is
a read-only handoff to a separately confirmed artifact Apply, followed later
by a separately confirmed Git Commit.

Each rejected structured phase result receives one bounded repair. If that
repair is exhausted, DRAFT records `failed` with no candidate. REPAIR,
SYNTHESIS, and FINALIZATION record `deadlocked`, retain the trusted input
candidate, select no next phase, and still forbid publication. Only the
SYNTHESIS case is a valid continuation source; REPAIR and FINALIZATION direct
the caller to failure inspection. The returned provider result retains the
content-free `ReplyTrace` with `origin=runtime_fallback` and `action=none`.

Direct `ConsensusProviderResult.validation_details` contains the ordered safe
chunks for that provider invocation. When any provider result has findings, the
terminal `ConsensusPhaseResult.validation_details_by_provider` persists one
tuple per returned provider result in invocation order; a provider without
findings has an empty tuple at its position. Running checkpoints carry none.
Read the terminal event through `load_workflow()` when inspecting these details
after restart. Older result events without the optional field decode as an
empty tuple. The details contain schema paths and fixed type or rule findings,
not raw provider values or publication authority.

`interrupt_consensus()` acts only on a running request. `resume_consensus()` is
an inspection operation: it never resumes/replays a provider call or recreates
confirmation authority after restart. `outcome_unknown` and
`recovery_required` require operator reconciliation.

`continue_consensus_deadlock()` prepares, but does not execute, one fresh
REPAIR request from an exact terminal synthesis deadlock. `mode` is
`ConsensusContinuationMode.RESUME_IN_PLACE` or
`ConsensusContinuationMode.LINKED_WORKFLOW`; `ruling` is stored as immutable
content-addressed material; and `added_revision_rounds` must be positive. The
caller supplies the source result event ID and current tail sequence from the
same durable inspection. Candidate and conflict text are never arguments.

The returned projection exposes continuation mode, source workflow/result/
revision, source candidate digest, ruling digest/presence, added rounds, and
remaining rounds. It is `repair awaiting_confirmation`, so the caller must
obtain a new user decision and call `confirm_consensus()`. Each later REAUDIT,
SYNTHESIS, and FINALIZATION likewise requires a newly prepared request and
confirmation. Revise/replacement consumes no continuation round; only a
completed REPAIR does. Consensus workflow serialization format version 3
rejects older workflow bytes without migration.

Validation rejects stale or foreign source identities, cross-session access,
changed lineage, missing immutable material, and exhausted/invalid budgets
before any provider or artifact mutation. Restarted inspection never rebuilds
the one-use capability, and interruption never replays provider work. There is
no installed CLI or interactive continuation route.

Provider input is an `AgentRequest` with the runtime-selected agent, phase,
revision, fixed task text, input, and current candidate. Canonical artifact
input retains the normal provider-read reference authorization and digest
revalidation. Artifact-backed consensus therefore binds the exact physical
session worktree, reconciles only cleanup owned by that identity, and builds
the agent definition from its guarded policy bytes. Candidate-only consensus
with no same-process pending reference cleanup performs no artifact/reference
worktree read and uses the control-owned definition. Each Codex provider call
still opens the exact session binding for its call-local execution capability.
If cleanup is pending, that binding also reconciles only identity-matching
ownership; its definition then uses the binding's session policy. Durable
events contain handles/digests rather than file bytes.
See [Consensus runs](../guides/consensus-runs.md) for examples and user-visible
state meanings, and [Consensus design](../design/consensus-workflow.md) for the
event/authority mechanism.

## Management and inspection methods

```python
StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(session_id: str, text: str) -> dict[str, object]
StoryRuntime.set_runtime_overrides(session_id: str,
                                   overrides: Mapping[str, Any]) -> dict[str, Any]
await StoryRuntime.prepare_runtime_configuration(
    configuration: ConfigurationSnapshot,
    provider_route_generations: Mapping[str, int],
) -> RuntimeConfigurationCandidate
await StoryRuntime.commit_runtime_configuration(
    candidate: RuntimeConfigurationCandidate,
    backend_candidate: BackendRuntimeConfigurationCandidate,
) -> None
StoryRuntime.discard_runtime_configuration(
    candidate: RuntimeConfigurationCandidate,
) -> None
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(session_id: str, debate_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate_context(
    session_id: str,
    debate_id: str,
) -> dict[str, Any] | None
StoryRuntime.inspect_reference_operation(
    operation_id: str,
) -> ReferenceOperationHistory | None
```

A reference-operation ID encodes its owning session. Inspection decodes that
canonical identity and reads only the corresponding session worktree's
persisted reference ledger after fresh READY validation. Malformed identities
raise `ValueError`; `RuntimeSession.inspect_reference_operation()` also rejects
an otherwise valid identity belonging to a different session. Neither case
falls back to another repository.

`RuntimeConfigurationCandidate` is defined in the orchestrator module but is
not a package-root export. Prepare requires a backend and complete quiescence;
commit rechecks candidate ownership and quiescence before swapping the backend
and prompt snapshot. Discard owns no resource.

## Lead front-channel methods

```python
await StoryRuntime.lead_turn(
    session_id: str,
    conversation_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
    interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
    adoption: ExplorationAdoptionSelection | None = None,
    premise_selection: PremiseDecisionSelection | None = None,
) -> LeadTurnResult

await StoryRuntime.propose_specialist_advisory(
    session_id: str,
    conversation_id: str,
    specialist_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
    authoring_scope: AuthoringScope | None = None,
    review_basis: ReviewBasis | None = None,
    authoring_assessment: AuthoringAssessment | None = None,
) -> LeadTurnResult | AuthoringAdmissionNeeded

await StoryRuntime.decide_lead_recommendation(
    decision: LeadRecommendationDecision,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
) -> LeadTurnResult

await StoryRuntime.decide_lead_step(
    decision: LeadStepDecision,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
) -> LeadDecisionResult
```

`lead_turn` and `decide_lead_step` do not accept an agent selector;
`lead_turn` always addresses `lead_orchestrator` and confirmation runs the
specialists already disclosed in the proposal. The advisory method accepts one
canonical specialist ID explicitly. The session and conversation must identify
the same active catalog record. Input is nonempty visible strict UTF-8 and is
capped by the collaboration text limit.

Import the lead values and decision enum from the conversations facade:

```python
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    CollaborationEvidenceAcquisition,
    CollaborationLeadEvidence,
    CollaborationNextAction,
    ArtifactProposalResult,
    ArtifactProposalState,
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
    CollaborationStepState,
    LeadDecisionResult,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadStepDecision,
    LeadRecommendationDecision,
    LeadRecommendationRequestPayload,
    LeadTurnResult,
)
```

They are deliberately not package-root exports.

### Explicit task choices and retained messages

`StoryRuntime.decide_lead_task(decision, *, action_progress_sink=None)` and the session facade return `LeadTaskDecisionResult`. Call synchronous `runtime.inspect_lead_collaboration(session_id, conversation_id)` first and choose a returned task or material reference from `available_tasks` or `selectable_materials`. The session facade takes only `conversation_id`. The decision factories generate a request ID; reuse the same decision for a retry.

```python
from story_writing_agents.lead_task_transitions import LeadTaskAction, LeadTaskDecision

# selected_task is an exact task view returned by collaboration inspection.
decision = LeadTaskDecision.from_task(
    selected_task.ref,
    LeadTaskAction.RESUME,
    text="Continue this task using the selected direction.",
)
result = await runtime.decide_lead_task(decision)
```

`LeadTurnResult.provider_invocation_id` is an optional, keyword-only host
correlation identity. When present, it identifies the exact provider invocation
behind the visible reply. Canonical submitted-call events may retain validated
`ProviderUsage` counters for that invocation; the terminal derives footer totals
from those events. Legacy results without the identity remain valid. This field
adds no provider dispatch, confirmation, Apply, or Git authority.

Inspection returns `CollaborationHistoryProjection`. `available_tasks` contains exact task references, labels, status, scope, goal, source events, pending controls, and submitted call IDs. `selectable_materials` contains whole text, a preview, its exact reference, and available response acceptance/call facts. `public_responses` keeps the public response records separate from the current execution projection; `discussion_context` identifies the current USER task transition, including a Resume or New Task that is still clarifying its intent. Such follow-ups keep the same planned task/revision, selected inputs, and submitted-call budget until a matching fresh proposal is admitted.

Each available task exposes `ref.task_id`, `ref.task_revision`, and lifecycle
`status`. Inspect `current_step_id`, `current_state`, and `next_safe_action`
separately for workflow state. A `discussion_context` with a prospective target
describes a pending Resume/New task revision; matching fresh proposal admission
clears it. The shell displays these values and refreshes after its interactive
decision completes.

`LeadTaskDecisionResult` identifies the action, selected task, and persisted transition event. Its `turn` is optional: Hold and Discard can complete without calling the provider. A returned `LeadTurnResult` may include `discussion` and per-call `public_responses` alongside its existing result and validation fields.

Discussion can return an explanation or a question without proposing specialist work. Hold keeps the selected task and its history. Resume requests a fresh proposal for the same task's next revision. Discard retires the selected idle task and its pending controls. New task creates a new task ID at revision 1 and requires its own proposal and confirmation.

A selected whole draft, discussion answer, or recoverable public response supplies input with an exact source reference and digest. It does not supply an old proposal, specialist reply, READ receipt, final review, Apply, or Commit authority. A source in another conversation must belong to the same session and pass exact snapshot validation.

A rejected final response remains an answer you can read, discuss, or select as
whole input material, even if its action structure or values are invalid. Its
rejection and validation details remain visible. Original responses, corrective
attempts, and accepted follow-ups keep separate call identities and repair
lineage.

A unique public string stays literal. A complete public value of another type
appears with an explicit value-representation label. Plain final text, top-level
JSON strings or scalars, and complete JSON object/array fences can also be
retained under the bounded recovery rules. Invalid action values and unrelated
duplicate keys or nonfinite values do not erase the answer. Ambiguous public
paths, private/debug content, unsafe values, and unavailable or oversized final
text receive an unavailable-text notice. Whole object/array envelopes are never
displayed as a fallback. A closed public slot can survive missing outer closing
delimiters; other malformed envelopes remain unavailable. Recovery reads only
the returned final response, excluding callback streams and private debugging.
Selecting a rejected answer as material does not approve its action.

Exact material references and digests preserve the source response as input.
They provide no proposal, READ, owner, review, Confirm, Apply, or Commit
authority. A trusted persisted notice identifies an actual call whose bounded
public answer is unavailable, including a failure with no returned final text.

Pending Apply/Commit and unresolved operations retain their required controls. Task transitions cannot bypass them or reset consumed calls. Inspect available choices before submitting a decision.

### Explicit interaction mode and adoption

Import current exploration values from `story_writing_agents.lead_exploration`.
Both `lead_turn` facades accept keyword-only `interaction_mode` and
`adoption`. The default is `LeadInteractionMode.ORCHESTRATION`; only an
explicit current user choice of `EXPLORATION` enables the provisional path.
History and provider output cannot select the mode.

`ExplorationAdoptionSelection` contains `source_conversation_id`,
`result_event_id`, `idea_id`, full-source `material_digest`, `target_paths`,
`start_character`, `end_character` and `intent`. The paired range defaults to
`None` for a whole idea; otherwise supply strict integer, nonempty, zero-based
Unicode code-point bounds with an exclusive end. Boolean bounds are invalid.
`intent` is `AuthoringIntent.AUTHORING` by default; use
`AuthoringIntent.REVIEW_ONLY` for review of the exact supplied selection.
Import that enum from `story_writing_agents.specialist_routing`.

Pass `adoption=` in an ordinary orchestration turn. Combining it with exploration
rejects. The latest typed source report may belong to another conversation in
the same session. The selected owner receives a fresh task at revision 1 with
the exact selected input and targets. Authoring retains required drafting and
final candidate review; review-only retains its actual review report without
rewriting. Confirm, Apply and Commit remain independent authority boundaries.

`ExplorationAdoptionCapture` adds the exact source-event SHA-256 and selected-text
SHA-256 to the USER-correlated capture. `material_digest` remains the full idea's
digest. Its current closed wire selection uses `writing_intent` for `intent`
and includes both range keys. No older-format reader is provided.

New intake rejects stale, unavailable, foreign-session or tampered selections
before appending a USER capture or submitting a provider call. Existing captures
retain their original report pin rather than following later reports; missing
or changed pinned sources invalidate current authority. Session-wide pending
decisions, unresolved operations and recovery conditions take priority and name
the owning conversation. They grant no call or round refund.

A `LeadOutcomeKind.EXPLORATION` result exposes its typed `exploration`
report and `interaction_mode`. Reports contain a request/call-bound status,
message, immutable ideas and qualified grounded observations. The runtime
assigns idea identities and material digests. Provisional material cannot be
used as previous draft or confirmation proof. Pending decisions and recovery
take priority without starting another provider call.
See [exploration and adoption](../guides/lead-agent.md#exploration-and-adoption).

### Explicit premise choices

Import the current values from `story_writing_agents.lead_premise_decisions`.
Both `lead_turn()` facades accept keyword-only `premise_selection`. A terminal analyst
report is provisional; its paired `premise_report_ref` supplies the exact conversation,
event, digest and question pins. A USER choice copies that reference and one whole option's
ID/material digest. Delegated minor choices instead use an exact USER allowlist and may
resolve only the report's eligible recommendation. Reject/defer grant no authoring authority.

Selection creates a fresh authoring proposal and separate Confirm. An exact current
authoring-task/revision pair requests current + 1; both absent create a new task at 1.
Same-request retries retain the exact original text and selection arguments and create
no duplicate capture, task or provider calls. Pending/unknown outcomes keep priority.
See [the state table and Python example](../guides/lead-agent.md#choosing-an-unresolved-premise).

### `LeadTurnResult`

```python
LeadTurnResult(
    session_id: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    kind: LeadOutcomeKind,
    message: str,
    proposal: CollaborationProposal | None,
    state: CollaborationStepState | None,
    confirmation_request_id: str | None,
    next_action: CollaborationNextAction,
    evidence: CollaborationLeadEvidence | None = None,
    artifact_proposal: ArtifactProposalResult | None = None,
    artifact_application_request: ArtifactApplicationRequestPayload | None = None,
    story_commit_request: StoryCommitRequestPayload | None = None,
    recommendation_request: LeadRecommendationRequestPayload | None = None,
    trace: ReplyTrace | None = None,
    validation_details: tuple[str, ...] = (),
    story_commit_preparation_request: StoryCommitPreparationRequest | None = None,
    *,
    interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
    exploration: LeadExplorationReport | None = None,
    premise_report: PremiseDecisionReport | None = None,
    premise_report_ref: PremiseReportRef | None = None,
)
```

The five `kind` values are `answer`, `question`, `critique`,
`recommendation`, and `proposal`. Fresh answers and critiques return
`continue_with_lead`; questions return `reply_to_lead`. A fresh recommendation
has `recommendation_request` and `submit_recommendation_decision`. Its
`state`, `proposal`, `confirmation_request_id`, `artifact_proposal`,
`artifact_application_request`, and `story_commit_request` are all `None`;
`evidence` equals the request's basis evidence. These decision controls are
mutually exclusive. `trace` defaults to `None`, preserving callers that omit it.
Historical judgments and runtime recovery notices can still have
kind `recommendation` without a new action; follow their state-specific action.

`validation_details` is an ordered tuple of bounded, content-safe diagnostic
chunks from rejected structured provider attempts. A successful repair can
retain `Original response` findings; an exhausted repair can retain both
`Original response` and `Repair response` findings. Empty means no validation
findings. These details describe schema paths, expected versus actual JSON
types, and fixed rules without provider values or unknown member names; they
grant no confirmation or replay authority.

Domain findings name the rejected property and the required constraint or
relationship at the first violated invariant. They come from the validator that
rejects the reply and retain their nested property paths through decoder wrappers.
The same findings accompany that physical call in captured exchange outcomes.

Ordinary reports include every independently detectable schema finding. A
report that exceeds the diagnostic cap keeps up to 2,048 findings in order and
ends with the fixed `$: additional validation issues omitted` notice. Display
chunks remain below 32,000 UTF-8 bytes each.

Durable validation messages may carry optional
`CollaborationMessagePayload.validation_invocation_id` and
`validation_call_id` alongside their detail chunk. Runtime correlates those
IDs with submitted provider-call facts to restore execution diagnostics in
physical-call order after restart, including concurrent specialist work. Older
messages without the optional IDs remain readable.

A proposal result contains the complete runtime-built
`CollaborationProposal`, state `awaiting_confirmation`, a fresh confirmation
request ID, and next action `submit_confirmation`.

When the lead supplies a valid artifact intent, `artifact_proposal` additionally
contains the runtime-built `ArtifactReviewCandidatePayload`. Before review its
state is `awaiting_consultation_confirmation`. After confirmed review it may be
`reviewing`, `reviewed`, `revision_required`, `rejected`, `superseded`, `stale`,
`failed`, or `recovery_required`. Only `reviewed` carries
`ArtifactReviewedProposal`; that immutable proposal has
`artifact_write_occurred`, `apply_authority_granted`, and
`git_action_occurred` fixed to false.

`artifact_application_request` carries the separate exact Apply request after
successful review, with `next_action=submit_application_decision`. The
optional `story_commit_request` carries an existing local Commit request when
redisclosed, with `next_action=submit_commit_decision`. It defaults to `None`,
preserving existing construction that omits it. These requests still need
their respective structured decisions; neither is consultation authority.

A fresh provider judgment also returns exact durable `evidence`. Runtime derives
its scope, status (`not_applicable`, `verified`, `missing`, `empty`, `incomplete`,
or `conflicting`), and ordered content-free receipts from the compact semantic
scope plus invocation-local authorization facts. Proposal evidence is
identical to `proposal.basis.evidence`. Runtime-generated workflow recovery and evidence-rejection notices can use
`evidence=None`; local recommendation rejection or failure closure can instead
return workflow-meta `not_applicable` evidence with no receipts.

Fresh Lead and specialist replies also expose a content-free `trace`. Its
`role` and `action` identify the reply route; `context_sources` names classes
such as `current_input`, `session_state`, `core_status`, `search_results`, and
`read:writing_artifact`; `recovered_fields` identifies the structured reply
only when repair or fallback was needed; and `origin` is `provider`,
`recovered`, or `runtime_fallback`. The runtime constructs this value after
provider validation. It is persisted with visible collaboration replies and
therefore survives reopen. A runtime fallback always reports `action=none` and
grants no confirmation, Apply, Commit, or other user authority.

Provider JSON is distinct from the Python result above. The built-in Lead
contract is `lead-response-v7`; every branch includes explicit `response_type`,
`response_text`, and `writing_task_scope`. See the
[agent JSON field guide](provider-wire.md) for all result variants, how to build
the schema property, and complete input/response guidance.

Provider scope selects the exact offered `task_reference` object containing
`task_id` and `task_revision`. `proposal_drafting_request` asks for a concrete
proposal; `document_change_request_update` starts, continues, or cancels a
tracked document change. These provider names normalize into the existing
Python recommendation and objective values described below.

Schemas validate shape and allowed values. Runtime checks separately enforce
identity, evidence, routing, confirmation, and write authority. Invalid structured
results follow bounded repair and the visible no-action fallback. No alternate
wire naming or migration is supported.

### `LeadRecommendationDecision`

```python
LeadRecommendationDecision(
    session_id: str,
    conversation_id: str,
    request_event_id: str,
    request_tail_sequence: int,
    request_id: str,
    action_id: str,
    action_revision: int,
    action_digest: str,
    kind: CollaborationConfirmationDecision,
    revision_request: str | None = None,
)
```

Copy the exact current request; do not reconstruct identity from prose:

```python
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    LeadRecommendationDecision,
)

request = result.recommendation_request
assert request is not None
prepared = await runtime.decide_lead_recommendation(
    LeadRecommendationDecision(
        session_id=result.session_id,
        conversation_id=result.conversation_id,
        request_event_id=request.request_event_id,
        request_tail_sequence=request.tail_sequence,
        request_id=request.request_id,
        action_id=request.action_id,
        action_revision=request.action_revision,
        action_digest=request.action_digest,
        kind=CollaborationConfirmationDecision.CONFIRM,
    )
)
```

Both `StoryRuntime` and `RuntimeSession` use the same method signature and
return `LeadTurnResult`; the bound session rejects a foreign session decision.
There is no additional recommendation-result facade. The optional synchronous
`context_event_sink` observes the preparation under the existing callback rules.

| Decision | Result and authority |
| --- | --- |
| `confirm` | Consumes one exact request for one bounded Lead preparation. Returns an exact artifact-review or consultation proposal, or a material question. Subsequent execution/review, Apply, and Commit require separate decisions. |
| `reject` | Records rejection without a preparation call or objective completion. |
| `revise` | Requires nonblank `revision_request`; authorizes only a new recommendation and request, with the same action ID and incremented revision. No candidate, consultation proposal, or objective transition is permitted. |

Preparation requests require substantive, nonworkflow `authoring_scope`.
Consultation preparation covers the union of all affected specialist domains.
Confirm preserves the exact scope, including activity and interpretation; the
current task/revision and cumulative obligations are revalidated. Revise uses
the next offered revision of the same task, preserves existing obligations,
and returns a fresh recommendation requiring a new decision. A fresh complete
supplied candidate can go directly to exact required-specialist review. A task
with DRAFT/REVISE history retains its draft provenance even if this request says
REVIEW. Legacy unscoped pending requests become stale and need fresh planning.

Only Revise accepts revision text. Cancel/EOF in the shell submits no decision.
Confirm does not authorize an automatic loop or renewed confirmation of that
same preparation. Pending requests bind the exact event/tail, revision/digest,
source fingerprint, evidence, input revisions, profiles, policies, and spent
physical calls. For an existing valid session/conversation, mismatched request
or action authority and repeated decisions raise `BackendProtocolError`.
Foreign or missing catalog identities retain their existing lifecycle/catalog
exceptions, such as `ConversationNotFoundError`; the bound session rejects a
foreign session decision with `ValueError`. Current-basis drift closes as
`stale` without preparation. Session/conversation
serialization prevents two concurrent decisions from consuming one request.

For both a direct Lead recommendation and a collaboration-synthesis
recommendation, runtime carries the invocation's one exact session-artifact
binding through response validation and request persistence. The recommendation
basis and source fingerprint are derived through that same binding object; a
fresh binding cannot silently substitute another worktree generation between
provider invocation, validation, and persistence. The physical generation pin
described above covers that whole interval; its repository guards remain short
and no Git lease spans the provider await.

Ordinary input is a real new authoring instruction: runtime atomically records
it and supersedes an unconfirmed recommendation before the new Lead turn. It
never fabricates a structured Confirm. Known preparation results can finalize
locally; a confirmed preparation without provider submission after restart
requires a new request and Confirm. Submitted uncertainty, corrupt history,
failed closure, and uncertain reference cleanup remain recovery-gated. See
[recommendation history](collaboration-history.md#recommendation-preparation-events)
and [recovery](../operations/recovery-and-limitations.md#recommendation-preparation-recovery).

### Editorial memory methods

`await StoryRuntime.record_editorial_decision(session_id, conversation_id, request)`
accepts an `EditorialRecordRequest` and returns `EditorialRecordResult`.
`StoryRuntime.inspect_editorial_state(session_id)` returns the deterministic
`EditorialStateProjection`. The session-bound forms omit `session_id`. Value contracts
are imported from `story_writing_agents.editorial_state`.

DIRECTIVE creates a SELECTED record; PROPOSE creates a PROPOSED record. SELECT,
REJECT and SUPERSEDE name the exact current `EditorialRecordRef`. REVISE also provides
replacement content and an explicit PROPOSED or SELECTED approval. The receipt contains
`session_id`, `record_id`, `revision`, `conversation_id`, `event_id` and `record_digest`.
Successful transitions create another retained revision with immutable origin.
The record kind and origin-task identity stay fixed across revisions. SELECT accepts
only a current proposed record; restoring discarded intent requires explicit REVISE.

Request IDs bind exact retry arguments. An unchanged retry does not append another
decision. Stale or foreign references and invalid provenance cannot overwrite current
records; pending workflow controls retain priority. Recording and inspection do not
invoke providers or apply artifacts. Provider assertions and raw conversational approval
cannot perform the typed decision controls.

Inspection separates current approval, all retained revisions, provenance gaps and
historical matching application facts. Lead context cites exact selected revisions and
pins them in host-owned proposal basis references. Confirm revalidates those references;
final review inherits them without substituting newly sampled memory. Application facts
identify historical task/candidate use and do not supply current artifact contents or
prove ambiguity or setup has been resolved.

Scope values come from `story_writing_agents.editorial_scope`: `EditorialScope`,
`EditorialScopeTarget`, and derived `EditorialScopeCoverage`. Known targets use canonical
artifact paths; finite inclusive ranges also require the exact source SHA. Unknown scope
is an explicit clarification state. `EditorialRecordDraft.scope` names application
coverage independently of provenance references.

`EditorialRecordRequest.relation_decisions` carries typed `EditorialRelationDecision`
values (coexist, conflict, replace), each naming an exact current counterpart reference,
optional intersecting scope, and explicit user reason. A nonempty relation request also
requires `expected_editorial_context_digest`, obtained from current inspection's
`context_digest(refs)` for the relevant records. Empty relation requests omit that pin.
Text-only revision preserves declared scope; explicit unknown-to-known clarification is
allowed. Partial replacement creates a new directive and exact replacement relation so
the previous decision's remaining coverage survives.

Inspection adds `scope_states`, `scope_relations`, and `application_attempts`. These are
deterministic history projections, distinct from qualified current source observations.
Nonempty proposal/recommendation editorial basis requires the host-created
`editorial_context_digest`; empty/no-memory basis retains its existing encoding.
Confirmation revalidates both exact references and affecting topology, and checks finite
source anchors through the guarded owning repository seam. No migration or earlier
nonempty codec acceptance is provided.

See [editorial decisions](../guides/lead-agent.md#editorial-decisions) and the
[history contract](collaboration-history.md#editorial-memory).

### Contextual authoring objectives

The public Python API calls the tracked document change an objective. Provider
JSON uses `document_change_request_update` only when a Lead result starts,
continues, or cancels that request. Omission makes no transition. These illustrative
wire objects use an offered pending request ID where required:

```json
{"update_type":"start_request","target_artifact_paths":["core_documents/global_outline.md"],"requested_workflow_step":"ask_clarification"}
```

```json
{"update_type":"continue_request","document_change_request_id":"<offered-pending-request-id>","requested_workflow_step":"draft_document_change_proposal"}
```

```json
{"update_type":"cancel_request","document_change_request_id":"<offered-pending-request-id>"}
```

`start_request` requires nonempty exact canonical target scope. The runtime assigns
an ID and freezes the exact originating user message. If targets are unresolved,
Lead asks a question before starting a request. `continue_request` and
`cancel_request` select only a pending request owned by the current conversation;
they cannot replace its origin or targets. Visibility in session history does not
transfer ownership.

| `requested_workflow_step` | Required provider result |
| --- | --- |
| `ask_clarification` | `response_type: "clarification_question"` |
| `consult_specialists` | `response_type: "specialist_consultation_proposal"` with `specialist_consultation_proposal` |
| `draft_document_change_proposal` | `response_type: "artifact_change_proposal"` with `artifact_change_proposal`; changes cover the frozen targets exactly |

Once drafting the document-change proposal is selected, bounded same-turn repair
cannot downgrade it to advice. A failed draft leaves the request pending.
`cancel_request` selects no work and cannot accompany a proposal. These values
normalize into the Python `objective_intent` model; persistence codecs remain
independent of the provider contract.

Draft IDs select only runtime-offered visible messages from the owner
conversation. `ArtifactDraftSource` identifies the conversation, event, and
SHA-256 of the complete exact UTF-8 message, including its original whitespace
and line endings. There is no extracted-draft or normalized-text identity.
An empty `continue` source list retains the previous selection. Draft sources
supply context, never artifact evidence or confirmation authority.

The stored source message and digest remain intact. Runtime may append a
separate visible notice about unfinished authoring; that notice is not part of
the source message. Candidate content is prepared separately and may omit
conversational framing. It need not copy the whole Lead message, and runtime
notices must not become story content. Selecting an earlier Lead draft does
not replace the required current-task specialist drafting coverage. A fresh,
finished user-supplied candidate can enter review without fabricated drafting
when the Lead explicitly discloses that interpretation.

`CollaborationHistoryProjection.artifact_objectives` retains these objectives;
`offered_draft_sources` supplies the current selectable source identities.
Advice, consultation, review, rejection, staleness, failure, and null intent
leave an objective `OPEN`. Only a matching validated `APPLIED` or `UNCHANGED`
result for its bound candidate makes it `SATISFIED`. Explicit cancellation
makes it `CANCELLED`; Commit is independent of objective completion. See the
[persisted contract](collaboration-history.md#contextual-artifact-objectives).

An artifact objective first created by structured recommendation Confirm uses
`ArtifactObjectiveFactsV2`/`ArtifactObjectivePayloadV2`, preserving the exact
request and decision event origin. Ordinary authoring retains v1 user-event
origin and the exact originating user-event content as its request. Confirm can
continue an existing v1 objective without changing its
immutable facts or codec. The preparation execution origin is separate from
objective provenance; there is no synthetic user message.

The public objective values are additive exports of
`story_writing_agents.conversations`; the package-root facade is unchanged.
Retained workspace policies receive the current runtime overlay without file rewrites.
Specialist and synthesis schemas remain separate from `lead-response-v7`.

`LeadProviderOutcome` preserves its Python positional order:

```python
LeadProviderOutcome(
    kind, message, scope, evidence_status, evidence_ids,
    proposal=None, artifact_intent=None, objective_intent=None,
    recommended_action=None, *, authoring_scope=None,
)
```

`objective_intent` remains the eighth positional slot and `recommended_action`
the ninth. Optional `authoring_scope` is keyword-only. These Python values are
richer than the provider contract because runtime adds durable metadata after
validation. JSON member order does not define positional
Python arguments.

### `LeadStepDecision`

```python
LeadStepDecision(
    confirmation_request_id: str,
    session_id: str,
    conversation_id: str,
    step_id: str,
    proposal_id: str,
    proposal_revision: int,
    proposal_digest: str,
    kind: CollaborationConfirmationDecision,
    revision_request: str | None = None,
)
```

`kind` is `confirm`, `reject`, or `revise`. Revise requires a nonempty visible
`revision_request`; Confirm and Reject reject that field. Runtime loads the
authoritative request event and requires the decision to match its exact tail,
request, proposal identity, revision, and digest. Callers never supply sequence
authority.

Confirm also rederives current references and lead-evidence facts, including
core status, used READ boundaries, and the authorized-source fingerprint for a
zero-hit SEARCH, plus required reviewers, route, profiles, context policy, and
budget facts. Drift rejects the decision before proposed work. Ordinary
collaboration events appended by the same lead step do not falsely invalidate
a mixed conversation/writing zero-hit receipt. After confirmation is durable,
runtime creates and consumes one private,
nonserializable, process-local start capability. It atomically appends
`running` plus every ordered dispatch before calling providers. No public
property exposes or reuses the capability.

### `LeadDecisionResult`

```python
LeadDecisionResult(
    session_id: str,
    conversation_id: str,
    step_id: str,
    decision: CollaborationConfirmationDecision,
    proposal: CollaborationProposal,
    turn: LeadTurnResult,
)
```

| Decision | Authoritative `turn` |
| --- | --- |
| `confirm` | Terminal `completed`, `failed`, `interrupted`, `outcome_unknown`, or `recovery_required`; otherwise a separately confirmable synthesis recommendation or fresh round-two `awaiting_confirmation` proposal. |
| `reject` | `recommendation` in `rejected`, with `continue_with_lead`. |
| `revise` | Replacement lead outcome after the old revision becomes `superseded`. |

An ordinary replacement proposal or direct outcome from Revise retains the
step ID. A replacement proposal increments the proposal revision and has fresh
proposal/digest/request identities. A
successful round-one synthesis may likewise return revision 2 on the same
step; it includes the original inputs plus round-one reply-event inputs and
requires fresh confirmation. A replacement or ordinary synthesis may instead
return `recommendation_request` on a fresh preparation step. A Revise-origin
recommendation binds the exact old proposal ID/revision/digest and actual
Revise chain, with a null source execution attempt. A synthesis recommendation
binds its exact source proposal and execution attempt. Confirming either
authorizes only preparation. Follow
`result.turn.next_action`.

An artifact candidate follows the same exact structured decision. Reject marks
the artifact candidate `rejected` without a provider call. Revise marks the old
candidate `superseded`; the replacement lead turn may return no candidate or a
new candidate with fresh identity, digest, and request. Confirm first
revalidates the candidate's evidence and complete base state. Drift returns a
superseded collaboration turn containing artifact state `stale`, makes zero
reviewer calls, and changes no artifact.

### `propose_specialist_advisory`

This method constructs a provider-free proposal for exactly one specialist:

```python
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain, AuthoringActivity, AuthoringScope, ReviewBasis,
)

# Synthetic complete outline candidate; runtime rebinds this fresh task ID.
scope = AuthoringScope(
    activity=AuthoringActivity.REVIEW,
    target_paths=("core_documents/global_outline.md",),
    domains=(ArtifactReviewDomain.PLOT_STRUCTURE,),
    interpretation="Review the complete outline supplied in this message.",
    task_id="outline_review",
    task_revision=1,
)
advisory = await runtime.propose_specialist_advisory(
    "novel",
    conversation_id,
    "plot_architect",
    "Review this complete outline: A courier loses a letter, retraces the route, "
    "and returns the recovered letter. Assess causal order and pacing.",
    authoring_scope=scope,
    review_basis=ReviewBasis(
        candidate_input_ids=("user_input",),
        criteria="Check causal order and pacing of the supplied complete outline.",
    ),
)
```

`authoring_scope` and `review_basis` are keyword-only on both facades. Missing
scope or `activity=workflow` raises `ValueError`; the defaults do not preserve
unscoped calls. The selected specialist must cover all semantic and known path
domains. Use `lead_turn()` for interpretation or work spanning multiple domains.
The advisory API accepts historical authoring scope versions1/2; explicit
scope3 contributions and captured premises use the confirmed Lead flow.

A new API task ID is rebound runtime-side; inspect `advisory.proposal.authoring_scope`
for the actual identity. To continue an offered active task, pass its exact
current identity/revision; stale or applied revisions are rejected.

For drafting, use `activity=AuthoringActivity.DRAFT`, an adequate authoring brief,
and omit `review_basis`. Review requires concrete nonempty material; the direct
supplied-text alias is `user_input`. A status receipt or a request to write a
candidate is insufficient review material. Confirm the exact returned proposal
using the decision flow above; this call alone starts no specialist.
Official drafting also requires `authoring_assessment`; handle the
`AuthoringAdmissionNeeded` result described above before accessing a proposal.

It returns `awaiting_confirmation` without calling the lead provider. The
proposal has step kind `specialist_advisory`, one assignment, and zero
synthesis reservation. Confirm invokes that specialist and returns its typed
message directly. It cannot chain to round two. Runtime rejects any semantic
or known-path domain that the selected specialist cannot cover.

### Confirmed execution results

Each persisted successful specialist reply has exact fields `assignment_id`, `mode`,
`assessment`, `message`, and the ordered assignment `input_ids`. Draft mode
returns `drafted` material or `insufficient_evidence`; review mode returns
`supports`, `concern`, `conflict`, or `insufficient_evidence`. Draft material
is not a supporting review or task completion. After every
assignment succeeds, a normal consultation performs separately budgeted lead
synthesis. The synthesis cites every reply event in order and retains all input
IDs from `concern`, `conflict`, or `insufficient_evidence` replies as
unresolved; `drafted` is exempt from that unresolved-input requirement. Its
`consultation-summary-v4` provider result permits `proposal_drafting_request` or
`follow_up_consultation`, mutually exclusively. It may recommend one separately confirmed
preparation or request one strict same-lineage round-two intent. With neither,
it returns an answer. Historical synthesis payloads remain readable; an action
is persisted as a separate request.

Synthesis separately repeats the proposal's exact `authoring_scope`,
story-specific scope, evidence status, and evidence IDs; reply event IDs are attribution, not story
evidence. One to three assignments run concurrently with isolated reference/context
authority and disjoint call reservations. If one fails, siblings finish and
known replies are preserved, but synthesis is not attempted. Cancellation
cancels and joins siblings, persists knowable results, and re-raises.

For artifact review, runtime derives the canonically ordered reviewers from the
candidate's mandatory and additional domains. Every reviewer receives the
same strict serialized candidate and the same disclosed evidence; no reviewer
can rewrite it. After every reviewer succeeds, the provider Lead synthesis
returns only:

```text
decision: propose | revise | reject
message: nonempty visible text
```

Runtime preserves all reply attribution and unresolved non-supporting inputs,
uses `message` as `rationale`, adds the fixed no-change `user_impact`, and sets
`limitations` to `No additional limitations were reported.` when there are no
unresolved IDs. Otherwise it reports `Unresolved reviewer inputs remain: `
followed by the exact unresolved input IDs.

`propose` produces `ArtifactReviewedProposal` and artifact state `reviewed`.
`revise` produces `revision_required`; `reject` produces `rejected`. Partial
review or invalid synthesis produces `failed`. Before successful `propose`
finalization, runtime projects the exact candidate over a fresh complete-state
capture; drift produces `stale`. None of these paths writes an artifact or
grants later execution authority. Specialist assessments are evidence, not a
vote, and synthesis cannot discard a concern because more peers returned
`supports`.

### Apply and Commit decision methods

```python
await StoryRuntime.decide_artifact_application(
    decision: ArtifactApplicationDecision,
) -> ArtifactApplicationDecisionResult
await StoryRuntime.decide_story_commit(
    decision: StoryCommitDecisionRequest,
) -> StoryCommitDecisionResult
await StoryRuntime.reprepare_story_commit(
    request: StoryCommitPreparationRequest,
) -> StoryCommitRequestPayload
```

Import the decision and result values from
`story_writing_agents.conversations`. `RuntimeSession` exposes the same three
method signatures and rejects a decision or preparation request owned by
another session.

A result-only successful Apply tail projects
`reprepare_commit_request` and returns its exact
`story_commit_preparation_request` before ordinary Lead text appends a
message or calls a provider. An extant exact Commit request instead
projects `submit_commit_decision` and has no preparation request.

`reprepare_story_commit()` accepts a structured
`StoryCommitPreparationRequest` with the session, conversation, step,
original Apply-result event, application ID and request digest, and current
tail event IDs. It is available only from a typed reprepare state. It
revalidates those durable facts, prepares a candidate, and publishes a
new Commit request without committing; that request needs its own fresh
structured decision. After a structured Revise, the new candidate carries a
durable revision ordinal in its proposed local commit message so a retired
candidate ID is not reused; the applied artifact evidence is unchanged.
After restart, an extant old request stays visible,
but a decision without its process-local binding closes stale/reprepare
before Git mutation; re-preparation then creates a fresh request. Mismatched or
uncertain owner facts require inspection.

Apply requires the exact authoritative request tail and same-process one-use
authority. Immediately before any candidate revalidation or write, runtime
freshly binds the exact session worktree. If binding or physical-root
validation fails after the Apply decision became durable but before the
repository's `apply_artifact_changes()` method starts, the returned and
persisted application result is
`recovery_required` with `operator_recovery`; artifact application does not
start and no provider call is attempted. Once that method has started, an
unclassified root, security, or format failure is conservatively
`outcome_unknown` with `inspect_and_resolve_outcome`; the exact application
must be inspected before any new write. This includes guard-exit rejection
after the repository method returned: its optimistic terminal result is not
published and no Commit request is created. A terminal recovery condition
reported by the repository remains `recovery_required`. Successful application affects
only the correlated session worktree and its application ledger. Control-root
and sibling-session bytes remain outside the operation.

Commit remains a distinct decision and authority boundary. Its internal
service carries the successful Apply binding's physical worktree identity
into candidate preparation, authorization, and execution. Runtime retains
one six-descriptor physical-generation pin from the Apply guard through
candidate preparation and result/request publication. Preparation may
create an immutable candidate but cannot stage or commit. A successful
handoff publishes the Apply result and exact Commit request in one
`append_batch` operation. If that append raises, runtime reads back the
exact batch, event IDs, payloads, prior tail, and final length. A proven
pair is accepted; proven absence permits a result-only Apply record with
`reprepare_commit_request`; anything unproved remains
`outcome_unknown` for inspection, never replay. The result-only fallback
also requires exact readback. The pin transfers to the process-local
pending Commit owner only after verified pair publication; otherwise it
closes. If that owner is lost later, the durable request remains visible,
but a structured decision closes stale/reprepare before Git mutation.
The pin grants no repository access or exclusion from external Git.

The retained identity-bound Commit service is creator-process/thread affine.
Prepare, Authorize, Reject, Revise, Execute, Inspect, Recover, and internal
repository-access binding reject a foreign thread or forked child before
reading service state, taking service or history locks, or reaching
filesystem/Git I/O. The Commit service also
independently holds its session-history lease and places semantic READY
checkpoints around execution, inspection, recovery, and every durable history
transition. Normal phases require a clean READY index; after intentional
staging, phase-aware topology checks instead require exactly the candidate
index/worktree, branch and internal-ref tips, registered worktree, main-root
HEAD/status, and bound physical root. READY/history resource loss detected
before the `running` record is durable maps to `recovery_required`; failure
after it is durable maps to `outcome_unknown`. A candidate/root replacement
found before execution remains candidate drift and requires a fresh request.
Candidate and clean-index revalidation precedes revision-history inspection,
so an externally pre-staged index detected before `running` is durable is stale
and requires repreparation; it is not treated as a recovery-only staged
operation. Failure while publishing the final `committed` record also durably
preserves the same operation as `outcome_unknown` when the phase topology can
still be proved. Apply never implies Commit, and neither operation pushes,
merges, or grants a provider direct filesystem or Git authority.

Identity-bound candidate preparation borrows the live session-history READY
validator for canonical capture through the binding's one stable guarded
repository generation. The artifact snapshot, Git invariants, and create-only
candidate append stay under that same lease and READY authority; no second
repository generation or independently reacquired Git lease is substituted.
Likewise, merely constructing `running` in memory is not durable Commit start.
Pre-durable error mapping remains in force until the history append succeeds.

A structurally invalid or mismatched decision is rejected before publication
and preserves its published pending owner. If decision publication itself
fails, that owner and pin likewise remain available for a later exact decision.
After decision publication succeeds, terminal cleanup retires precisely the
owner observed when processing began—even when its request no longer matches
the durable tail—and never closes a replacement installed concurrently.
A definitive conversation archive, including an already-archived command,
likewise retires the exact pending Commit owners; verified compensation that
unarchives a focused conversation does not perform that retirement.

The identity-bound service applies the same Apply-root requirement to
`inspect()` and `recover()`, checking before and between their owner reads and
before every possible history transition. A replaced root returns
`recovery_required`; neither method reports or advances a staged, committed,
or other durable operation state from replacement resources.

Terminal inspection and recovery require the current session to be READY but
perform no history transition. A historical `committed` operation remains
inspectable after later commits: its recorded commit must exactly match its
candidate and equal or be an ancestor of the current READY branch tip, rather
than remain current `HEAD`. Reset/divergence, a missing or mismatched commit,
or candidate mismatch fails `recovery_required`. Historical `rejected` and
`recovery_required` records likewise remain immutable inspectable terminal
evidence under current READY.

If ordinary READY fails only because the index is staged, inspection and
recovery may enter a recovery-only read admission under one lease. Admission
requires the addressed, exactly correlated live Story Commit/candidate and
revalidates the branch and internal ref, candidate tree, exact index and
matching worktree, no untracked files or external Git operation, session-root/
registered-worktree identity, main HEAD, and the main-worktree status plus
allowed-path content fingerprint before and after. Unaddressed reconciliation
also requires exactly one eligible operation/candidate pair. Only that Story
Commit owner can receive or advance the correlated staged state. Arbitrary
non-READY dirty or staged state remains a session-resource recovery failure;
ordinary READY itself is unchanged and strict.

A successful Apply result and its follow-on Commit request are published as
an exact pair under the Apply binding. If publication fails, exact readback
proves whether the pair landed. Proven absence permits a durable result-only
`reprepare_commit_request` state across restart; uncertain readback requires
inspection. Repreparation publishes a fresh request and does not authorize
Commit without a fresh structured decision.

Known provider work is not replayed merely because session resources disappear
during local closure. Binding loss while finalizing a completed artifact review
durably closes the artifact and collaboration state as `recovery_required` and
does not issue Apply authority. Binding loss while proving recommendation or
reference closure returns the typed blocking recovery result and preserves the
original attempt. Guarded explicit-reference binding loss is likewise preserved
through preparation, read action, failure, terminalization, and cleanup, so
Lead returns `recovery_required` with no provider or fallback call. This
resource failure is not semantic staleness: true source,
basis, relationship, or candidate drift remains `stale` and requires fresh
planning/review, while loss of the exact bound resources requires recovery.
Repair authoritative session resources/history before continuing.

For those guarded references, creation publishes the operation or invocation
owner before a fallible access exit. A nonempty parsed batch acquires and
validates its non-inheritable physical-worktree descriptor before the
repository can publish `CAPTURED`; successful construction transfers that pin
to the operation exactly once. Every failure after publication defers cleanup.
A process-shared `StoryRuntime` registry keys content-free ownership
by control workspace, session, physical worktree identity, and operation while
transient bytes/results, authorization, capability authority, and retained
binding access are closed. The operation retains one private, non-traversed
duplicated directory descriptor that pins the exact original worktree during
deferred cleanup. This descriptor is never read, serialized, exposed to a
provider, or treated as repository authority. It closes exactly once on a
terminal success or failure, reconciled completion, stale-owner abandonment,
or an unretained preparation path. The next same-process artifact-sensitive
boundary reconciles only an owner whose physical identity matches its fresh
binding. A replacement
root receives neither the old cleanup nor its operation ID in the replacement's
live-owner exclusion set; the stale transient owner and its pin are closed and
the original durable history is unchanged. After restart, the next such
boundary reconciles durable nonterminal orphans instead. Reconciliation is
invocation-first, operation-second, idempotent, non-replaying, and fail-closed
on corrupt evidence. See
[Explicit-reference evidence recovery](../operations/recovery-and-limitations.md#explicit-reference-evidence-recovery).

If the post-capture registry callback fails, transient authorization and the
unretained descriptor close without nested repository cleanup, while the
durable operation remains at `REQUESTED` → `CAPTURED`. The callback failure is
re-raised unchanged. A later artifact-sensitive boundary discovers that
content-free operation as a restart orphan and terminalizes it without provider
replay.

Reference deferral first detaches every pending invocation, authorization,
repository, and repository-access authority. It then attempts every applicable
content, authorization, and owner-pin cleanup, preserving an active primary
failure and recording only content-free cleanup exception types.

Guarded reference preparation preserves `WorkspaceSecurityError` and binding
failures for runtime recovery mapping instead of flattening them into an
ordinary reference workflow failure. Unguarded public repository composition
retains its fixed `ReferenceOperationFailure` conversion. Thus loss of the
selected session root remains a typed recovery boundary and does not authorize
provider work or fallback. If either preserved failure arrives after guarded
authority publication, runtime closes transient content and retains only the
content-free cleanup operation for fresh-binding reconciliation.

### Free text and recovery

Calling `lead_turn(..., "Yes")` is contextual conversation, never structured
confirmation. When a consultation proposal is pending, runtime atomically
appends the user message and `superseded` state before any next provider call.
Idle application exit makes no call and leaves the pending request unchanged.

Pending Apply or Commit takes precedence: `lead_turn()` and
`propose_specialist_advisory()` return the unchanged exact request without a
provider call or history append. After a known terminal Apply/Commit cycle,
fresh authoring can create new requests while retaining previous correlations
and objective completion. Unknown or recovery outcomes block new work.

Before accepting new text, `lead_turn` normalizes authoritative state:

- confirmed without its process-local capability becomes
  `reconfirmation_required` with the unchanged proposal/digest and a fresh
  request;
- running with a known result performs deterministic local finalization with
  zero provider calls;
- corrupt history or uncertain call-boundary facts returns a blocking
  `recovery_required` result without invoking the provider.

Unresolved `running` with no live owner and no authoritative result becomes
`outcome_unknown`; it is never replayed. A `running` result batch is finalized
locally to `completed`, `failed`, or `interrupted`. The runtime clears live
execution markers and releases bound-agent resources on every exit path. Each
retained bound-agent use is released by one separately joined task. Cancellation
cannot interrupt or duplicate that release: the runtime shields and joins it,
then propagates the original cancellation after cleanup. A simultaneous
release failure is recorded on that cancellation; without cancellation, the
release failure remains the reported failure.

### Context event sink

`context_event_sink` is an optional synchronous callback for content-free
`ContextExecutionEvent` values. It must return exactly `None`. Coroutine,
async-generator, asynchronously callable, awaitable-returning, non-`None`, or
exception-raising observers fail under the agent-context lifecycle rules. The
events contain fixed identities, states, and bounded counts—not prompts,
queries, paths, excerpts, provider responses, or credentials.

Do not confuse this public content-free observer with detailed action progress.
The supported `StoryRuntime` and `RuntimeSession` Lead methods above and below
expose an additive keyword-only `action_progress_sink` seam for
package-internal-typed events. Its sink type is not exported from the package
root or `agent_context` facade, and the seam is unsupported for general
application observation; the interactive controller is its intended consumer.
Python callers can still supply and retain the callback events and then own
their access control, retention, and cleanup. Events carry exact SEARCH query
text, requested resource/source kinds and optional relative scope prefix, plus
SEARCH/READ relative locators, boundaries, counts, and truncation. They carry
no snippets, provider answers, absolute paths, credentials, or hidden
reasoning. Terminal capture or scrollback may nevertheless retain those exact
queries and relative locators.

## Bound session methods

```python
RuntimeSession.apply_directive(text: str) -> dict[str, object]
RuntimeSession.set_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]
RuntimeSession.inspect() -> dict[str, Any]
await RuntimeSession.record_editorial_decision(
    conversation_id: str,
    request: EditorialRecordRequest,
) -> EditorialRecordResult
RuntimeSession.inspect_editorial_state() -> EditorialStateProjection
RuntimeSession.inspect_debate(debate_id: str) -> dict[str, Any]
RuntimeSession.inspect_debate_context(debate_id: str) -> dict[str, Any] | None
RuntimeSession.inspect_reference_operation(
    operation_id: str,
) -> ReferenceOperationHistory | None
RuntimeSession.submit_consensus_prompt(
    conversation_id: str, prompt: str,
    *, authoring_assessment: AuthoringAssessment | None = None,
    authoring_direction: str | None = None,
) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded
RuntimeSession.submit_consensus_draft(
    conversation_id: str, draft: str,
    *, intent: AuthoringIntent = AuthoringIntent.AUTHORING,
    authoring_assessment: AuthoringAssessment | None = None,
    authoring_direction: str | None = None,
) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded
RuntimeSession.inspect_consensus(
    conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
await RuntimeSession.decide_consensus(
    decision: ConsensusRuntimeDecision,
) -> ConsensusRuntimeDecisionResult
await RuntimeSession.confirm_consensus(
    decision: ConsensusRuntimeDecision,
) -> ConsensusRuntimeDecisionResult
RuntimeSession.interrupt_consensus(
    conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
RuntimeSession.resume_consensus(
    conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
RuntimeSession.request_next_consensus_phase(
    conversation_id: str, workflow_id: str,
) -> ConsensusRuntimeInspection
RuntimeSession.continue_consensus_deadlock(
    conversation_id: str,
    source_workflow_id: str,
    source_result_event_id: str,
    expected_source_tail_sequence: int,
    *,
    mode: ConsensusContinuationMode,
    ruling: str,
    added_revision_rounds: int,
    authoring_assessment: AuthoringAssessment | None = None,
) -> ConsensusRuntimeInspection | AuthoringAdmissionNeeded
await RuntimeSession.lead_turn(
    conversation_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
    interaction_mode: LeadInteractionMode = LeadInteractionMode.ORCHESTRATION,
    adoption: ExplorationAdoptionSelection | None = None,
    premise_selection: PremiseDecisionSelection | None = None,
) -> LeadTurnResult
await RuntimeSession.propose_specialist_advisory(
    conversation_id: str,
    specialist_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
    authoring_scope: AuthoringScope | None = None,
    review_basis: ReviewBasis | None = None,
    authoring_assessment: AuthoringAssessment | None = None,
) -> LeadTurnResult | AuthoringAdmissionNeeded
await RuntimeSession.decide_lead_recommendation(
    decision: LeadRecommendationDecision,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
) -> LeadTurnResult

await RuntimeSession.decide_lead_step(
    decision: LeadStepDecision,
    *,
    action_progress_sink: ContextActionProgressSink | None = None,
) -> LeadDecisionResult
await RuntimeSession.decide_artifact_application(
    decision: ArtifactApplicationDecision,
) -> ArtifactApplicationDecisionResult
await RuntimeSession.decide_story_commit(
    decision: StoryCommitDecisionRequest,
) -> StoryCommitDecisionResult
await RuntimeSession.reprepare_story_commit(
    request: StoryCommitPreparationRequest,
) -> StoryCommitRequestPayload
```

The bound methods reject decisions and preparation requests for another session. Bound
reference inspection likewise rejects an operation ID that does not belong to
its session.

## Management example

```python
from story_writing_agents import StoryRuntime, Workspace

workspace = Workspace("/path/to/initialized-story")
runtime = StoryRuntime(workspace)

runtime.apply_directive("novel", "[SYS: LANG=EN]")
session = runtime.session("novel")
snapshot = session.inspect()
print(snapshot["session_id"])
```

There is no package-root `Runtime` or `Session` alias. Import the explicit
`StoryRuntime` and `RuntimeSession` names.

## Removed and unavailable execution surfaces

No alternate or forwarding entry point restores `converse`,
`converse_with_retrieval`, `run_prompt`, or `run_draft` on the runtime classes.
There is no unconfirmed/public raw specialist-dispatch, debate-start,
artifact-application bypass, general Git, push, merge, or one-turn bypass
method. Consensus
is available only through the separately confirmed Python methods above; it
has no installed shell command.

Confirmation of a consultation cannot authorize one of those later effects.
The current interactive artifact Apply and local Git Commit paths each require
their own separately disclosed, structured confirmation.

<a id="runtime-diagnostic-side-effect"></a>

## Diagnostics and side effects

Runtime construction creates no diagnostic file. Directive, configuration,
and inspection calls best-effort append bounded operational facts to
`debug_logs/runtime.jsonl`. Diagnostic failure is swallowed and cannot replace
the method's result or exception.

Lead turns and confirmed consultations append authoritative schema-v2 user,
typed evidence-bound lead judgments or failures, dispatch, typed
reply/synthesis, provider-call-accounting, result, and state events as
applicable. Attached references create content-free
reference-operation evidence and send authorized selected bytes to the
configured provider for each addressed invocation. Conversation, consultation,
review, and consensus methods never publish an artifact or mutate Git; only the
separate exact Apply and Commit decision methods can perform those effects.

<a id="context-lifecycle-observer"></a>

### Session context capacity failures

`conversations.session_context.SessionContextCapacityError`는 정확한 맥락의 용량을
검사하는 기존 `ValueError` 경계다. Host가 제공하는 진단 필드는 `stage`,
`actual_utf8_bytes`, `maximum_utf8_bytes`, `excess_utf8_bytes`다.
`known_envelope`와 `provider_payload`의 최대값은 524,288이며 다른 context 단계는
262,144를 유지한다. 최종 payload 생성 전에 할당이 실패하면 actual/excess를 알 수
없을 수 있다. 측정값을 완성된 provider payload 크기로 해석하기 전에 단계를 확인한다.

History/editorial preflight and the final `provider_payload` guard have distinct
stages. The final guard uses the existing canonical payload encoding after context
rendering and immediately before both initial and continuation provider submission.
It counts UTF-8 bytes, including JSON escaping and envelope fields, with equality
allowed. Output-schema and native transport bytes are separately delivered and
are outside this existing payload cap. Do not add overlapping component estimates
to the final measured total.

The orchestration failure path retains its current user/control/checkpoint and
failure-audit semantics. Capacity rejection submits no offending provider request
and grants no approval or successful progress. The interactive shell renders the
content-free capacity summary through its trusted error-notice path. Archive and
restart preserve history according to current continuity rules; neither is a
capacity bypass or a selective-delivery API.

## Related APIs

- [Lead-agent guide](../guides/lead-agent.md)
- [Schema-v2 collaboration history](collaboration-history.md)
- [Agent-context design](../design/agent-context.md)
- [Artifact references](../guides/artifact-references.md)
- [Conversation search](conversation-search.md)
- [Conversation archives and transfer](conversation-archives.md)
- [Package API](package-api.md)
- [Agent contracts](agent-contracts.md)
