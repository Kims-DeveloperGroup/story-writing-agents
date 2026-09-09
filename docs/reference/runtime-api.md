# Runtime and lead-orchestration API

`StoryRuntime` is the public management, configuration, inspection, and lead
front-channel facade. `RuntimeSession` binds those operations to one session
ID. The runtime can obtain one strict lead outcome, disclose a consultation,
construct an exact complete-state-bound artifact candidate, and execute its
one-to-three specialist assignments after an exact structured confirmation.
Artifact review can produce an immutable reviewed proposal. The runtime also
exposes a stepwise, separately confirmed consensus workflow through Python.
Neither workflow applies or mutates an artifact, grants Apply authority, or
performs Git work.

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

## Phase-confirmed consensus methods

```python
StoryRuntime.submit_consensus_prompt(
    session_id: str, conversation_id: str, prompt: str,
) -> ConsensusRuntimeInspection
StoryRuntime.submit_consensus_draft(
    session_id: str, conversation_id: str, draft: str,
) -> ConsensusRuntimeInspection
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
) -> ConsensusRuntimeInspection
```

`RuntimeSession` exposes the same names without `session_id`. Its decision
methods reject a decision belonging to another session. Import their values
from `story_writing_agents.consensus`:

```python
from story_writing_agents.consensus import (
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

Submission creates a durable DRAFT request and returns without calling a
provider. Prompt and supplied-draft input both require confirmation; the latter
is transformed by the Execution Specialist into newly authored `r0` before
audit. `request_next_consensus_phase()` creates, but does not execute, the
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
`publication_allowed` is always false. A terminal deadlock reports
`next_action=continue_deadlock`.

The normal phase graph is DRAFT → AUDIT → SYNTHESIS. Lead synthesis selects
REPAIR → REAUDIT → SYNTHESIS, FINALIZATION, or deadlock. Every phase is a new
request and confirmation. Audits invoke all three specialists against the same
revision; the lead synthesizes and finalizes but does not vote. Finalization is
a read-only handoff to a separately confirmed artifact Apply, followed later
by a separately confirmed Git Commit.

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
revalidation; durable events contain handles/digests rather than file bytes.
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
) -> LeadTurnResult

await StoryRuntime.propose_specialist_advisory(
    session_id: str,
    conversation_id: str,
    specialist_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
) -> LeadTurnResult

await StoryRuntime.decide_lead_step(
    decision: LeadStepDecision,
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
    LeadTurnResult,
)
```

