# Runtime and lead-orchestration API

`StoryRuntime` is the public management, configuration, inspection, and lead
front-channel facade. `RuntimeSession` binds those operations to one session
ID. The runtime can obtain one strict lead outcome, disclose a consultation,
construct an exact complete-state-bound artifact candidate, and execute its
one-to-three specialist assignments after an exact structured confirmation.
Artifact review can produce an immutable reviewed proposal. It does not start
a debate, apply or mutate an artifact, grant Apply authority, or perform Git
work.

The installed shell does not call the lead methods. Ordinary shell input still
receives the direct-execution-unavailable guidance.

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
`lead_turn` then raises `BackendProtocolError`.

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

A fresh provider judgment also returns exact durable `evidence`. It contains
the provider-declared `scope` (`story_specific` or `workflow_meta`), declared
status (`not_applicable`, `verified`, `missing`, `empty`, `incomplete`, or
`conflicting`), and ordered content-free receipts. Proposal evidence is
identical to `proposal.basis.evidence`. Runtime-generated recovery and evidence-
rejection notices use `evidence=None`.

The provider must return one strict JSON object with exactly `scope`,
`evidence_status`, `evidence_ids`, `kind`, `message`, `proposal`, and
`artifact_intent`:

```json
{
  "scope": "story_specific",
  "evidence_status": "verified",
  "evidence_ids": ["reference_1"],
  "kind": "recommendation",
  "message": "The referenced outline supports the existing sequence.",
  "proposal": null,
  "artifact_intent": null
}
```

For `kind=proposal`, exactly one of `proposal` or `artifact_intent` is non-null.
Artifact intent contains `operations` and `additional_review_domains`.
Operations are strict provider-neutral `create`, `patch`, `replace`, `rename`,
or `delete` requests. Patch/replace/rename/delete target current runtime-issued
artifact evidence IDs; create suggests a canonical path. The provider cannot
supply candidate identity, reviewer identity, digests, authority flags, or
side-effect claims.

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

If semantic evidence validation rejects a submitted response, runtime appends
one typed `lead_outcome_failure`, returns a recommendation with
`next_action=retry_lead_turn`, and supplies no state, proposal, request, or
evidence. Retry starts a fresh turn; the rejected physical call is never
replayed. If that closure cannot be persisted, the result is
`recovery_required` instead.

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

Calling `lead_turn(..., "Yes")` is ordinary conversation, never confirmation.
When a proposal is pending, runtime atomically appends the user message and
`superseded` state before any next provider call. Idle application exit makes
no call and leaves the pending request unchanged.

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
artifact-application, consensus, commit, push, merge, or one-turn bypass method
in this stage.

Confirmation of a consultation cannot authorize one of those later effects. A
future artifact or Git action—including a commit—must be a separately
disclosed and separately confirmed step.

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
