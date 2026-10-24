# Lead agent: story workflows and reviewed artifact proposals

The Lead Orchestrator is the normal user-facing story agent in both the
interactive shell and Python runtime. Users discuss story work with the lead; they do not need to choose an
agent for every turn. The lead answers workflow/status questions and asks
focused clarification questions directly. The lead can also answer factual questions by inspecting current writing-artifact
READs under a review scope covering the relevant domains. This inspection does
not draft or revise story text or grant specialist, candidate or write authority.
Substantive story critiques, drafting, revision and specialist review require a
bounded proposal covering every affected specialist domain. The Lead may first recommend preparing that scoped
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

## Grounded official authoring

Official outlines, character material, narrative rules and manuscripts need foundations that support the requested work, a USER direction, and explicit limits on creative choices. The runtime binds the assessment to the exact request, task, revision and current source snapshot before drafting can be dispatched.

A detailed literal USER brief can supply those foundations and creative latitude. An empty target READ records that the target has no content; it does not forbid creating an initial document from a sufficient brief. A bare request to write an outline, with no story foundation or direction, requires a question about the actual missing material or decisions. The Lead reuses facts and choices already supplied.

The assessment names required foundations and supporting source IDs, the USER direction, permitted creative actions, protected constraints, supplied or explicitly delegated decisions, and any missing information. Source availability and semantic adequacy are separate: nonempty text, summaries, confidence or an asserted receipt do not replace the host's exact source qualification. Rejected public responses can remain attributable selected input, with their original rejected status, and undergo fresh qualification.