They are deliberately not package-root exports.

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
)
```

The five `kind` values are `answer`, `question`, `critique`,
`recommendation`, and `proposal`. For a fresh provider outcome, answer,
critique, and recommendation return `continue_with_lead`; question returns
`reply_to_lead`. Those four fresh direct outcomes have no proposal,
confirmation request, or durable step state. Recovery normalization may return
`recommendation` with an existing proposal, state, and state-specific action.

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
successful review, with `next_action=submit_application_decision`. The trailing
optional `story_commit_request` carries an existing local Commit request when
redisclosed, with `next_action=submit_commit_decision`. It defaults to `None`,
preserving existing construction that omits it. These requests still need
their respective structured decisions; neither is consultation authority.

A fresh provider judgment also returns exact durable `evidence`. It contains
the provider-declared `scope` (`story_specific` or `workflow_meta`), declared
status (`not_applicable`, `verified`, `missing`, `empty`, `incomplete`, or
`conflicting`), and ordered content-free receipts. Proposal evidence is
identical to `proposal.basis.evidence`. Runtime-generated recovery and evidence-
rejection notices use `evidence=None`.

The provider's `lead-outcome-v2` result is one strict JSON object with exactly
eight fields: `scope`, `evidence_status`, `evidence_ids`, `kind`, `message`,
`proposal`, `artifact_intent`, and `objective_intent`:

```json
{
  "scope": "story_specific",
  "evidence_status": "verified",
  "evidence_ids": ["reference_1"],
  "kind": "recommendation",
  "message": "The referenced outline supports the existing sequence.",
  "proposal": null,
  "artifact_intent": null,
  "objective_intent": null
}
```

For `kind=proposal`, exactly one of `proposal` or `artifact_intent` is non-null.
Artifact intent contains `operations` and `additional_review_domains`.
Operations are strict provider-neutral `create`, `patch`, `replace`, `rename`,
or `delete` requests. Patch/replace/rename/delete target current runtime-issued
artifact evidence IDs; create suggests a canonical path. The provider cannot
supply candidate identity, reviewer identity, digests, authority flags, or
side-effect claims.

For an existing empty canonical core file, a selected zero-byte core-status
receipt can support `replace` only after runtime recaptures and exactly
revalidates it. Nonempty targets require current attached-reference or READ
evidence. Status identity is not file-content authority; runtime resolves the
whole-source and selected-range digests before constructing a candidate.

Runtime inventories content-free statuses for the four canonical core
documents and issues evidence IDs for explicit references, complete zero-hit
SEARCH, and final-answer-used READ operations. It accepts only the closed
matrix below:

| Scope/status | Required receipts | Allowed kinds |
| --- | --- | --- |
| `workflow_meta` / `not_applicable` | none | answer, question, critique, recommendation |
| `story_specific` / `verified` | nonempty attached or used READ content | all five |
| `story_specific` / `missing` | absent-core status or complete non-truncated zero-hit writing-artifact SEARCH | question, recommendation, proposal |
| `story_specific` / `empty` | zero-byte core status or whole attached reference | question, recommendation, proposal |
| `story_specific` / `incomplete` | content or core-status receipt | question, critique, recommendation, proposal |
| `story_specific` / `conflicting` | two distinct nonempty content receipts | question, critique, recommendation, proposal |

All other combinations fail closed. Runtime validates structural facts; it
does not use NLP to infer scope, status, relevance, or semantic consistency.
Evidence IDs must be current, unique, and in runtime order. A SEARCH hit that
was not READ, a READ omitted from final `used_handles`, a truncated or non-
writing zero-hit search, a prior-invocation ID, or an invented ID is invalid.

Duplicate/unknown/private fields, a nonobject, an unknown kind, a non-null
proposal or artifact intent on a direct outcome, both proposal forms at once,
or malformed intent also fails closed.
Runtime—not the provider—creates authoritative IDs, revision, digest, input
facts, impact, effects, profiles, and budgets.

For artifact intent, runtime also captures the complete canonical artifact
state, resolves evidence-bound operations, builds the exact change set,
validates projected relationships, derives mandatory review domains, adds only
the canonically ordered requested domains, and maps them to specialists. An
invalid non-null artifact intent produces both an
`artifact_intent_rejected` lead failure and `intent_rejected` artifact-review
failure, then returns `retry_lead_turn` with no candidate.

Eligible read-only Lead failures, including provider timeout/failure and
rejected final output or evidence, close through typed `lead_outcome_failure`.
The returned failure recommendation has `next_action=retry_lead_turn` and no
new proposal, confirmation request, or evidence. Submit explicit new text to
`lead_turn()` in the same conversation to start a fresh turn; the original
request and failure remain in history. Exact legacy unstarted, prepared-only,
or unmatched submitted requests are closed before that new turn is appended,
including after restart. Reading history or reopening does not mutate or retry.
An eligible known failure after mandatory references is also retryable once
the exact attempt and reference accounting close. Its authoring objective
remains open, and retry acquires new evidence rather than reusing the old read.

Cancellation/interruption also attempts durable closure; task cancellation is
re-raised after resource release. Failed closure persistence, corrupt history,
uncertain reference cleanup, and uncertain authoritative writes remain
recovery-gated. A failed Revise attempt leaves the old proposal superseded,
with no replacement authority.
Specialist/synthesis uncertainty and successful results cannot be abandoned by
this path. See the [failure contract](collaboration-history.md#state-and-user-action-contract)
and [Lead recovery](../guides/lead-agent.md#submitted-lead-outcome-unavailable-after-restart).

### Contextual authoring objectives

`objective_intent` is required in every front-channel result. `null` makes no
objective transition. The three non-null shapes are:

```json
{"action":"start","request":"Prepare the agreed outline.","target_paths":["core_documents/global_outline.md"],"next_step":"clarify","draft_source_event_ids":[]}
```

```json
{"action":"continue","objective_id":"<offered-open-owner-objective-id>","next_step":"prepare","draft_source_event_ids":["<offered-draft-event-id>"]}
```

```json
{"action":"cancel","objective_id":"<offered-open-owner-objective-id>"}
```

`start` requires a nonempty exact canonical target scope and a visible request;
runtime assigns the objective ID and origin event. If the target is unresolved,
the Lead asks a question with `objective_intent=null` first. `continue` and
`cancel` can name only an `OPEN` objective owned by the current conversation.
They cannot replace its origin request or target paths. Complete same-session
history exposes these facts across conversation switches without transferring
ownership or authority.

For `start` and `continue`, `next_step` is one of:

| Value | Required Lead outcome |
| --- | --- |
| `clarify` | `kind=question`; both proposal fields are null. |
| `consult` | `kind=proposal`; ordinary `proposal` populated, `artifact_intent=null`. |
| `prepare` | `kind=proposal`; `artifact_intent` populated, `proposal=null`; candidate operations cover the frozen target scope exactly. |

After PREPARE is selected, the bounded same-turn correction cannot downgrade
it to advice. A failed preparation does not satisfy the objective. Existing
validation, retry, and recovery rules determine its outcome. Later ordinary
user direction is still interpreted in context. `cancel` selects no work and
cannot accompany a proposal.

Draft IDs select only runtime-offered visible messages from the owner
conversation. `ArtifactDraftSource` identifies the conversation, event, and
SHA-256 of the complete exact UTF-8 message, including its original whitespace
and line endings. There is no extracted-draft or normalized-text identity.
An empty `continue` source list retains the previous selection. Draft sources
supply context, never artifact evidence or confirmation authority.

`CollaborationHistoryProjection.artifact_objectives` retains these objectives;
`offered_draft_sources` supplies the current selectable source identities.
Advice, consultation, review, rejection, staleness, failure, and null intent
leave an objective `OPEN`. Only a matching validated `APPLIED` or `UNCHANGED`
result for its bound candidate makes it `SATISFIED`. Explicit cancellation
makes it `CANCELLED`; Commit is independent of objective completion. See the
[persisted contract](collaboration-history.md#contextual-artifact-objectives).

The public objective values are additive exports of
`story_writing_agents.conversations`; the package-root facade is unchanged.
Retained workspace policies receive a runtime v2 overlay without file rewrites.
Specialist and synthesis schemas remain separate from `lead-outcome-v2`.

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
| `confirm` | Terminal `completed`, `failed`, `interrupted`, `outcome_unknown`, or `recovery_required`; otherwise a fresh round-two `awaiting_confirmation` proposal. |
| `reject` | `recommendation` in `rejected`, with `continue_with_lead`. |
| `revise` | Replacement lead outcome after the old revision becomes `superseded`. |

The revise replacement retains the step ID. A replacement proposal increments
the proposal revision and has fresh proposal/digest/request identities. A
successful round-one synthesis may likewise return revision 2 on the same
step; it includes the original inputs plus round-one reply-event inputs and
requires fresh confirmation. Follow `result.turn.next_action`.

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
advisory = await runtime.propose_specialist_advisory(
    "novel",
    conversation_id,
    "plot_architect",
    "Advise on the immediate pacing question.",
)
```

