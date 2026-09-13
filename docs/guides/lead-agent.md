# Lead agent: story workflows and reviewed artifact proposals

The Lead Orchestrator is the normal user-facing story agent in both the
interactive shell and Python runtime. Users discuss story work with the lead; they do not need to choose an
agent for every turn. The lead answers workflow/status questions and asks
focused clarification questions directly. Substantive story answers, critiques,
drafting, revision, and review require a bounded proposal covering every
affected specialist domain. The Lead may first recommend preparing that scoped
proposal; preparation grants no specialist execution authority. For a requested
story-file change, the lead can also describe a
provider-neutral edit intent. Runtime code—not the lead provider—resolves that
intent against the complete current artifact state, derives the required
reviewers, and returns one exact candidate. An exact user confirmation starts
review of that candidate.

The lead makes the semantic choice: whether a turn is about the story or only
the workflow, what evidence matters, which
specialist domains apply, and what the combined feedback means for the user.
Every story-specific judgment must name current runtime-issued evidence. The
runtime checks that evidence against a closed rule matrix before showing or
persisting the judgment. Runtime code does not interpret prose or decide that
the lead chose the right story facts; it is the authority and execution
boundary that inventories sources, performs authorized SEARCH/READ operations,
validates results, constructs proposals, records confirmation, calls providers,
persists results, and recovers interrupted work.

This distinction also governs story changes. The lead is the semantic trigger
and front channel: it can recommend an edit and request construction and review
of an exact candidate. Runtime code captures current files, constructs and
records the candidate, calls confirmed reviewers, and creates an immutable
reviewed proposal. The interactive workflow then presents a separate exact
Apply/Reject/Revise decision. Only Apply lets runtime write the revalidated
artifact change. A successful Apply may present a separate local
Commit/Reject/Revise decision. Runtime—not the lead provider—executes both
effects; neither authorizes remote push or merge.

In the installed `story` shell, attach a session, focus a conversation, and
type ordinary non-slash text. It is passed literally to the session-bound Lead
API. The async Python methods below expose the same authority model.