Each offered Lead response has a required schema ID bound to its final field
and value rules, validation and corrective guidance. Review-only intent permits
review work; workflow activity has no story-domain or target selections. Writing
keeps its actual domains, roles and assessment obligations. A rejected reply and
its bounded correction remain separate answers. A necessary clarification keeps
the current task and document request pending; it does not claim that drafting,
review, Apply or Commit completed. See [provider validation and repair](../reference/provider-wire.md#validation-and-accounting).

Ordinary Lead and adoption obtain the assessment through their existing Lead front. Direct specialist and consensus AUTHORING API intakes accept an explicit `authoring_assessment`; absent or insufficient input returns `AuthoringAdmissionNeeded` with actionable guidance and available source IDs. Callers use those offered IDs and the retained actual input instead of inventing receipts. Assessment input grants no confirmation or write authority. Review-only work keeps its existing exact-input contract, and drafting still proceeds through specialist confirmation, all required final-candidate reviews, Apply and Commit.


## Editing goals and protected conditions

The Lead's shared editorial brief distinguishes explicit change prohibitions,
preferences that can be traded off with reasons, and undecided assumptions.
Prohibitions take priority over improvement goals. For example, "make the
ending more shocking without revealing the stranger's allegiance" allows
changes to rhythm or gesture while keeping the allegiance unresolved.

Every affected specialist receives the same protected elements, permitted
changes, and conflict-handling criteria. If two explicit requirements conflict,
the Lead should identify the conflicting condition and evidence, explain what
can be achieved within the allowed scope, and offer alternatives with their
costs. It asks for a material choice only when the user's existing answers or
delegation do not resolve it. An unchanged result or a grounded explanation of
a limit can be a useful outcome.

The Lead checks protected events, dialogue, voice, and ambiguity after
integration; all required specialists still review the exact final candidate.
Changing a condition needs a newly disclosed scope and revision with fresh
confirmations. Earlier drafts, reviews, or confirmations grant no authority for
that change. Review-only input retains its exact bytes and permits critique
without starting a rewrite or Apply.

These are provider policy requirements. Runtime checks establish exact
scope/source/candidate identity and authority; they do not prove the model made
a sound editorial judgment. The [evaluation protocol](../evaluations/lead-editorial-coordination.md)
tracks blinded human judgments and remaining evidence separately.

## What users can do now

| Users can | Users cannot yet |
| --- | --- |
| Converse with the lead through focused ordinary input in the installed shell, `StoryRuntime.lead_turn()`, or `RuntimeSession.lead_turn()`. | Send story text before attaching a ready session and focusing an active conversation. |
| Attach several ordered whole-file or ranged `${path}` references and inspect the result's content-free evidence. | Treat an explicit missing `${path}` as proof of absence; it fails before the substantive lead call. |
| Receive an evidence-grounded answer, question, critique, recommendation, or consultation proposal. | Assume runtime performed semantic conflict detection or proved that every relevant artifact was selected. |
| Confirm, reject, or revise the exact current proposal. | Confirm with free text such as `Yes`, or replay stale confirmation fields. |
| Receive an exact runtime-built artifact candidate, confirm review by every disclosed specialist, and receive a reviewed/revise/reject result. | Treat a candidate or reviewed proposal as Apply, write, stage, commit, or Git authority. |
| Run one to three specialists in parallel after confirmation and receive a typed lead synthesis. | Treat consultation or review confirmation as Apply or Commit authority. |
| Separately confirm exact Apply and local Commit requests through the installed shell or public runtime methods. | Push, merge, mutate a remote, or give a provider direct write/Git authority. |
| Request one direct specialist advisory without a lead planning call. | Bypass the exact ready session-worktree requirement or fall back to `main`. |
| Reconfirm safe pre-start work after restart and locally finalize known results without replay. | Automatically retry an uncertain submitted provider call. |

Local provider request/response diagnostics, collaboration events, and
content-free accounting facts are plaintext unless the surrounding environment
protects them. Referenced story bytes are sent to the configured provider when
that invocation is authorized, and the provider may retain them under its own
policy.

## Exploration and adoption

Exploration is an explicit user choice for one turn. It lets the Lead compare
hypotheses, alternatives or short examples while showing the material as
provisional. The next ordinary message returns to normal orchestration; neither
history nor a provider response can enable exploration.

From Python, pass the mode through either runtime facade:

```python
from story_writing_agents.lead_exploration import LeadInteractionMode

result = await session.lead_turn(
    conversation_id,
    "Explore two ways to stage the ending.",
    interaction_mode=LeadInteractionMode.EXPLORATION,
)
```

Exploration can use bounded authorized artifact reads. A grounded observation
about an existing artifact must cite actual read evidence with its exact source,
revision and range; a search hit, user assertion or provisional idea does not
establish that evidence. Ideas remain provisional even when they draw on a
grounded observation.

An exploration result cannot dispatch specialists, prepare a manuscript
candidate, open or satisfy an authoring objective, Apply a change or Commit.
Existing pending decisions and recovery conditions retain priority. Restart or
conversation switching preserves the report's attribution without retaining
exploration as a standing mode.

Adoption selects one whole idea or an exact character range from the latest
typed report in any conversation of the same session. The focused conversation
owns the new task; the source conversation supplies input only. Range bounds are
zero-based Unicode code points, start included and end excluded. Selected text
retains its original whitespace and Unicode bytes.

Choose Authoring or Review only explicitly. Authoring starts fresh drafting or
revision, affected specialist work and complete review of the final candidate.
Review only evaluates the exact selected supplied text and persists the actual
review report without rewriting it. Both begin a fresh task at revision 1 and
require a new proposal confirmation. Neither imports draft, READ, review,
Confirm, Apply or Commit authority from the source.

The capture pins the source conversation, report event and digest, idea identity,
full material digest, selected range and selected-text digest. A later report
does not replace an already captured source. A missing or changed pinned source
fails closed on inspection or decision, including after restart. New selections
must use the latest report. Invalid ranges or unavailable sources are refused
before a new USER capture or provider submission.

An unresolved Apply, Commit, prepared/executing operation or recovery boundary in
any session conversation blocks new exploration or adoption. The error names
the owning conversation and its required control. Resolve that control there;
switching conversations does not bypass it. This priority applies to explicit
exploration/adoption admission; ordinary discussion keeps its existing rules.

## Editorial decisions

Use `record_editorial_decision()` to retain a story direction, alternative, protected
ambiguity, setup obligation or unresolved concern as an explicit user decision. Each
record keeps its exact text, reason, alternatives and source references. Inspect the
current view with `inspect_editorial_state()`; the Lead also receives a bounded cited
view across conversations and restart.

| User action | Recorded effect |
| --- | --- |
| DIRECTIVE | Create a selected record. |
| PROPOSE | Retain an idea as proposed. |
| SELECT, REJECT or SUPERSEDE | Record a new approval revision against the exact current receipt. |
| REVISE | Replace the record's content with an explicit proposed or selected revision. |

Revisions retain the original conversation, user event and task provenance. A later
revision can be recorded in another conversation of the same session without changing
that origin. Stale or foreign receipts cannot overwrite the current revision. Rejected
alternatives remain rejected, and selected ambiguity or setup remains selected until
an explicit user revision changes it. Ordinary prose and provider assertions cannot
perform these controls. Pending workflow controls retain priority.

SELECT accepts a current proposed record. Use an explicit REVISE operation to restore
a rejected or superseded record to selected intent.

Consultation proposals pin the exact selected records delivered to the Lead. Confirm
checks that those revisions remain current, and final candidate review inherits the
same references. A matching Apply result records historical use of the exact task and
candidate. It does not resolve ambiguity, prove a setup paid off or establish current
artifact contents. Current artifact claims still require the existing READ receipts.

See the [editorial memory API](../reference/runtime-api.md#editorial-memory-methods)
for typed intake and inspection.

### Scope, relations, and application evidence

An editorial decision carries its intended application scope separately from the document
that supplied its evidence. Use a canonical artifact target, or an inclusive line range
anchored to its exact source digest. A named scene or work-wide meaning that cannot be
mapped to a known target remains unknown and needs clarification. Source drift does not
silently rebase a range or erase the original user intent.

Overlapping targets do not establish a semantic contradiction. Explicit user relations
record coexistence, conflict, or replacement against exact current decision references.
A proposed alternative cannot replace selected direction. Partial replacement keeps the
original decision's remaining coverage and history; restoring an earlier direction makes
a fresh user decision. Recency and a narrower scope do not select a winner.

Inspection separates selected direction, effective remaining coverage, unresolved
conflicts, current source observations, and historical application attempts. Selecting a
new direction does not update manuscript bytes. Failed or uncertain Apply attempts stay
visible with their task, revision, candidate, source, and target identities. A matching
successful receipt establishes historical use; it does not prove semantic fulfillment or
that the current manuscript still contains those bytes. Current observations require
qualified source evidence.

Proposals with editorial references also capture the relevant relation and coverage
context. If another decision changes that context, an old confirmation becomes stale
even when the original referenced decision itself has not changed. Obtain a fresh
proposal after resolving the relevant conflict or replacement.

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

When you approve a completed draft and ask for a new document, the Lead retains
the draft's current task and specialist provenance. It can use the approved text
for candidate preparation without asking the specialists to draft it again when
all required current-task drafts are already present. A preparation recommendation
can select the earlier draft as context; confirming that recommendation binds
the exact selected draft to the preparation request. A prior draft's event ID is
not automatically an input for a new specialist assignment.

New manuscript documents use an unused `manuscript/PART/PATH.md` destination,
such as `manuscript/essays/approved-draft.md`. The part and filename components
follow the existing ASCII path rules; a title and body in Korean or another
language remain inside the document. The Lead discloses the chosen destination
and exact candidate before review and Apply confirmation. A request for a new
document does not itself create or save a file.

Use `decide_lead_recommendation()` on either runtime facade; it returns the
existing `LeadTurnResult`. The complete
[decision example](../reference/runtime-api.md#leadrecommendationdecision)
uses every returned request identity. Historical recommendations and runtime
recovery notices are not turned into action requests. An objective is satisfied
only by a matching successful Apply, regardless of preparation completion.

For confirmed consultation preparation, the provider selects the confirmed story
sources. After checking the current request and decision, runtime supplies the
`recommendation_request` and `recommendation_decision` metadata aliases itself.
Omitting those two aliases does not invalidate an otherwise complete proposal.
Omitting a real selected source, changing the confirmed scope, or reusing stale
confirmation still fails before specialist execution. These metadata aliases do
not become candidate material or grant review, Apply, or Commit authority.

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
| `answer` | Workflow/status guidance or a verified factual writing-READ inspection, without story authoring. | `continue_with_lead` |
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

When structured validation rejects a provider reply, the live Lead result and
`/history` show ordered `validation` notices. Each chunk identifies the
`Original response` or `Repair response`; together the chunks list every
independently detectable schema issue in ordinary reports using trusted JSON
property paths, expected and actual JSON types, or a fixed rule. Reports that
exceed the diagnostic cap list up to 2,048 findings, then say additional issues
were omitted.
For example, a missing
`$.agent_action.task_result.response_details.task_scope` is described as a missing required property;
unexpected property names and provider values are never printed. Python
callers can read the same ordered chunks from `LeadTurnResult.validation_details`;
direct `AgentReply` and `ConversationReply` values also expose
`validation_details`. Domain validation identifies the rejected property and the
required rule, including relationships between properties, at the first violated
invariant. Validation notices display each diagnostic line separately. Each
rejected physical call also records its findings in the `/exchanges` outcome when
exchange capture succeeds. A successful repair retains the original rejection
details. If repair fails, the safe no-action reply retains both attempts. The
optional field is empty for replies without validation findings and absent
from older serialized history.

Durable notices can be matched to their submitted provider calls, so
diagnostics from concurrent specialists keep their physical-call order when a
result is finalized after restart.

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

The lead discloses its interpretation of free text in the current `AuthoringScope` schema 3:
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

Each assignment explicitly uses `draft`, `constraint_assessment`, or `review`.
Drafting produces `drafted` material from an adequate brief, including an empty
target. Constraint assessment returns `constraints_assessed`, `concern`,
`conflict`, or `insufficient_evidence`; it supplies constraints to the draft owner
and cannot supply draft proof or review support. Review requires `ReviewBasis`
schema 1 with nonempty `candidate_input_ids` and visible `criteria`; a brief or
status receipt alone is not a review candidate. The candidate subset and criteria
remain independent of the assignment's other required inputs and task instructions.
A continuation preserves its frozen candidate eligibility after READ. When the user supplies a complete
candidate, the Lead must disclose that interpretation.

Continuation assignments keep the original required input set after READ adds
evidence. Review candidates remain separately frozen within that set.

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

### Contribution roles and confirmed shared premises

Current Lead proposals explicitly use scope version 3. Eligible ordinary new-step user fronts
offer `lead-response-v8`, including the separate premise-analysis branch. Preparation,
continuation, exploration/adoption, and selected-premise authoring intake do not offer that
branch; their bound `lead-response-v7` or `lead-response-v6` contracts remain selected.
`contributions` declares
one `draft_owner` or `constraint_assessor` for each affected domain. Domains
mapping to one specialist must agree on its role; the runtime never drops a
required specialist to resolve a conflict or fit a budget. Draft/revise work
requires a draft owner. Final candidate review still assigns every affected
specialist `review`, with the same immutable integrated candidate.

`dependency_slots` declares a premise needed by named draft-owning domains.
The Lead selects its exact text and offered basis aliases in `shared_premises`.
Before Confirm, the host captures every selected source revision and qualifies
conversation-local material with its source conversation. Each dependent author
must select all required sources and receives the same captured facts. Missing
choices, stale sources, undeclared slots, and omitted required inputs prevent
confirmation. These checks establish provenance; they do not infer dependencies
from prose or prove that a source is true.

The first confirmed authoring proposal pins the scope, duties, brief, premise
text, and qualified sources for that task revision. Equivalent source aliases
can change only with a fresh proposal and Confirm; reusing an alias for another
owner or material is not equivalent. Changed duties or premises require the
next explicit task revision, fresh confirmation, and fresh owner drafts.
Constraint results never satisfy those draft obligations. Historical scope
versions 1/2 and basis version 1 retain their exact encodings and authority;
omitted-version Python construction still defaults to scope 2 and basis 1.

A scope-3 draft-derived final review uses the exact separately confirmed Lead
preparation recommendation. The host inherits the successful source capture,
spent physical calls, and specialist-round count. A detached new step cannot
reset that lineage. The runtime reserves preparation calls and required READs,
all final reviewers, and synthesis before optional initial calls. Round one
performs drafting/constraint assessment; round two reviews the final candidate.
Final preparation for these confirmed drafts stays in their source conversation.
If you select the task elsewhere, focus that conversation to prepare its final
candidate; sharing source facts does not transfer execution authority.
An infeasible plan requires clarification or a fresh proposal within the same
two-round/twelve-call limits. Review, Apply, and Commit remain separate decisions.

While a completed local scope-v3 draft is offered for continuation, ordinary
Lead calls leave the preparation and full final-review minimum available,
including required READs and corrections. Fresh alternatives in that invocation
share this finite allowance. The separate advisory-consultation API accepts
historical scope1/2 only; scope3 uses the confirmed Lead flow.

### Choosing an unresolved premise

An already selected premise uses the authoring route above. For a material unresolved
question, the Lead can propose a separate premise-analysis consultation. Affected analysts
examine alternatives; the Lead returns provisional whole options with evidence, tradeoffs,
and a reasoned disposition of each actual reply, including residual costs and minority
concerns. This report is advice about a choice, with no drafting or approval authority.

These state names describe the user experience, rather than additional public methods.

| State or event | Meaning and next permitted action |
| --- | --- |
| Unresolved question | Disclose the question, sources, affected analysts, and bounded proposal. Reuse an already selected premise without another analysis. |
| Analysis awaiting confirmation | Confirm the exact consultation to start it, or reject it without analyst execution. Ordinary text does not confirm. |
| Revise before analysis Confirm | Return provider-free clarification while keeping that exact proposal pending. To change the question or sources, Reject and request a fresh proposal; already resolved premises can use the direct authoring route. |
| Analysis running | Retain actual calls and interruption state. Settle pending or uncertain outcomes before advancing a choice. |
| Options available | Show provisional whole options, recommendation, reply dispositions, residual costs, and actual spend. Dependent drafting does not start automatically. |
| Clarification needed | Insufficient evidence remains unresolved; missing material is not a selected premise. |
| Choice deferred | Keep the report unresolved for a later exact choice, including after restart. |
| Choice rejected | Close that report's selection path. A later genuine question requires new work. |
| Choice selected | Record exact report, option text/digest, user control, owning conversation, and task/revision. Disclose a fresh authoring proposal with its own confirmation. |
| Known front failure after selection | Retain the genuine choice and actual spend. Only the exact validated user/capture origin pair can allow safe closure; any execution authority or uncertain outcome still requires recovery. |
| Authoring running | All dependent writers receive the same captured premise. Analysis does not satisfy draft-owner obligations. |
| Draft premise conflict | Hold the draft and show its typed slot/capture conflict. A specialist cannot declare a user direction change. |
| User changes direction | Use the next explicit task revision, fresh confirmation, owner drafts, and final review. Old candidates and approvals grant no new authority. |
| Budget exhausted or recovery required | Preserve submitted calls and uncertainty. Resolve recovery first; permitted fresh work links earlier costs and discloses its own bounds. |

The Python entry point accepts a typed `premise_selection`; `/premise` exposes the same
choices in the shell. Selection binds the exact report in the current owning conversation.
An identical request-identifier retry returns its recorded result without repeating calls or
captures; changed text or selection arguments with that identifier are refused. Foreign-conversation and partial
adoption use their own workflow.

A report-bearing `LeadTurnResult` pairs `premise_report` with `premise_report_ref`. The latter
supplies the exact conversation, event, digest, and question pins. Let `chosen` be the whole
option the user selected from `result.premise_report.options`:

```python
from uuid import uuid4
from story_writing_agents.lead_premise_decisions import (
    PremiseChoicePolicy, PremiseDecisionAction, PremiseDecisionSelection,
)

ref = result.premise_report_ref
assert ref is not None
selection = PremiseDecisionSelection(
    request_id=uuid4().hex,
    action=PremiseDecisionAction.CHOOSE,
    source_conversation_id=ref.source_conversation_id,
    report_event_id=ref.report_event_id,
    report_digest=ref.report_digest,
    question_id=ref.question_id,
    choice_policy=PremiseChoicePolicy.USER_CHOICE,
    option_id=chosen.option_id,
    material_digest=chosen.material_digest,
)
proposal = await session.lead_turn(
    conversation_id, "Draft using this chosen premise.", premise_selection=selection,
)
```

Keep that same selection object for an identical retry. Use a new identifier for new intent.
This example requests targetless writing in a fresh task; its proposal still needs Confirm.
Supplying artifact targets or an exact current task/revision pair changes the disclosed fresh
authoring scope, with the same separate confirmation and final-review requirements.

Important choices default to the user. Delegated minor choices require a user-authored
allowlist of exact report options and material digests. The host may select only the supplied
recommendation when it belongs to that allowlist. Missing or out-of-set recommendations refuse
selection without a provider call. A general Confirm, provider suggestion, or first-turn policy
label does not grant delegation. Reject and defer never establish a premise.

A new task starts at revision 1. Continuing a known authoring task requires both its identifier
and exact current revision; the successor uses current revision + 1. Partial, stale, foreign, or
unresolved task references are refused. The selected source does not authorize Apply or Commit.
An explicit new request can reuse the same terminal report for either transition without
reopening analysis. If the earlier chosen-premise front failed before admitting a task, a
fresh request can disclose a new task while retaining that earlier choice and spend.
Targetless writing remains possible without artifact authority; artifact targets need complete
affected-domain coverage and current source pins.

Analysis ends after its analyst round and Lead synthesis. Fresh authoring separately uses the
existing draft/constraint round and exact-candidate final-review round. Their actual costs remain
distinct and visible together, without calling the total a single three-round/twelve-call job.
Count unique actual submitted call identities, including failed fronts, without double counting
shared cost references. Provider usage can remain unknown even when a submitted call is known.
Later reports cite their actual earlier terminal analysis report. Intervening failed ordinary
fronts retain their own steps and are shown separately; a prior-report link does not reduce an
ordinary READ-heavy request to the narrow two-call admission limit.

For exhausted-source fresh work, an authority-free front admission pins the exact new user
origin, invocation, offered identities, prior cost heads, and front-call limit before the first
call. Initial, READ/recovery, and structured correction calls share a conservative two-call cap;
renewing a delegate does not reset it. Invalid or unsupported submissions still consume calls.
The receipt does not reopen a closed question or authorize old preparation, continuation, or
artifact mutation. Unknown prepared outcomes keep recovery priority.

These guards establish provenance, workflow, and accounting. Alternative quality and semantic
fidelity of drafts still require genuine author-intent and reader/editor evaluation in #806.

A retained selection belongs to its report. A later independent report still allows Reject or Defer; those controls do not revoke the earlier capture. When fresh work offers several completed-task successors, the provider may choose any exact offered pair. An explicit premise selection instead pins its one disclosed pair. The durable offer limits task admission, while earlier source and cost receipts retain their separate provenance.

## How a lead judgment is grounded

For ordinary outcomes, the provider declares one semantic scope and one evidence
status. Runtime does not use NLP to relabel either value. Ambiguous story/workflow
requests are a prompt rule for the Lead: classify them as story-specific and ask a
grounded question. The separate typed premise-analysis intent derives its own host
scope. Evidence and authoring routing remain separate from execution authority:

| Declared scope and status | Minimum current evidence | Allowed visible kinds |
| --- | --- | --- |
| `workflow_meta` / `not_applicable` | No receipts | `answer`, `question`, `critique`, `recommendation` |
| `premise_analysis` / `not_applicable` | No receipts; exact typed analysis Spec in the same batch, with no AuthoringScope | Candidate-free `proposal` only |
| `story_specific` / `verified` | One nonempty attached reference or completed READ | `question`, `recommendation`, `proposal`; a factual `answer` also requires the current writing-READ review scope |
| `story_specific` / `missing` | An absent canonical-core status, or a complete non-truncated zero-hit SEARCH whose authorized scope includes writing artifacts | `question`, `recommendation`, `proposal` |
| `story_specific` / `empty` | A zero-byte canonical-core status or a zero-byte whole attached reference | `question`, `recommendation`, `proposal` |
| `story_specific` / `incomplete` | At least one current content or canonical-core status receipt | `question`, `recommendation`, `proposal` |
| `story_specific` / `conflicting` | At least two distinct, nonempty current content receipts | `question`, `recommendation`, `proposal` |

The `premise_analysis` scope is host-derived after strict validation of the
offered question and analyst assignments; a provider cannot select it. A typed
analysis proposal retains any genuine qualified READ/attached receipts and their
corresponding current-evidence requirements above. These receipts ground the
available inputs; the report options remain provisional. Context validation
requires the exact analysis Spec in the same batch and forbids an AuthoringScope.
It never grants drafting, candidate review, Apply or Commit authority.

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
`insufficient_evidence`. Constraint mode permits `constraints_assessed`, `concern`,
`conflict`, or `insufficient_evidence`, never `drafted` or `supports`.
Synthesis cites every reply event in order and retains
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
| A completed result is rejected. | Story received malformed or semantically invalid structured output; safe validation details can identify the rejected properties and rules. | Story requests one bounded repair. If repair fails, it returns a visible safe no-action Lead answer and keeps the conversation open. |
| Collaboration state is `outcome_unknown`. | Submission is durable, but no authoritative accepted or rejected result closes it. | Follow `inspect_and_resolve_outcome`; never replay the submitted call. |

For example, the former Codex failure was caused by a response schema whose
array `items` node had no explicit supported type. Codex rejected that schema
before returning a valid `ProviderResult`, so the exchange could show only
`provider_invocation_failed`. Agent-provider-wire-v10 uses a closed
`{agent_action}` root and embeds every action/result object directly. Runtime derives
correlation from the active call.
Conversely, an Antigravity result that adds undeclared bookkeeping fields or
omits a semantic field required by its selected variant is a returned-result
rejection, not `outcome_unknown`. These examples contain no provider-native
diagnostic or story content. Durable user history can retain the safe validation
details alongside the fixed classifications, never the raw rejected response.

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

The runtime binds the selected agent and drives one bounded contextual invocation.
Codex and Antigravity receive the same `agent-provider-wire-v10` payload and
response-schema semantics through their native transports. See the
[agent JSON field guide](../reference/provider-wire.md) for the complete contract,
field meanings, schema construction, and illustrative responses.

Each call carries `protocol_version`, `request_correlation_token`,
`response_contract`, and `contract_guidance`. Initial calls also carry the relevant
`task_input`, `session_history`, documents, and current draft. Continuations carry
`context_observation` and the current remaining action limits. Named objects
replace positional controls, source records, and candidate operations.

The response root is `agent_action`. Its `action_type` selects `search_context`,
`read_context`, or `submit_result`; the current schema includes only actions still
allowed by the invocation. SEARCH returns authorized READ handles. READ retrieves
exact source content. A terminal response embeds the semantic object directly in
`task_result`. The runtime owns correlation, attribution, and evidence receipts.

Every built-in result has an explicit `response_type`. The Lead contract,
`lead-response-v7`, distinguishes answers, clarification questions, critiques,
proposal-drafting recommendations, specialist-consultation proposals, and artifact
change proposals. Scoped Lead results include `response_text` and
`response_details.task_scope`; the scope selects an exact offered `task: {id, revision}`
pair. Other response families retain their exact offered field names and
nullability. For example, an unconstrained front readiness branch requires
`response_details.writing_task_scope` to be `null`. Missing a branch-required
scope receives the bounded structured-result repair and then the runtime's visible
no-action fallback if repair fails.

A `proposal_drafting_request` asks Lead to draft a concrete document-change or
specialist-consultation proposal for later confirmation. A `document_change_request`
tracks the user's requested durable document change. It remains pending through
questions, drafts, consultation, and review; exact successful Apply completes it.
These concepts are explained separately in the field guide.

Ordinary draft/review tasks use `specialist-assignment-response-v3`. Constraint
assignments use `specialist-assignment-response-v4`, expose `assess_constraints`, and
return `constraints_assessed`, `concerns_found`, `conflicts_found`, or
`insufficient_evidence` in their closed offered schema. Draft assignments return
`draft_completed` or `insufficient_evidence`; review assignments return
`supports_proposal`, `concerns_found`, `conflicts_found`, or `insufficient_evidence`.
Ordinary synthesis uses `consultation-summary-v4`. Artifact review uses
`artifact-review-summary-v4`, with `review_outcome` set to
`ready_for_apply_confirmation`, `revision_required`, or `rejected`. A positive
review permits presentation of a separate Apply decision and does not write files.

All roles receive shared bound instructions, per-call input guidance, and response
schema descriptions. Agents must follow the current schema's presence rules and
select offered identities exactly. There are no alternate-name decoders or wire
migration steps. Python API and persisted-history names remain separate interfaces.

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

A Lead result can require a selection call before its final response. Selection is
a submitted provider call and a nonterminal context action; it creates no evidence
or confirmation authority. Ordinary front answers and clarification questions, or
a current singleton result contract, can return without that extra call. Other
results need room for selection and the result within the same allocation. Ordinary
SEARCH or READ invalidates the prior selection; a corrective call retains its
rejected contract and repair limit. Insufficient room produces typed budget
exhaustion before launch; it does not increase the allocation or discard known
specialist replies when synthesis fails.

<a id="reviewed-now-versus-apply-and-commit-later"></a>

## Exact context capacity

Lead context is limited to 262,144 UTF-8 bytes of the complete serialized provider
payload. Equality is allowed. This is a byte limit on the current JSON payload,
including its envelope and JSON escaping; it is separate from token/cost budgets,
the separately delivered output schema, and a provider's model context window.

A capacity stop reports its stage, actual bytes, maximum and excess when that
stage has an exact measurement. An early editorial-allocation stop can occur
before a complete provider payload exists; its unavailable counts are labelled
rather than invented. The diagnostic contains measurements, not conversation or
story text. The offending provider submission does not run. Already accepted
user/control records and existing failure audit remain authoritative; overflow
creates no approval, successful task progress or permission to replay work.

Inspect the conversation and task state before deciding what to do next. Bounded
editorial memory improves decision lookup and restoration, but does not bound the
required-history delivery to the provider. Archiving, changing conversations or restarting
can retain that history, so these actions alone do not guarantee a smaller payload.
The current runtime does not silently omit, summarize or delete history to fit.
Selective delivery requires a separate policy with preserved originals, required
history, provenance, explicit omissions and consistent restart/provider behavior.

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

`StoryRuntime` is constructed from the initialized `main` control workspace,
but every artifact read, reference, proposal check, Apply, application-ledger
lookup, and recovery operation freshly binds the exact READY session worktree.
The Lead current-state payload reads canonical core snapshots from that
binding, while session runtime overrides remain authoritative control-plane
state read from the same control workspace used by directive and override
writers. A stale override copy in the session worktree is ignored.
The interactive picker uses the exact attached session as well. There is no
fallback to the control root, current directory, or another session and no
automatic provisioning. A binding failure occurs before provider or artifact
I/O; if resources are lost after Apply confirmation, runtime durably reports
`recovery_required` / `operator_recovery` without attempting the write.

Commit has separate authority and independently revalidates the same session
worktree and branch. Providers receive only bounded context/reference access;
they never receive repository, write, or Git authority.

Only a matching successful Apply satisfies the authoring objective. Before
that result, rejection, revision, staleness, or failure leaves it open. A
terminal Commit result preserves prior objective satisfaction and cannot
authorize the next cycle. Pending Apply and Commit requests are redisclosed
exactly, and each new cycle requires fresh decisions. No local Commit request
authorizes remote push or merge. A successful Apply result alone does not
create Commit authority: the runtime must publish its matching Commit request
in the same durable batch. If request preparation is proven absent, use the
typed reprepare action; if publication is uncertain, inspect the owner before
retrying. The installed shell offers Prepare or Later for a typed reprepare
request, then asks for a separate Commit, Reject, or Revise decision on the
fresh exact Commit request.

See the [runtime API](../reference/runtime-api.md),
[collaboration-history reference](../reference/collaboration-history.md),
[agent-context design](../design/agent-context.md), and
[artifact-reference guide](artifact-references.md).