It returns `awaiting_confirmation` without calling the lead provider. The
proposal has step kind `specialist_advisory`, one assignment, and zero
synthesis reservation. Confirm invokes that specialist and returns its typed
message directly. It cannot chain to round two. Runtime rejects a reference
whose mandatory core-document reviewer differs from `specialist_id`.

### Confirmed execution results

Each successful specialist answer has exact fields `assignment_id`,
`assessment`, `message`, and the ordered assignment `input_ids`. Assessment is
`supports`, `concern`, `conflict`, or `insufficient_evidence`. After every
assignment succeeds, a normal consultation performs separately budgeted lead
synthesis. The synthesis cites every reply event in order and retains all input
IDs from non-supporting replies as unresolved; it may request only one strict
same-lineage round-two intent.

Synthesis separately repeats the proposal's exact story-specific scope,
evidence status, and evidence IDs; reply event IDs are attribution, not story
evidence. One to three assignments run concurrently with isolated reference/context
authority and disjoint call reservations. If one fails, siblings finish and
known replies are preserved, but synthesis is not attempted. Cancellation
cancels and joins siblings, persists knowable results, and re-raises.

For artifact review, runtime derives the canonically ordered reviewers from the
candidate's mandatory and additional domains. Every reviewer receives the
same strict serialized candidate and the same disclosed evidence; no reviewer
can rewrite it. After every reviewer succeeds, the lead synthesis must preserve
all reply attribution and unresolved non-supporting inputs and must add:

```text
decision: propose | revise | reject
rationale: nonempty visible text
user_impact: nonempty visible text
limitations: nonempty visible text
next_intent: null
```

`propose` produces `ArtifactReviewedProposal` and artifact state `reviewed`.
`revise` produces `revision_required`; `reject` produces `rejected`. Partial
review or invalid synthesis produces `failed`. Before successful `propose`
finalization, runtime projects the exact candidate over a fresh complete-state
capture; drift produces `stale`. None of these paths writes an artifact or
grants later execution authority. Specialist assessments are evidence, not a
vote, and synthesis cannot discard a concern because more peers returned
`supports`.

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
execution markers and releases bound-agent resources on every exit path.

### Context event sink

`context_event_sink` is an optional synchronous callback for content-free
`ContextExecutionEvent` values. It must return exactly `None`. Coroutine,
async-generator, asynchronously callable, awaitable-returning, non-`None`, or
exception-raising observers fail under the agent-context lifecycle rules. The
events contain fixed identities, states, and bounded counts—not prompts,
queries, paths, excerpts, provider responses, or credentials.

## Bound session methods

```python
RuntimeSession.apply_directive(text: str) -> dict[str, object]
RuntimeSession.set_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]
RuntimeSession.inspect() -> dict[str, Any]
RuntimeSession.inspect_debate(debate_id: str) -> dict[str, Any]
RuntimeSession.inspect_debate_context(debate_id: str) -> dict[str, Any] | None
RuntimeSession.inspect_reference_operation(
    operation_id: str,
) -> ReferenceOperationHistory | None
RuntimeSession.submit_consensus_prompt(
    conversation_id: str, prompt: str,
) -> ConsensusRuntimeInspection
RuntimeSession.submit_consensus_draft(
    conversation_id: str, draft: str,
) -> ConsensusRuntimeInspection
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
) -> ConsensusRuntimeInspection
await RuntimeSession.lead_turn(
    conversation_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
) -> LeadTurnResult
await RuntimeSession.propose_specialist_advisory(
    conversation_id: str,
    specialist_id: str,
    text: str,
    context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
) -> LeadTurnResult
await RuntimeSession.decide_lead_step(
    decision: LeadStepDecision,
) -> LeadDecisionResult
```

The bound decision method rejects a decision for another session. Bound
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
artifact-application, commit, push, merge, or one-turn bypass method. Consensus
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
configured provider for each addressed invocation. These methods never publish
an artifact or mutate Git.

<a id="context-lifecycle-observer"></a>

## Related APIs

- [Lead-agent guide](../guides/lead-agent.md)
- [Schema-v2 collaboration history](collaboration-history.md)
- [Agent-context design](../design/agent-context.md)
- [Artifact references](../guides/artifact-references.md)
- [Conversation search](conversation-search.md)
- [Conversation archives and transfer](conversation-archives.md)
- [Package API](package-api.md)
- [Agent contracts](agent-contracts.md)