Ordinary Lead turns and consultation/artifact-review synthesis share compatible
provider continuation across the session's conversations and pause/reopen or
restart. Each invocation receives required retained session memory and current
directives/core documents, including for workflow/meta questions. Archived
history remains eligible for provider disclosure. This memory grants no
current receipt, confirmation, replay, write, or Git authority; exact capacity
and recovery limits are in [Lead session memory](agent-context.md#lead-session-memory).

The lead also interprets abbreviated approval after a visible draft from this
context. Runtime retains a scoped authoring objective in its originating
conversation. With enough information, current artifact evidence, and all required
current-task specialist drafts, the lead prepares an exact candidate. Missing
detail requires a question; required drafting or review uses an ordinary
consultation. Other conversations can see
the objective through session memory, but only its owner conversation can
continue or cancel it.

An objective remains `OPEN` through side discussion, consultation, review,
rejection, staleness, and failure. Only a matching validated `APPLIED` or
`UNCHANGED` result for its exact bound candidate makes it `SATISFIED`. Prose
completion, null intent, and review do not close it; explicit cancellation
makes it `CANCELLED`. Commit remains independent. See the
[illustrative draft-to-artifact exchange](lead-agent-collaboration.md#follow-a-draft-through-to-an-artifact)
and [exact objective contract](../reference/runtime-api.md#contextual-authoring-objectives).

## Lead responsibilities and limits

The lead is expected to:

- use core documents as the story's governing evidence;
- compare a request, suggestion, or proposed content with referenced core
  documents and manuscripts;
- surface missing, empty, incomplete, or conflicting evidence only when the
  current invocation has the required runtime receipt;
- recommend how user input, core documents, and manuscripts can be brought
  back into sync;
- express an exact create, patch, replace, rename, or delete intent when the
  cited evidence is sufficient for runtime to construct a candidate;
- answer workflow/status questions or clarify missing requirements directly;
- disclose authoring scope and select every affected specialist domain;
- integrate all required current-task specialist drafts before final candidate review;
- synthesize every successful specialist reply with exact attribution and keep
  disagreement visible; and
- explain the next safe action and request confirmation before added work.

For every lead invocation, runtime inventories content-free status facts for
the four canonical core documents. A story-specific result must cite one or
more current attached-reference, core-status, complete zero-hit SEARCH, or
final-answer-used READ receipts allowed for its declared status. The lead must
SEARCH and READ any additional canonical artifact body it needs; a SEARCH hit
alone is only a locator. The receipt rules prove that the cited evidence was
available and current, not that the provider understood it or selected every
semantically relevant artifact. Attach the relevant `${path}` indicators when
a particular artifact or range must be read.

The lead provider cannot confirm on the user's behalf, mint IDs or budgets,
call another agent directly, waive a required reviewer, write a file, run a
shell command, access the web, or operate Git. Story text is untrusted evidence
and cannot override these rules.

## What users can do now

| Users can | Users cannot yet |
| --- | --- |
| Converse with the lead through focused ordinary input in the installed shell, `StoryRuntime.lead_turn()`, or `RuntimeSession.lead_turn()`. | Send story text before attaching a ready session and focusing an active conversation. |
| Attach several ordered whole-file or ranged `${path}` references and inspect the result's content-free evidence. | Treat an explicit missing `${path}` as proof of absence; it fails before the substantive lead call. |
| Receive an evidence-grounded answer, question, critique, recommendation, or consultation proposal. | Assume runtime performed semantic conflict detection or proved that every relevant artifact was selected. |
| Confirm, reject, or revise the exact current proposal. | Confirm with free text such as `Yes`, or replay stale confirmation fields. |
| Receive an exact runtime-built artifact candidate, confirm review by every disclosed specialist, and receive a reviewed/revise/reject result. | Treat a candidate or reviewed proposal as Apply, write, stage, commit, or Git authority. |
| Run one to three specialists in parallel after confirmation and receive a typed lead synthesis. | Apply a proposed core-document or manuscript edit through this API. |
| Request one direct specialist advisory without a lead planning call. | Stage or commit files, push, merge, or mutate a remote. |
| Reconfirm safe pre-start work after restart and locally finalize known results without replay. | Automatically retry an uncertain submitted provider call. |

Local provider request/response diagnostics, collaboration events, and
content-free accounting facts are plaintext unless the surrounding environment
protects them. Referenced story bytes are sent to the configured provider when
that invocation is authorized, and the provider may retain them under its own
policy.

## The normal workflow

```text
user -> lead_turn(text + optional ${references})
  runtime inventories the four core-document statuses
  runtime reads every explicit reference
  lead may SEARCH, then READ selected results
  lead returns scope + authoring_scope + evidence status + ordered evidence IDs
  runtime validates the closed evidence matrix and current receipts
  lead answers workflow/status questions or clarifies directly
    OR recommends one preparation -> submit_recommendation_decision
       -> exact Confirm prepares a proposal or asks a material question
       -> proposal execution/review still requires its own Confirm
    OR requests a draft/review consultation for every affected domain
       -> runtime discloses consultation -> awaiting_confirmation
    OR integrates required current-task drafts into a provider-neutral artifact intent
       -> runtime captures the complete canonical artifact state
       -> runtime constructs and relationship-validates one exact candidate
       -> runtime derives mandatory + explicitly added review domains
       -> runtime discloses candidate review
       -> artifact state awaiting_consultation_confirmation

invalid/stale/invented evidence
  -> lead_outcome_failure -> retry_lead_turn
  rejected provider call is closed and is never replayed

user -> decide_lead_step(exact Confirm)
  runtime records confirmed
  runtime atomically records running + every ordered dispatch
  runtime calls 1-3 specialists concurrently with isolated evidence
  if all succeed: runtime calls the lead for synthesis
  runtime atomically records replies/outcomes/result
  runtime locally finalizes ordinary consultation
    -> completed / failed / interrupted
    OR discloses a synthesis recommendation -> submit_recommendation_decision
    OR proposes same-step revision+1 -> awaiting_confirmation

after draft synthesis, user -> lead_turn("Prepare the final candidate from these drafts")
  lead integrates required drafts into an artifact intent
  runtime discloses the exact candidate and a separate review confirmation

  for an artifact candidate, every reviewer receives the same immutable
  candidate and the lead synthesizes all replies without voting
    propose -> artifact reviewed (still no write/Apply/Git authority)
    revise  -> artifact revision_required
    reject  -> artifact rejected
    incomplete review or invalid synthesis -> artifact failed
```

`confirmed` is normally a transient durable boundary inside
`decide_lead_step()`: the method continues into execution and returns the final
authoritative `turn`. If the durable start append fails, no specialist call is
made. If the process restarts after confirmation but before start authority can
be used, the proposal becomes `reconfirmation_required` instead of being
silently executed.

## Confirm recommendation preparation

A fresh recommendation carries `recommendation_request` and
`next_action=submit_recommendation_decision`. It discloses purpose, instruction,
artifact targets or specialists, selected sources, expected result, user impact,
and physical-call budget. Confirm authorizes one bounded Lead preparation,
which returns an exact artifact-review or consultation proposal, or a material
clarifying question. It does not reconfirm that preparation or automatically
execute the next step. Confirm review or consultation separately; Apply and
Commit follow their own controls.

The request binds the current substantive task and revision. Confirm preserves
its exact scope; Revise offers the next revision of the same task while keeping
all existing domains, targets, and drafting obligations. Fresh complete
supplied candidates may proceed directly to the required specialists for
review. Existing drafting or revision work still needs its specialist draft
provenance. Old unscoped requests need fresh planning before preparation.

Reject starts no preparation and leaves any authoring objective open. Revise
requires nonempty instructions and permits only a revised recommendation with
a new request. Cancel/EOF submits nothing. Ordinary contextual approval remains
a real new instruction, superseding an unconfirmed recommendation without
creating a structured Confirm. With sufficient scope and current evidence,
the Lead can prepare its exact candidate from that ordinary input.

Use `decide_lead_recommendation()` on either runtime facade; it returns the
existing `LeadTurnResult`. The complete
[decision example](../reference/runtime-api.md#leadrecommendationdecision)
uses every returned request identity. Historical recommendations and runtime
recovery notices are not turned into action requests. An objective is satisfied
only by a matching successful Apply, regardless of preparation completion.

## Call the lead and confirm a proposal

Assume `runtime` has a configured ready backend and `conversation_id` is an
active conversation in session `novel`:

```python
result = await runtime.lead_turn(
    "novel",
    conversation_id,
    (
        "Check whether this opening conflicts with "
        "${core_documents/global_outline.md} and "
        "${manuscript/part-01/opening.md}#L20-L32."
    ),
)
```

The bound form supplies the session ID:

```python
session = runtime.session("novel")
result = await session.lead_turn(conversation_id, "Is this concern already resolved?")
```

The lead returns one `LeadTurnResult`:

| `kind` | User meaning | `next_action` |
| --- | --- | --- |
| `answer` | A workflow/status answer, without substantive story authoring. | `continue_with_lead` |
| `question` | One material user choice is missing. | `reply_to_lead` |
| `critique` | A non-authoring workflow critique; substantive story critique needs specialists. | `continue_with_lead` |
| `recommendation` | A fresh preparation recommendation, or a runtime workflow/recovery notice. | `submit_recommendation_decision` for a fresh action; otherwise state-specific. |
| `proposal` | Added specialist work is disclosed but has not started. | `submit_confirmation` |

A direct answer has `proposal=None`, `state=None`, and no confirmation request:

```text
User: What happens if I reject a consultation proposal?
LeadTurnResult:
  kind: answer
  message: Reject records the decision and starts no specialist work.
  state: none
  next_action: continue_with_lead
  evidence: workflow_meta / not_applicable / no receipts
  reply_construction:
    role: lead_orchestrator
    action: answer
    context: current_input, session_state
    recovered_fields: none
    origin: provider
Provider work: lead only; required session memory; no added reference/SEARCH/READ or specialist
```

The terminal renders these content-free construction facts beside fresh and
historical Lead/specialist replies. `origin=recovered` means the first
structured result was rejected and the single repair succeeded.
`origin=runtime_fallback` always has `action=none`; it keeps the conversation
open but supplies no collaboration or mutation authority. The trace lists
context classes, not the prompt, retrieved text, hidden reasoning, or reply
content.

The same front channel is available as ordinary input in the interactive
shell. Exact prose is provider-generated, while the typed outcome and next
action are runtime-owned:

```text
story[novel]> Does the opening conflict with ${core_documents/global_outline.md}?
[Lead]
  Outcome: proposal
  Lead: I propose a Plot review of the opening order against this outline.
  Next action: submit_confirmation
```

For a story-specific provider judgment, `result.evidence` identifies its
declared `scope`, `status`, and ordered content-free receipts. A workflow/meta
judgment also has an evidence value, but it is exactly
`workflow_meta`/`not_applicable` with no receipts. Proposal-free runtime-generated
recovery notices instead have `evidence=None`.

An artifact-intent outcome is still `kind=proposal` and carries the ordinary
consultation proposal, state, confirmation request, and evidence. It also
carries `artifact_proposal`. Before confirmation, that value contains state
`awaiting_consultation_confirmation` and the exact runtime-built candidate;
after a successful `propose` synthesis it contains state `reviewed` and an
immutable `reviewed_proposal`. Inspect the candidate change set, relationship
facts, reviewer IDs, and digest before constructing the exact decision. The
reviewed proposal always records `artifact_write_occurred=false`,
`apply_authority_granted=false`, and `git_action_occurred=false`.

For a proposal, copy the exact returned authority into a structured decision:

```python
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    LeadStepDecision,
)

proposal = result.proposal
assert proposal is not None
assert result.confirmation_request_id is not None

decision = LeadStepDecision(
    confirmation_request_id=result.confirmation_request_id,
    session_id=result.session_id,
    conversation_id=result.conversation_id,
    step_id=result.step_id,
    proposal_id=proposal.proposal_id,
    proposal_revision=proposal.proposal_revision,
    proposal_digest=proposal.proposal_digest,
    kind=CollaborationConfirmationDecision.CONFIRM,
)
accepted = await runtime.decide_lead_step(decision)
print(accepted.turn.state, accepted.turn.message)
```

`accepted` is a `LeadDecisionResult` containing the decided proposal and the
authoritative resulting `turn`. Confirm returns the completed, failed,
interrupted, uncertain, recovery, synthesis-recommendation, or round-two-proposal
turn—not a separate
stale `state` field on `LeadDecisionResult`.

Reject performs no consultation and returns a rejected recommendation turn.
Revise supersedes the old revision and returns the replacement lead turn in
`result.turn`:

```text
User action: Reject the exact current proposal
Runtime provider calls: none
LeadDecisionResult.turn:
  kind: recommendation
  state: rejected
  next_action: continue_with_lead
Effect: no specialist, artifact, or Git operation
```

```python
revision = LeadStepDecision(
    confirmation_request_id=result.confirmation_request_id,
    session_id=result.session_id,
    conversation_id=result.conversation_id,
    step_id=result.step_id,
    proposal_id=proposal.proposal_id,
    proposal_revision=proposal.proposal_revision,
    proposal_digest=proposal.proposal_digest,
    kind=CollaborationConfirmationDecision.REVISE,
    revision_request="Focus each review on causal consequences while retaining all affected domains.",
)
replacement = (await runtime.decide_lead_step(revision)).turn
```

Free text is never structured confirmation. Calling `lead_turn(..., "Yes")`
while a consultation proposal is pending atomically supersedes that proposal
before asking the lead for a contextual outcome. A pending Apply or Commit
request is instead redisclosed unchanged without a provider call.

## How specialist routing works

| Specialist | Domain |
| --- | --- |
| Plot & Structure Architect (`plot_architect`) | Global/part outlines, plot structure, causality, flow, and pacing |
| Character & World Custodian (`character_custodian`) | Character sheets, relationships, world facts, motivation, and continuity |
| Tone & Execution Specialist (`execution_specialist`) | Narrative rules, manuscripts, prose, POV, tone, and writing execution |

Runtime derives mandatory specialists from disclosed semantic domains and
every affected canonical path, including empty or missing targets:

| Core document | Required specialist |
| --- | --- |
| `core_documents/global_outline.md` | `plot_architect` |
| `core_documents/part_outlines.md` | `plot_architect` |
| `core_documents/character_sheets.md` | `character_custodian` |
| `core_documents/narrative_rules.md` | `execution_specialist` |
| Eligible `manuscript/**/*.md` reference paths | `execution_specialist` |

The lead discloses its interpretation of free text in `AuthoringScope` schema 1:
`activity` (`workflow`, `draft`, `revise`, or `review`), `target_paths`, canonical
`domains`, `interpretation`, and the exact runtime-offered `task_id`/`task_revision`
pair. Runtime unions those facts with relevant references, current evidence,
and the explicitly selected active task. Unrelated inventory/history does not
add domains; status never grants reads. Runtime does not infer story meaning
from prose or promise perfect semantic routing. It rejects proposals with unknown or duplicate specialists,
noncanonical order, missing mandatory reviewers, unassigned inputs, or an
infeasible budget. It never adds undisclosed work.

Each specialist receives only its assignment's ordered inputs and fresh
mandatory-reference reads. A specialist cannot inventory, SEARCH, or READ a
sibling's artifact-only evidence. The lead synthesis receives all typed replies
and the directly referenced story-artifact inputs; one agent's previous read
receipt is never borrowed as another agent's evidence.

Each assignment explicitly uses `draft` or `review`. Drafting can produce
`drafted` material from an adequate brief even when its target is empty. Review
requires `ReviewBasis` schema 1 with nonempty `candidate_input_ids` and visible
`criteria`; a brief or status receipt alone is not a review candidate. When a
user supplies a complete candidate, the Lead must disclose that interpretation.

Active same-session tasks come from validated conversation catalog/events. The
Lead explicitly selects a runtime-offered task/revision to continue it across
conversations; its domains, targets, drafting obligations, and current revision
cannot silently shrink. An unrelated request uses a fresh task. Archived
history remains readable context, without routing authority; an applied task
revision is no longer offered. No separate task database is introduced.

A `completed` consultation records that round, not completion of the authoring
objective. Draft synthesis returns material; ask the Lead in a subsequent
`lead_turn()` to prepare the final candidate from those drafts. The synthesis
contract does not itself emit an artifact intent. The Lead integrates all required current-task drafts, every required
specialist reviews the exact immutable final artifact candidate, and Apply
requires another structured confirmation. A draft or earlier supporting
review cannot replace final candidate review.

## How a lead judgment is grounded

The provider declares one semantic scope and one evidence status. Runtime does
not use NLP to relabel either value. Ambiguous story/workflow requests are a
prompt rule for the lead: classify them as story-specific and ask a grounded
question. The evidence layer accepts only these combinations; authoring routing applies
in addition, so evidence alone never permits substantive direct story work:

| Declared scope and status | Minimum current evidence | Allowed visible kinds |
| --- | --- | --- |
| `workflow_meta` / `not_applicable` | No receipts | `answer`, `question`, `critique`, `recommendation` |
| `story_specific` / `verified` | One nonempty attached reference or completed READ | All five kinds |
| `story_specific` / `missing` | An absent canonical-core status, or a complete non-truncated zero-hit SEARCH whose authorized scope includes writing artifacts | `question`, `recommendation`, `proposal` |
| `story_specific` / `empty` | A zero-byte canonical-core status or a zero-byte whole attached reference | `question`, `recommendation`, `proposal` |
| `story_specific` / `incomplete` | At least one current content or canonical-core status receipt | `question`, `critique`, `recommendation`, `proposal` |
| `story_specific` / `conflicting` | At least two distinct, nonempty current content receipts | `question`, `critique`, `recommendation`, `proposal` |

Every other combination fails closed. Evidence IDs are invocation-local and
must be unique and in runtime order: attached references, four core statuses,
then completed SEARCH/READ actions. Every successful current-invocation READ
may support content and become a proposal input. Core-status and
zero-hit SEARCH receipts are evidence only, never assignment inputs.

An exact explicit reference has stronger failure semantics than an exploratory
search. `${manuscript/part-01/missing.md}` raises
`ReferenceOperationFailure`; the provider cannot convert it into `missing`.
A whole reference to an existing zero-byte file may prove `empty`, while a
range into that file fails as out of bounds.

## Explicit story-writing scenarios

These scenarios define the implemented behavior. Each separates the visible
conversation from provider intent and runtime enforcement. “Candidate” means
exact proposed bytes and paths. “Reviewed proposal” means every disclosed
specialist assessed that same candidate and the lead selected `propose`; it
never means the files changed.

### 0. Greeting and orientation

```text
User input
Hello. Can you help me continue this story?

Lead output
Hello. Tell me what you want to develop or reference the relevant files.
Outcome: answer
Next action: continue_with_lead
```

This is a `workflow_meta` ANSWER with `not_applicable`, no evidence IDs, no
specialist, no artifact intent, and no confirmation. Runtime may inventory
content-free core status facts, but those facts grant no story-body receipt. Required Lead session memory
still includes current core bytes and retained history.

### 1. Incomplete or conflicting core evidence

```text
User input
The narrative rules file is missing. Propose a minimal close-third rule.

Lead output
The target is absent. I propose Execution drafting from your close-third brief.
Authoring activity: draft; required domain: tone_execution.
User action: Confirm the exact draft assignment.
Execution / drafted: returns proposed rule text; no file is written.
Lead synthesis: presents the draft.
User: Prepare the final candidate from this draft.
Lead integrates the draft into one exact create candidate, then proposes review.
Review requires execution_specialist against that immutable final candidate.
Artifact state: awaiting_consultation_confirmation
User impact now: no file has changed
User choices: Confirm review, Reject, or Revise

User action
Confirm the exact request/proposal revision/digest.

Specialist output
execution_specialist / supports: the proposed rule is internally usable and
does not contradict the supplied evidence.

Lead review synthesis
decision: propose
message: The exact candidate review is complete.
user_impact: No artifact changed; the user may review the proposal.
limitations: No additional limitations were reported.

Final artifact state: reviewed
Final collaboration state: completed
```

Internally, the lead cites the absent canonical-core status and discloses the
drafting assignment. After the required current-task draft is recorded and
integrated, it returns a strict `create` intent. Runtime captures every canonical artifact, constructs
and relationship-validates the candidate against that complete state, derives
the tone/execution review domain, and discloses its reviewer. Confirm authorizes
only that review. Runtime revalidates the candidate before calling the
specialist and again before finalizing the reviewed proposal. The missing file
still does not exist afterward.

The other core-gap cases differ deliberately:

```text
Canonical core file absent -> core status receipt -> missing
Canonical core file exists at zero bytes -> core status/whole reference -> empty
Relevant canonical search has no hit -> complete zero-hit SEARCH -> missing
Explicit ${missing/path.md} -> ReferenceOperationFailure, not a lead judgment
```

The lead does not force every gap into the same operation:

| Current evidence | Valid lead behavior | Candidate example |
| --- | --- | --- |
| Canonical core absent | Clarify the brief, confirm specialist drafting, then integrate a `create` candidate for review. | Create the missing core path. |
| Existing core is zero bytes | Cite its empty status, clarify or confirm drafting, then integrate a whole-file `replace` candidate. | Replace the exact empty file. |
| One core source is incomplete | Disclose status, then propose its specialist for substantive critique/revision before candidate review. | Fill the exact missing section. |
| Two nonempty core sources conflict | Cite both sources, preserve both interpretations, and consult every affected domain before choosing a candidate. | Revise one source, or atomically revise both when consistency requires it. |

For example, if `${core_documents/part_outlines.md}` schedules a reveal before
the trust condition in `${core_documents/character_sheets.md}`, the lead may
first disclose plot and character consultation. After Confirm, it must
synthesize both assessments without a majority shortcut. It can reject the
user's requested timing, ask which rule should govern, or return a separately
confirmable exact artifact candidate on the next lead turn. Consultation
confirmation is not reused as candidate-review confirmation.

### 2. Writing or revising core documents

```text
User input
Replace the current POV rule with “Mara-led scenes use close third person.”
Check ${core_documents/character_sheets.md} and
${core_documents/narrative_rules.md} first.

Lead output
I interpret the quoted rule as your complete supplied replacement candidate,
not a request for me to author missing material. I propose review of that exact
replacement, grounded by both references.
Runtime-derived required reviewers: character_custodian and
execution_specialist.
Artifact state: awaiting_consultation_confirmation.

User action: Confirm exact candidate review.

Specialist outputs against the same candidate
- character_custodian / concern: the sheet includes scenes without Mara;
  preserve an explicit rule for their viewpoint.
- execution_specialist / supports: the replacement is precise for Mara-led
  scenes.

Lead synthesis
decision: revise
rationale: the candidate leaves non-Mara scenes undefined.
user_impact: no file changed; request a replacement candidate that closes the
gap.
Artifact state: revision_required.
```

For authoring requests, specialist drafts must precede integration and review.
The supplied complete-candidate example above discloses its separate review
interpretation; a brief alone cannot take that path. The lead supplies semantic
intent and may request any additional affected
review domain. Runtime owns exact change-set construction, relationship validation,
mandatory reviewer derivation, reviewer order, assignment/profile/budget
facts, and digests. Every reviewer sees the identical serialized candidate;
their views are evidence, not votes. One `supports` result cannot outvote one
material concern. `revision_required` carries no reviewed proposal and grants
no authority. The user can ask for a new candidate; the old candidate becomes
`superseded` and cannot be confirmed again.

An existing empty core document uses a replacement, not a create. A selected
current zero-byte core-status receipt can ground that replacement only after
runtime recaptures and revalidates it. Empty status proves the absence of
content; it does not prove that the story direction is complete. Nonempty
targets require attached-reference or READ evidence. Whole-message draft
event IDs and digests preserve the drafting context but do not replace these
artifact receipts or authorize review, Apply, or Commit.

### 3. Reviewing a manuscript edit against core documents

```text
User input
Rewrite ${manuscript/part-01/opening.md}#L20-L32 so Mara reveals the secret.
Verify it against ${core_documents/global_outline.md},
${core_documents/character_sheets.md}, and
${core_documents/narrative_rules.md}.

Lead output
I propose Plot, Character, and Execution draft/revision assignments for the
causal beat, motivation, and prose. All three domains are affected.
User action: Confirm the exact drafting proposal.
Specialists: return drafted material without writing files.
Lead synthesis: presents the draft material.
User: Prepare the final candidate from these drafts.
Lead: integrates every required current-task draft into one exact range patch.
All three specialists must now review that same immutable final candidate.
User action: Confirm the separate exact candidate review.

Specialist replies
- plot_architect / conflict: the reveal is scheduled for a later turning point
- character_custodian / concern: current motivation does not justify disclosure
- execution_specialist / supports: the requested prose form can follow the POV rule

Lead synthesis
The prose technique is compatible, but the event timing and motivation are
not. decision: reject. The unresolved input IDs for every conflict/concern
remain visible.
Final artifact state: rejected; final collaboration state: completed.
```

Internally, runtime revalidates the referenced outline, character sheet,
narrative rules, and manuscript range at confirmation, then gives each
specialist the same exact candidate and disclosed evidence. Externally, the
user receives one lead synthesis that preserves the plot and character
objections rather than averaging them away. No manuscript bytes are changed.
A later artifact Apply step must disclose the exact effect and receive separate
confirmation; a later Git Commit step must receive another separate
confirmation. The lead triggers those semantic steps, while runtime code
performs the write and Git operations.

### 4. Ordinary discussion

The lead uses the smallest useful path:

```text
User: What does Reject do?
Lead: Direct workflow/meta answer. No story evidence, proposal, or specialist.

User: Is this beat structurally too early?
Lead: Uses current story evidence and proposes plot_architect review of the
      concrete beat. After confirmation, it integrates the specialist reply.

User: Would this reveal hurt both pacing and character credibility?
Lead: Proposes plot_architect + character_custodian in parallel; after all
      successful replies, synthesizes both with exact attribution.

User: What color should the editor theme use?
Lead: If no story specialist domain applies, answers/clarifies directly and
      does not manufacture a consultation.
```

For the first and fourth examples, the lead may return
`workflow_meta`/`not_applicable` with no evidence receipts and no proposal.
Runtime still inventories content-free source facts as part of planning, but
it sends no story reference, SEARCH, or READ merely because the provider
declares the turn meta. An explicit reference or provider action would be
separate. Runtime does not make that semantic decision for the lead.
Clarification comes before a proposal when a missing user choice would
materially change the work.

Ordinary discussion does not construct an artifact candidate. A story-specific
opinion still needs current evidence; a workflow/meta answer does not. The lead
routes every affected story domain. Workflow/status and clarification can
remain direct. In all cases the lead owns the final user-facing
answer; specialists never become a competing front channel.

## Additional execution scenarios

### Disagreement is preserved

A review-mode specialist reply has exactly:

```json
{
  "mode": "review",
  "assessment": "concern",
  "assignment_id": "<assignment-id>",
  "input_ids": ["user_input", "reference_1"],
  "message": "The reveal conflicts with the current pacing constraint."
}
```

In review mode, `assessment` is `supports`, `concern`, `conflict`, or
`insufficient_evidence`. Draft mode instead permits `drafted` or
`insufficient_evidence`. Synthesis cites every reply event in order and retains
every input ID from concern/conflict/insufficient-evidence replies in
`unresolved_input_ids`; drafted material is exempt from that unresolved-input
requirement. The lead cannot erase disagreement by voting.

### Partial failure

If one of several specialists fails, siblings are allowed to finish and their
typed replies are preserved. The runtime does not call the lead for semantic
synthesis, because the evidence set is incomplete. The user receives a
`failed` recommendation explaining that known sibling results were retained;
`next_action` is `continue_with_lead`.

For an artifact candidate, the artifact state is also `failed`, an
`artifact_review.failure=review_incomplete` fact is durable, and no reviewed
proposal exists. A successful sibling reply is retained as evidence; it is not
enough to approve the candidate.

### Stale evidence and a rejected provider result

Confirmation never means “use whatever is there now.” If a proposal relied on
a manuscript READ or a zero-hit writing-artifact SEARCH and the relevant source
snapshot changes before Confirm, runtime rejects the decision before any
specialist call:

```text
User action: Confirm the displayed consultation
Runtime check: proposal evidence no longer matches current sources
Specialist calls: 0
Visible outcome: decision raises a protocol/authority error
Next user action: ask the lead again so it can obtain current evidence
```

If the provider itself returns an invented, prior-turn, duplicate, reordered,
unused, or otherwise invalid evidence ID, runtime closes that submitted call
instead of persisting its story claim:

```text
LeadTurnResult:
  kind: recommendation
  message: The lead result failed runtime evidence validation. Retry the lead
           turn; the rejected provider call will not be replayed.
  state: none
  evidence: none
  next_action: retry_lead_turn
Durable fact: one lead_outcome_failure for the exact invocation and call
```

The retry is a new turn and a new provider call. If the failure event itself
cannot be appended, runtime returns `recovery_required` and normal
collaboration must stop for history repair.

Do not treat every provider-related failure as the same recovery case:

| Observable fact | What it means | Safe next action |
| --- | --- | --- |
| An exchange closes with `provider_failure` / `provider_authentication_required`, but no valid `ProviderResult`. | The completed native result requires authentication. Authentication and timeout are independent facts; interactive Lead work still offers provider-native sign-in. | Complete native sign-in. Story may replay only the exact Antigravity pre-execution rejection when no timeout fact exists; a timeout defeats replay and continuation reuse and closes an eligible read-only Lead attempt as `provider_timed_out` with `retry_lead_turn`. |
| An exchange has a request and `provider_failure` / `provider_invocation_failed`, but no valid `ProviderResult`. | The adapter did not return Story's provider-result contract. Configuration, output-schema rejection, transport, or another provider-side failure may be responsible. The fixed code does not prove whether the provider performed external work. | Diagnose the provider/configuration boundary. Do not infer that replay is safe from this diagnostic alone. |
| A completed result is rejected. | Story received malformed or semantically invalid structured output. | Story requests one bounded repair. If repair fails, it returns a visible safe no-action Lead answer and keeps the conversation open. |
| Collaboration state is `outcome_unknown`. | Submission is durable, but no authoritative accepted or rejected result closes it. | Follow `inspect_and_resolve_outcome`; never replay the submitted call. |

For example, the former Codex failure was caused by a response schema whose
array `items` node had no explicit supported type. Codex rejected that schema
before returning a valid `ProviderResult`, so the exchange could show only
`provider_invocation_failed`. Typed wire v7 uses a closed
`{action}` root and embeds every action/result object directly. Runtime derives
correlation from the active call.
Conversely, an Antigravity result that adds undeclared bookkeeping fields or
omits a semantic field required by its selected variant is a returned-result
rejection, not `outcome_unknown`. These examples contain no provider-native diagnostic or
story content; durable user history also keeps only the fixed classifications.

The replay exception is deliberately narrow. Antigravity must exit 1 with its
exact zero-turn, zero-duration, empty conversation/response authentication-
timeout envelope and five zero usage counters. After successful interactive
sign-in and fresh status, Story may make one replacement physical call with a
new call ID. Payload, schema, correlation, expected thread, execution profile,
and captured evidence remain identical, and any route, configuration, session,
thread, workspace, or source drift vetoes submission. Both attempts consume the
physical-call limit and have distinct exchanges.

The fixed provider message contains the words `timed out`, but those words do
not set Story's independent timeout fact. When that fact is present,
authentication assistance and its exchange classification remain available,
while timeout forbids replay and continuation reuse and controls durable Lead
closure as `provider_timed_out` with `retry_lead_turn`.

During a confirmed multi-specialist consultation, Story waits for active
siblings before giving native sign-in the terminal. Completed sibling replies
remain durable and are not dispatched again. If authentication or replay fails,
the runtime persists every known reply and the applicable `failed` or
`outcome_unknown` result before returning the typed authentication error. A
restart performs no replay. The next user action follows that durable state;
any later Lead request is a fresh turn rather than a continuation of the failed
physical call. Noninteractive callers receive
`ProviderAuthenticationRequiredError` without a native login handoff.
Authentication recovery preserves the exact task/revision and assignment mode.
It adds no drafting coverage, candidate-review approval, Apply, or Commit
authority, and it does not satisfy an open authoring objective.

Artifact candidates add two separate staleness checks. Before review starts,
Confirm revalidates the candidate's evidence and complete base state; drift
returns artifact state `stale`, makes zero reviewer calls, and changes no file.
After successful review, runtime projects the change set over a fresh complete
state before creating `ArtifactReviewedProposal`; drift there also returns
`stale` rather than publishing obsolete review authority. Ask the lead for a
new candidate in either case.

### A focused second round

After all round-one assignments and synthesis succeed, the synthesis may
request one same-lineage continuation. When it fits the remaining call budget,
runtime creates proposal revision 2 on the same step, adds the round-one
reply-event inputs, carries actual submitted call spend, and returns
`awaiting_confirmation`. Nothing in round two starts until the user submits a
fresh structured Confirm for the new request. Round-two synthesis cannot
request a third round.

```text
Round 1: concern remains -> completed result + revision 2 proposal
User: Confirm revision 2
Round 2: focused specialist reassessment -> final completed result
Maximum: 2 specialist rounds and 12 submitted physical calls
```

If the requested second round cannot fit the remaining lineage budget, the
successful consultation ends as `completed` with next action
`continue_with_lead`. Finalization makes no extra provider call and creates no
new proposal or confirmation authority. Durable history retains the full
specialist replies, synthesis, evidence, and requested continuation.

The visible result appends a budget notice to the synthesis. If the combined
message would exceed the existing UTF-8 byte limit, it shows only the bounded
notice, which points to the completed replies and full synthesis in history.
Reopening preserves this terminal result without duplicate finalization or
replaying the consultation. A new user request starts a normal fresh Lead
turn. The two-round and 12-call limits are unchanged; an affordable second
round still requires its own structured Confirm.

### Direct specialist advisory

An application can disclose a one-specialist proposal without first calling
the lead provider:

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
A new API task ID is rebound runtime-side; inspect `advisory.proposal.authoring_scope`
for the actual identity. To continue an offered active task, pass its exact
current identity/revision; stale or applied revisions are rejected.

For drafting, use `activity=AuthoringActivity.DRAFT`, an adequate authoring brief,
and omit `review_basis`. Review requires concrete nonempty material; the direct
supplied-text alias is `user_input`. A status receipt or a request to write a
candidate is insufficient review material. Confirm the exact returned proposal
using the decision flow above; this call alone starts no specialist.

The session-bound form omits `session_id`. Proposal construction is
provider-free; exact confirmation runs one specialist, reserves zero synthesis
calls, returns the specialist's message directly, and cannot chain to another
round. Runtime rejects any semantic or known-path domain that the selected specialist
cannot cover.

## What providers receive

Applications do not call separate provider search/read/answer endpoints.
Runtime binds the selected agent and drives one bounded contextual invocation.
The samples below are synthetic illustrations of the stabilized contracts;
generated correlations, IDs, handles, digests, and compact planning counters
vary.
Initial-payload samples below omit the separate required `h` session-context
object for readability; they are excerpts, not complete on-wire payloads.
SEARCH/READ continuation frames carry only their new observation.

Codex and Antigravity receive byte-identical provider-neutral wire-v7 schema
and payload semantics; only their native transport differs. The provider root
requires only `action`; runtime derives correlation and action-index facts from
the active invocation. This sanitized structural sample shows all three action
variants:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "action": {
      "anyOf": [
        {"type":"object","additionalProperties":false,"properties":{"kind":{"type":"string","enum":["search"]},"queries":{"type":"array","minItems":1,"items":{"type":"object","additionalProperties":false,"properties":{"text":{"type":"string"},"source_kinds":{"type":"array","minItems":1,"items":{"type":"string","enum":["writing_artifact","conversation","active_debate","own_agent_state"]}}},"required":["text","source_kinds"]}},"path_prefix":{"type":"string"}},"required":["kind","queries"]},
        {"type":"object","additionalProperties":false,"properties":{"kind":{"type":"string","enum":["read"]},"handles":{"type":"array","minItems":1,"items":{"type":"string"}}},"required":["kind","handles"]},
        {"type":"object","additionalProperties":false,"properties":{"kind":{"type":"string","enum":["answer"]},"result":{"type":"object","additionalProperties":false,"properties":{"message":{"type":"string"},"authoring_scope":{"type":"object","additionalProperties":false,"properties":{"activity":{"type":"string","enum":["workflow","draft","revise","review"]},"target_paths":{"type":"array","items":{"type":"string"}},"domains":{"type":"array","items":{"type":"string","enum":["plot_structure","character_world","tone_execution"]}},"task":{"type":"string"}},"required":["activity","target_paths","domains","task"]}},"required":["message","authoring_scope"]}},"required":["kind","result"]}
      ]
    }
  },
  "required": ["action"]
}
```

The shown ANSWER branch is the minimal `lead-outcome-v5` workflow-meta direct-
answer variant. Every provider-produced direct Lead result requires `message`
and compact `authoring_scope`; questions and critiques add `kind`, while the
default answer omits it. Live schemas add only the semantic fields needed by
recommendation, story-specific, and proposal variants. SEARCH finds locators,
READ accepts only issued SEARCH handles, and ANSWER embeds its semantic result
directly. Runtime derives evidence receipts, used handles, correlation, and
other bookkeeping. It never infers a missing scope as workflow: omission gets
one bounded repair, then a runtime-owned visible no-action fallback if repair
fails. That fallback is not a provider-schema variant. There is no JSON-in-
string layer or legacy decoder.

A sanitized provider-call summary (with the schema block referenced rather
than duplicated) has the same semantics for either provider:

```json
{
  "call_kind": "collaboration_initial",
  "prior_thread_id": null,
  "schema": "<exact per-call schema shown above>",
  "payload": {
    "c": "<correlation-1>",
    "q": ["lead", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["lead", "lead-outcome-v5"]],
    "i": {"workflow": "lead_front_channel", "command": "Does the planned reveal conflict with the current opening?"}
  }
}
```

Codex receives that schema by `--output-schema <schema-file>`. Antigravity
receives the same canonical schema by `--json-schema <canonical-json>` and must
return a root-object `structured_output` plus an exact `json_schema` echo; its
custom-agent, isolated-project, and JSON-result boundary is documented in
the [Antigravity guide](antigravity.md#sanitized-provider-call-and-json-result).
Applications do not call separate provider search/read endpoints.

For example, either provider can return this provider-neutral SEARCH output:

```json
{"action":{"kind":"search","queries":[{"text":"planned reveal opening","source_kinds":["writing_artifact"]}]}}
```

The samples keep `source_kinds` in canonical order for readability, but a
provider's array order is not semantic. Runtime requires a nonempty,
duplicate-free subset of the kinds allowed for that request and canonicalizes
it before authorizing SEARCH. Empty, duplicate, unknown, or request-disallowed
kinds are rejected. A non-null `path_prefix` is allowed only with
`source_kinds=["writing_artifact"]`. The provider schema types and enums each
member; runtime—not JSON Schema—owns uniqueness and ordering validation.

### Lead turn: initial input, SEARCH, READ, and ANSWER

For this user input:

```text
Does the planned reveal conflict with the current opening?
```

the initial compact call includes required workflow material and four
content-free canonical-core statuses. The status inventory itself grants no
story-body receipt. Separate required `h` context includes retained history and
complete current core bytes even for a meta turn without references or
SEARCH/READ; those bytes do not satisfy the current evidence contract.

```json
{
  "c": "<correlation-1>",
  "q": ["lead", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["lead", "lead-outcome-v5"]],
  "i": {
    "workflow": "lead_front_channel",
    "contract_version": "lead-outcome-v5",
    "command": "Does the planned reveal conflict with the current opening?",
    "identity": {"agent_id": "lead_orchestrator", "session_id": "novel", "conversation_id": "<conversation-id>", "turn_id": "<turn-id>", "step_id": "<step-id>"},
    "authoring_tasks": [{"task_id":"task_demo","task_revision":1,"current_scope":null}],
    "input_aliases": [{"input_id": "user_input", "kind": "user_input", "locator": "conversation/<turn-id>/user_input", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null}],
    "answer_contract": {
      "assignments": {"agent_id": "canonical specialist ID", "input_ids": "nonempty runtime-supplied aliases", "instruction": "complete visible assignment", "mode":"draft | review", "review_basis":"null for draft; schema 1 candidate_input_ids and criteria for review"},
      "direct_answer": "message and authoring_scope; add kind only for question or critique",
      "authoring_scope": "always required: activity, target_paths, domains, and exact offered task token",
      "kind": "question | critique | recommendation | proposal when selected; omit for the default answer",
      "message": "nonempty user-visible text",
      "proposal": {"assignments": "required only for an ordinary consultation proposal"},
      "artifact_intent": "create/patch/replace/rename/delete operations for an artifact proposal",
      "objective_intent": "strict start/continue/cancel intent when transitioning an objective",
      "recommended_action": "strict prepare_artifact/prepare_consultation action for a recommendation"
    }
  },
  "core_statuses": [
    {"evidence_id": "status_1", "locator": "core_documents/global_outline.md", "status": "present", "utf8_bytes": 428, "source_id": "<source-id>", "manifest_digest": "sha256:<digest>", "revision": "sha256:<digest>", "source_digest": "sha256:<digest>"},
    {"evidence_id": "status_2", "locator": "core_documents/part_outlines.md", "status": "empty", "utf8_bytes": 0, "source_id": "<source-id>", "manifest_digest": "sha256:<digest>", "revision": "sha256:<digest>", "source_digest": "sha256:<empty-digest>"}
  ]
}
```

The sample abbreviates the remaining two status rows and the
`runtime_contract_overlay` and `artifact_objectives` material. The latter
contains the owner conversation, current objectives, and exact offered draft
source IDs/digests. The illustrated review involves plot, motivation, and prose,
and offers `task_demo`/`1`; real output must select an exact pair offered by that
call. A provider that needs the manuscript can request a bounded lexical SEARCH:

```json
{"action":{"kind":"search","queries":[{"text":"planned reveal opening","source_kinds":["writing_artifact"]}]}}
```

Runtime returns a compact continuation observation. A hit includes source,
revision, boundary, provenance, snippet, and an opaque invocation-local
handle; it is not yet content evidence. The provider asks to READ that handle:

```json
{"action":{"kind":"read","handles":["<handle>"]}}
```

The READ continuation contains the selected untrusted text and a new evidence
ID such as `read_2_1`. Runtime associates that receipt with the active
invocation; the provider's final ANSWER returns only the semantic proposal:

```json
{
  "action": {
    "kind": "answer",
    "result": {
      "kind": "proposal",
      "message": "I propose reviews of opening order, motivation, and prose.",
      "authoring_scope": {
        "activity": "review",
        "target_paths": ["core_documents/global_outline.md", "manuscript/part-01/opening.md"],
        "domains": ["plot_structure", "character_world", "tone_execution"],
        "task": "task_demo@1"
      },
      "proposal": {
        "assignments": [
          {"agent_id":"plot_architect","instruction":"Review opening order against the outline.","input_ids":["user_input","read_2_1","read_2_2"],"mode":"review"},
          {"agent_id":"character_custodian","instruction":"Review motivation in the opening.","input_ids":["user_input","read_2_1","read_2_2"],"mode":"review"},
          {"agent_id":"execution_specialist","instruction":"Review the opening prose.","input_ids":["user_input","read_2_1","read_2_2"],"mode":"review"}
        ]
      }
    }
  }
}
```

Runtime maps only those current used READs to durable content-free receipts,
checks that two distinct nonempty receipts can prove `conflicting`, persists a
typed scoped proposal, and returns its evidence and confirmation request to the
caller. All affected specialists are disclosed before work starts. It does not
independently decide that the two passages are semantically inconsistent.

A complete, non-truncated zero-hit SEARCH instead includes a content-free
`search_<action>` receipt in the observation. That receipt can support
`missing` only when the searched scope includes canonical writing artifacts.
A conversation-only zero hit, a truncated search, a hit that was never READ,
or a READ not associated with the accepted answer cannot ground the claim.

For a story-file change, the lead ANSWER uses the same envelope but chooses
`kind=proposal`, sets the ordinary `proposal` to `null`, and returns one strict
provider-neutral `artifact_intent`. For example, a range reference might be
resolved to `reference_1` by runtime. This illustration assumes all required
current-task specialist drafts are already recorded and integrated; it is not
a shortcut from an authoring brief to review. The synthetic task pair below
must match the offered active task:

```json
{
  "action": {
    "kind": "answer",
    "result": {
      "kind": "proposal",
      "message": "I prepared an exact manuscript patch candidate for review.",
      "authoring_scope": {"activity":"review","target_paths":["manuscript/part-01/opening.md"],"domains":["plot_structure","character_world"],"task":"task_demo@1"},
      "artifact_intent": {"operations":[{"kind":"patch","evidence_id":"reference_1","replacement_text":"<proposed replacement>"}]}
    }
  }
}
```

The provider does not supply a candidate ID, path authority, reviewer ID,
digest, Apply flag, or Git flag. Runtime resolves `reference_1` to its current
canonical path/range, captures the complete artifact state, builds an exact
change set, validates projected relationships, derives mandatory review
domains, unions the explicitly requested additional domains in canonical
order, and computes the candidate digest. An unknown or authority-bearing
provider field violates the closed wire-v7 result schema, so the response is
repaired once before candidate construction. If repair also fails, runtime
emits a visible safe no-action Lead answer and continues the conversation. A
schema-valid intent that cannot satisfy runtime artifact semantics instead
closes as `artifact_intent_rejected`; runtime never infers missing paths, task
selectors, or other authority.

Every front-channel result supplies only the semantic fields for its selected
variant. CLARIFY requires `kind=question`; CONSULT requires an ordinary
consultation proposal; PREPARE requires an artifact intent. Once PREPARE is
selected, bounded same-turn correction cannot replace it with advice or a prose
completion claim. Runtime derives absent bookkeeping and no-action defaults; it
never derives authority. Ordinary synthesis and specialist and artifact-review
synthesis retain separate minimal contracts. Fresh Lead policies use v5.

### Specialist assignment: initial input

The dynamic material includes the exact assignment, its input subset, and the
strict answer contract. Runtime loads authorized reference bytes locally and
adds them under `untrusted_writing_artifact_context` without a provider prelude.

```json
{
  "c": "<correlation-1>",
  "q": ["specialist_assignment", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["specialist_assignment", "specialist-reply-v2"]],
  "i": {
    "workflow": "lead_specialist_consultation",
    "authoring_scope": {"schema_version":1,"activity":"review","target_paths":["core_documents/global_outline.md","manuscript/part-01/opening.md"],"domains":["plot_structure","character_world","tone_execution"],"interpretation":"Review the supplied opening for timing, motivation, and prose.","task_id":"task_demo","task_revision":1},
    "assignment": {
      "assignment_id": "<assignment-id>",
      "instruction": "Check the outline against the opening range.",
      "mode": "review",
      "review_basis": {"schema_version":1,"candidate_input_ids":["reference_3"],"criteria":"Check opening order against the outline."},
      "input_ids": ["user_input", "reference_1", "reference_3"],
      "round": 1
    },
    "inputs": [
      {"input_id": "user_input", "kind": "user_input", "locator": "conversation/<turn-id>/user_input", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null, "text": "Check the opening."},
      {"input_id": "reference_1", "kind": "core_document", "locator": "${core_documents/global_outline.md}", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null},
      {"input_id": "reference_3", "kind": "manuscript", "locator": "${manuscript/part-01/opening.md}#L20-L32", "revision_digest": "sha256:<digest>", "start_line": 20, "end_line": 32}
    ],
    "answer_contract": {
      "assessment": ["supports", "concern", "conflict", "insufficient_evidence"],
      "mode": "review",
      "assignment_id": "<assignment-id>",
      "input_ids": ["user_input", "reference_1", "reference_3"],
      "message": "nonempty visible specialist assessment"
    }
  },
  "untrusted_writing_artifact_context": [
    {"ordinal": 1, "content": "<authorized whole outline>"},
    {"ordinal": 2, "content": "<authorized manuscript lines 20-32>"}
  ]
}
```

For artifact review, every disclosed specialist receives all disclosed
evidence plus one identical `artifact_candidate` input. The content-bearing
candidate is a strict canonical object, not a path to reread and not write
authority. This abbreviated rendering replaces the two nested canonical
objects with labels:

```json
{
  "input_id": "artifact_candidate",
  "kind": "artifact_candidate",
  "locator": "artifact-candidate/<candidate-id>",
  "revision_digest": "sha256:<candidate-digest>",
  "start_line": null,
  "end_line": null,
  "candidate": {
    "schema_version": 1,
    "candidate_id": "<candidate-id>",
    "candidate_revision": 1,
    "candidate_digest": "sha256:<candidate-digest>",
    "change_set": "<exact canonical change-set object>",
    "relationship_validation": "<exact projected relationship facts>",
    "grounding_evidence_ids": ["reference_1", "reference_2"],
    "mandatory_review_domains": ["tone_execution"],
    "disclosed_review_domains": ["plot_structure", "character_world", "tone_execution"]
  }
}
```

Runtime derives `plot_architect`, `character_custodian`, and
`execution_specialist` from those canonical domains. Reviewers cannot modify
the candidate or delegate. Each returns only its exact assignment mode and ID, exact
input IDs, assessment, and visible message.

### Specialist SEARCH, READ, and ANSWER

The provider can request bounded lexical search:

```json
{"action":{"kind":"search","queries":[{"text":"opening causality","source_kinds":["writing_artifact","conversation"]}]}}
```

Runtime searches only this invocation's authorized sources and returns an
opaque handle in the continuation observation. The provider requests that
handle:

```json
{"action":{"kind":"read","handles":["<handle>"]}}
```

After runtime recaptures and validates the source, the provider answers with
only its assessment and visible message:

```json
{
  "action": {
    "kind": "answer",
    "result": {"assessment":"concern","message":"The opening advances the reveal before its outline cause."}
  }
}
```

SEARCH and READ can inspect canonical writing artifacts, focused conversation,
active debate, or the invoking agent's own state only as the route permits.
They are not web search, shell access, a specialist call, or write authority.

### Lead synthesis input and answer

Only after every assignment succeeds does runtime call the lead with all typed
replies and the exact attribution contract:

```json
{
  "c": "<synthesis-correlation>",
  "q": ["lead_synthesis", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["lead_synthesis", "lead-synthesis-v3"]],
  "i": {
    "workflow": "lead_synthesis",
    "inputs": [
      {"input_id": "user_input", "kind": "user_input", "locator": "conversation/<turn-id>/user_input", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null, "text": "Check the opening."},
      {"input_id": "reference_1", "kind": "core_document", "locator": "${core_documents/global_outline.md}", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null},
      {"input_id": "read_2_1", "kind": "manuscript", "locator": "${manuscript/part-01/opening.md}#L20-L32", "revision_digest": "sha256:<digest>", "start_line": 20, "end_line": 32}
    ],
    "replies": [
      {"mode":"review", "assessment": "concern", "assignment_id": "<assignment-1>", "input_ids": ["user_input", "reference_1", "read_2_1"], "message": "The reveal is too early.", "reply_event_id": "<reply-event-1>"},
      {"mode":"review", "assessment": "supports", "assignment_id": "<assignment-2>", "input_ids": ["user_input", "reference_1", "read_2_1"], "message": "The character motivation is plausible.", "reply_event_id": "<reply-event-2>"},
      {"mode":"review", "assessment":"supports", "assignment_id":"<assignment-3>", "input_ids":["user_input","reference_1","read_2_1"], "message":"The prose supports the intended scene form.", "reply_event_id":"<reply-event-3>"}
    ],
    "answer_contract": {
      "scope": "story_specific",
      "authoring_scope": {"schema_version":1,"activity":"review","target_paths":["core_documents/global_outline.md","manuscript/part-01/opening.md"],"domains":["plot_structure","character_world","tone_execution"],"interpretation":"Review the supplied opening for timing, motivation, and prose.","task_id":"task_demo","task_revision":1},
      "evidence_status": "conflicting",
      "evidence_ids": ["reference_1", "read_2_1"],
      "message": "nonempty visible synthesis",
      "reply_event_ids": ["<reply-event-1>", "<reply-event-2>", "<reply-event-3>"],
      "unresolved_input_ids": ["user_input", "reference_1", "read_2_1"],
      "next_intent": "null or an exact same-lineage proposal intent",
      "recommended_action": "null or one typed preparation action"
    }
  }
}
```

The terminal provider answer returns only its semantic message:

```json
{
  "action": {
    "kind": "answer",
    "result": {
      "message": "Motivation is plausible, but the reveal still conflicts with the outline timing."
    }
  }
}
```

Synthesis preserves the proposal's exact `authoring_scope`, story-specific scope,
evidence status, and ordered evidence IDs unchanged; specialist reply IDs remain separate
attribution. Synthesis may also use bounded SEARCH/READ before ANSWER, but new
reads do not replace the proposal evidence lineage. It cannot dispatch another
agent, write an artifact, or authorize Git. A strict `next_intent` is only a
semantic request for runtime to construct a fresh round-two proposal. Ordinary
synthesis uses `lead-synthesis-v3`: `recommended_action` is nullable and mutually
exclusive with `next_intent`. Without either, the visible outcome is an answer.
An action produces a separately confirmable recommendation while the historical
durable synthesis payload remains unchanged.

For artifact review, the synthesis input additionally includes the same exact
`artifact_candidate`. Its independent minimal answer contract requires only
`decision` and `message`. This uses `artifact-review-synthesis-v3`, independently
of the candidate codec version:

```json
{
  "action": {
    "kind": "answer",
    "result": {"decision":"propose","message":"The candidate is coherent after preserving the stated concern."}
  }
}
```

`decision=propose` is not a majority tally: the lead must account for every
reply and retain every input from a `concern`, `conflict`, or
`insufficient_evidence` reply in `unresolved_input_ids`. `revise` returns
`revision_required`; `reject` returns `rejected`; neither creates an immutable
reviewed proposal.

## State meanings and next actions

Collaboration step state describes confirmed provider work. Artifact proposal
state separately describes the exact change candidate carried by that step.
Applications should show both when `LeadTurnResult.artifact_proposal` is
present.

| State | External meaning | Allowed next action |
| --- | --- | --- |
| `awaiting_confirmation` | The exact disclosed proposal is pending; added work has not started. | Confirm, Reject, Revise, or send ordinary text (which supersedes it). |
| `confirmed` | The exact confirmation is durable; execution normally starts in the same method call. | Transient internally; after lost process authority, reconfirm. |
| `reconfirmation_required` | Confirmation survived, but the private single-use start authority did not. Proposal/digest are unchanged and request ID is fresh. | Confirm, Reject, or Revise the fresh request. |
| `rejected` | The proposal did not run. | Continue with the lead. |
| `superseded` | This revision can never be confirmed. | Follow its replacement turn or continue with the lead. |
| `running` | Attempt identity and every ordered dispatch are durable; provider work may be active. | Wait for the live owner, or locally finalize a known durable result. |
| `completed` | Successful result and visible lead/specialist message are durable. | Continue with the lead; no later effect is implied. |
| `interrupted` | Work was cancelled/interrupted and every knowable result was preserved. | Continue with the lead; never infer missing success. |
| `failed` | A known assignment or synthesis failure is durable. | Continue with the lead using preserved facts; no automatic semantic synthesis. |
| `outcome_unknown` | A submitted call may have completed, but no authoritative outcome proves it. | Inspect and resolve; runtime never replays it. |
| `recovery_required` | History or call-boundary authority is invalid or uncertain. | Repair authoritative history before normal collaboration. |

Artifact proposal states mean:

| Artifact state | External meaning | Safe user response |
| --- | --- | --- |
| `awaiting_consultation_confirmation` | One exact candidate and its reviewers are disclosed; review has not started. | Confirm, Reject, or Revise the exact consultation. |
| `reviewing` | Confirmed reviewers are assessing the immutable candidate. | Wait for the live owner or recovery result. |
| `reviewed` | Every reviewer returned and the lead selected `propose`; an immutable reviewed proposal exists. No write or Git authority exists. | Inspect it; a later Apply operation needs separate confirmation. |
| `revision_required` | Review completed and the lead selected `revise`; no reviewed proposal exists. | Ask the lead for a fresh candidate. |
| `rejected` | The user rejected review or the lead rejected the reviewed candidate. | Continue with the lead; no file changed. |
| `superseded` | A revision or ordinary new turn replaced this candidate. | Use only the fresh candidate, if any. |
| `stale` | Evidence or complete base state changed before review/finalization. | Ask the lead to rebuild from current artifacts. |
| `failed` | Review or synthesis was incomplete/invalid. | Use preserved facts and ask the lead how to continue. |
| `recovery_required` | Authoritative artifact-review history cannot safely continue. | Repair history before normal collaboration. |

Evidence rejection is not a proposal state. The returned turn has `state=None`,
`evidence=None`, kind `recommendation`, and next action `retry_lead_turn`. The
visible message says that runtime evidence validation failed. A typed
`lead_outcome_failure` event closes the exact submitted call, so retrying starts
a fresh turn and never replays the rejected result.

The history projection may expose `none`, `finalize_result`,
`start_or_reconfirm`, `inspect_and_resolve_outcome`, or
`repair_authoritative_history` as state-specific next actions. These describe
what is safe; they do not themselves authorize execution.

Apply and Commit have their own pending controls. Ordinary Lead or advisory
input rediscloses the same exact request without a provider call or history
append. A returned `LeadTurnResult.artifact_application_request` pairs with
`submit_application_decision`; its trailing optional `story_commit_request`
pairs with `submit_commit_decision`. Known terminal cycles allow fresh
authoring and new exact requests. Unknown outcomes or recovery states block
new work, and prior objective satisfaction remains durable.

## Internal durability and recovery

The confirmation path enforces this order:

1. Re-read the authoritative tail and revalidate proposal identity, bound lead
   evidence, references, core statuses/READ boundaries/zero-hit source
   fingerprint, required reviewers, profiles, context policy, and budget.
2. Append the user's decision and `confirmed` state.
3. Consume one process-local capability and atomically append `running` plus
   every ordered assignment dispatch before any provider call.
4. Run one to three assignment invocations concurrently, each with disjoint
   call allocation and isolated reference/context authority.
5. Join every sibling. If all succeed, run separately budgeted lead synthesis;
   otherwise skip synthesis.
6. Atomically append typed replies, known terminal outcomes, synthesis when
   present, and the execution result.
7. Deterministically append the visible notice and terminal state, or the
   completed round-one notice plus a fresh round-two proposal.
8. Release bound-agent resources and remove the live execution marker.

For an artifact candidate, the same order is tightened: candidate freshness is
checked before the confirmation batch; `reviewing` is correlated to the one
execution attempt; each derived reviewer receives the identical serialized
candidate; the provider artifact synthesis supplies only `decision` (`propose`,
`revise`, or `reject`) and `message`. Runtime uses the message as rationale,
adds the no-change user impact, and reports either no additional limitations or
the exact unresolved reviewer input IDs. Successful `propose`
finalization reprojects the exact change set against a fresh complete-state
capture. Only then does runtime persist `ArtifactReviewedProposal`, still with
zero Apply/write/Git authority.

Restart behavior follows durable facts:

- `confirmed` without the private capability becomes
  `reconfirmation_required` with zero provider calls;
- `running` plus an authoritative result is finalized locally with zero
  provider calls;
- unresolved `running` without a live owner/result becomes `outcome_unknown`
  and is never replayed; and
- corrupt or ambiguous authority becomes `recovery_required`.

For a consultation proposed by the Lead, the reconfirmation turn returns the
unchanged original proposal with its existing evidence: the same scope, status,
and ordered receipts. It issues a fresh confirmation request; the old
confirmation authority is not revived. Returning this proposal makes no
provider call and grants no artifact-write authority. Only a fresh structured
Confirm can start the disclosed work after normal revalidation.

### Submitted Lead outcome unavailable after restart

An eligible read-only Lead attempt without a usable result exposes
`retry_lead_turn`. This includes timeout, provider failure, rejected final
output or evidence, cancellation, interruption, and exact legacy unstarted,
prepared-only, or unmatched submitted requests. Runtime preserves the original
request and closes the failed attempt with `lead_outcome_failure`; it does not
record a successful judgment or revive confirmation authority.

This includes a known read-only failure after mandatory references when the
exact attempt and reference accounting can be closed. The authoring objective
stays open. Failed closure or uncertain reference cleanup retains the recovery
gate; the next explicitly requested turn must acquire fresh evidence.

For a response timeout, the returned recommendation is:

```text
[Lead]
  Outcome: recommendation
  Lead: The Lead provider attempt exceeded its response timeout.
        Your original request and failure remain in history.
        Send a new message or resubmit your request in this conversation.
  Next action: retry_lead_turn
```

Send a new message, revise the wording, or explicitly resubmit the request in
the same conversation, including after restarting and reopening the session.
This starts a fresh turn. Reopening, reading history, and projection are
provider-free and do not close or replay an attempt. For an eligible legacy
request without a closure, explicit new input holds the session and
conversation leases, closes the prior attempt, verifies the new tail, and only
then appends the new user turn. Preparation without submission does not imply
external execution or spend a submitted call.

The projection checks that the attempt is read-only and has no successful
judgment, specialist/synthesis work, unresolved execution authority, or pending
or uncertain Apply/Commit outcome. A failed Revise request leaves the old
proposal superseded; there is no replacement proposal or confirmation request.
If closure cannot be written or verified, or history is corrupt, normal work
remains recovery-gated. An eligible proposal-free Lead retry is session
`ready` after normal resource checks; other unresolved owner outcomes retain
their restricted transition rules.

A fresh turn retires only the unfinished native operation matching an
abandoned Lead attempt. It preserves a successful session checkpoint from
another conversation. The original request and failure remain history, and
fresh reference evidence is required for new work. No automatic full-turn or
successful-specialist replay, Apply authority, or Commit authority is granted.
The existing bounded malformed-response correction and proven pre-execution
continuation replacement remain separate runtime paths. See the exact
[failure payload and projection](../reference/collaboration-history.md#state-and-user-action-contract)
and [provider continuation](../reference/backend-and-sessions.md#lead-session-continuation).

Task cancellation attempts the same durable failure closure and is re-raised
after bound resources and leases are released. Recovery never converts
cancellation into a successful return.

Outer cancellation cancels and joins all child tasks, persists every knowable
result, clears the live marker, and re-raises cancellation to the caller.

## Budget and accounting

One lineage permits at most two specialist rounds and twelve submitted physical
provider calls. Each invocation receives a disjoint reservation; siblings
cannot borrow each other's unused calls. A consultation reserves at least one
lead-synthesis call; a direct advisory reserves zero synthesis calls.

Submitted calls include:

- initial provider call;
- SEARCH, READ, and final ANSWER continuations; and
- calls that were submitted but then failed, timed out, or were cancelled.

Every call first records `PREPARED`. Only `SUBMITTED` counts as spent. An
invocation is capped at five physical calls and may receive less when the
lineage has fewer calls left. Runtime constructs feasible allocations before
confirmation, carries actual spend into round two, and makes round three and
physical call thirteen unreachable.

<a id="reviewed-now-versus-apply-and-commit-later"></a>

## Review, Apply, and Commit

The implemented workflow constructs an exact complete-state-bound candidate,
reviews it, and persists an immutable `ArtifactReviewedProposal` before
separate Apply and Commit decisions. The immutable reviewed value
contains exact candidate/reviewer/evidence/synthesis identity plus rationale,
user impact, limitations, and unresolved inputs; it hard-codes all side-effect
and authority flags to false.

The current story-change workflow then has two independently confirmed
operations:

```text
reviewed artifact proposal
  -> lead semantically triggers a separately disclosed Artifact Apply
  -> user confirms Apply
  -> runtime writes the artifact and verifies the result
  -> lead semantically triggers a separately disclosed Git Commit
  -> user confirms Commit
  -> runtime stages and commits the disclosed files
```

The lead is the semantic trigger and user-facing coordinator at every step;
runtime code is the only executor. Consultation confirmation never doubles as
Apply or Commit confirmation.

Only a matching successful Apply satisfies the authoring objective. Before
that result, rejection, revision, staleness, or failure leaves it open. A
terminal Commit result preserves prior objective satisfaction and cannot
authorize the next cycle. Pending Apply and Commit requests are redisclosed
exactly, and each new cycle requires fresh decisions. No local Commit request
authorizes remote push or merge.

See the [runtime API](../reference/runtime-api.md),
[collaboration-history reference](../reference/collaboration-history.md),
[agent-context design](../design/agent-context.md), and
[artifact-reference guide](artifact-references.md).
