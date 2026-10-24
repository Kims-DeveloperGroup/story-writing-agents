# Collaborate with the Lead Agent

The Lead Orchestrator is the normal front channel for story work. You tell the
lead what you want to understand, change, or discuss. The lead reads the
evidence it needs, discloses the affected specialist domains, explains the
next action, and speaks to you again after specialist work completes.

Runtime code is the authority boundary. It captures references, performs
SEARCH and READ, validates provider results, calls specialists, persists the
conversation, applies confirmed artifact changes, and creates a separately
confirmed local commit. The lead proposes and triggers those steps; it does not
perform them itself.

This guide describes the user-visible workflow and the internal/provider
boundary. Examples use placeholders and small excerpts so they do not create
story content for a real workspace.

## The responsibilities of each participant

| Participant | Owns | Does not own |
| --- | --- | --- |
| You | The request, clarification, and each explicit decision | Provider calls or implicit approval |
| Lead | User conversation, scope interpretation, routing, draft integration, synthesis, and next-action guidance | Filesystem, provider tools, specialist calls, artifact writes, or Git |
| Specialist | Drafting, revision, or review in its assigned domain over its assigned inputs | Sibling context, lead conversation, routing, writes, or Git |
| Runtime | Context loading, validation, persistence, budgets, calls, writes, and commit execution | Choosing story meaning on the lead's behalf |

The lead works from the four core documents and current manuscript evidence. It
checks whether your request is consistent, calls out missing or conflicting
information, and can recommend changing your request or the documents so they
stay synchronized.

The fixed specialist roster is:

| Specialist | Focus | Typical source |
| --- | --- | --- |
| Plot & Structure Architect | Outlines, causality, chronology, pacing, progression, and plot structure | `global_outline.md`, `part_outlines.md` |
| Character & World Custodian | Motivation, behavior, relationships, world facts, and continuity | `character_sheets.md` |
| Tone & Execution Specialist | Narrative rules, prose, POV, tone, formatting, and manuscript execution | `narrative_rules.md`, manuscripts |

Routing covers semantic domains and affected canonical paths, even when a
target is empty or missing. The Lead discloses its interpretation and explicitly
selects the current task/revision when continuing work across conversations.
That task retains its affected scope and drafting obligations. Unrelated
inventory/history does not add work, and status facts do not grant reads.
Archived conversations remain readable context without active task authority.

Specialists are isolated. A Plot assignment cannot use a Character reply as if
it were its own evidence, and a specialist never becomes a second user-facing
lead.

### Supply foundations and creative room

Give Lead the story facts or selected material to use, the direction of the requested work, protected conditions, and the choices it may invent. A detailed USER brief can support initial creation while the target document is empty. Lead asks for a specific missing foundation or material choice when the supplied information cannot support official drafting.

Read evidence records which source and revision were actually supplied. The semantic assessment explains how that material supports the particular task and its permitted creative choices. It keeps established facts, interpretations, creative proposals and unknowns distinct. Specialist calls do not establish a missing foundation, and already supplied facts do not need to be entered again.

The same admission applies to Lead, direct specialist, adopted material, consensus AUTHORING and Python API paths. Referencing a rejected public answer preserves its exact input and original provenance; later use has fresh task qualification and the usual separate review, Apply and Commit decisions.


### Session worktree boundary

The initialized `main` workspace is the runtime's control root: configuration,
conversation history, and diagnostics stay there. Story artifacts do not.
Before every artifact-sensitive operation, runtime freshly verifies the exact
branch, internal ref, and sibling worktree for the selected session and binds
the operation to that worktree's physical `(st_dev, st_ino)` identity. Each
outer repository or session-source transaction holds a short synchronous
runtime guard and checks both the physical root and exact Git READY state once
before and once after its body. Nested repository operations reuse that
transaction with liveness and named-root checks instead of repeating the full
READY scan. Provider waits and concurrent specialist work run without that
guard; the next filesystem transaction reacquires it and rejects persistent
drift. Repository access exists only inside that guard and expires on exit. The
exit check also runs when the action raises or is cancelled; a binding loss
found there is reported instead of treating the earlier result as authoritative.
The picker follows the currently attached session, guards its
path list and selected-file read separately, and never lends repository access
to the range viewer. Switching or concurrent sessions never reuse another
session's artifact source, active-debate or own-agent state, or application
ledger. Conversation history keeps its separate control-root/configured-
storage ownership. A worktree-path replacement observed before or during a
guarded action is rejected rather than adopted.

Direct Lead and Lead-synthesis work additionally retain an opaque physical-
generation pin from before session-derived context and provider work through
durable result publication. Its six private descriptors keep the exact control,
Git-administration, worktree, and indirection-file generation from being
recycled; they are not exposed for traversal or artifact access. The pin is
rechecked and closed after persistence. It may span the provider wait, but a
Git lease never does, and the pin is not proof of semantic READY.

There is no fallback to `main`, the shell's current directory, or a previous
session, and a Lead call does not provision missing resources. If the exact
session binding is absent or unsafe, runtime reports a content-free failure
before any provider or artifact I/O. Create or reopen the session resources,
or follow operator recovery when the identity has drifted. Session-state reads
also reject unsafe relative paths, symlinks, FIFOs/devices, hard-linked or
changing leaves, and embedded ownership that names another session, debate, or
agent. The Git lease coordinates this runtime's cooperating operations; it is
not continuous protection against arbitrary external Git that mutates and
restores state between checks. Stop workspace processes before external
recovery work.

The continuity pin itself grants no provider process authority. For each Codex
or Antigravity story call, the same session binding separately mints an opaque
call-local capability that selects the retained session-root descriptor as OS
working directory under the fail-closed Darwin boundary. Codex also uses native
project root `--cd .`; Antigravity uses the exact CWD as its project root. See the
[Provider session execution boundary](../reference/backend-and-sessions.md#codex-session-execution-boundary).
Neither provider gains direct manuscript access; story evidence remains
runtime-mediated.

## The normal request flow

```text
you -> lead turn
     -> runtime inventories bounded source status and attached references
     -> lead answers workflow/status questions or clarifies directly
        or recommends one preparation -> you Confirm, Reject, or Revise
             -> lead prepares a separately confirmable proposal or asks a question
        or proposes specialist drafting/review in every affected domain
        or integrates required drafts into an exact artifact candidate
     -> you Confirm, Reject, or Revise the exact request
     -> runtime calls the disclosed specialists with isolated inputs
     -> lead synthesizes every reply and preserves disagreement
     -> after drafting, you ask the Lead to prepare the final candidate
     -> you confirm review; all required specialists review that exact candidate
     -> you may separately confirm Apply, then separately confirm Commit
```

The runtime never treats a conversational “yes” as confirmation. A
confirmation is a structured decision bound to the exact request, revision,
and digest shown to you.

### Decide a recommendation

A recommendation shows what the Lead wants to prepare, why, the exact targets
or specialists, selected sources, expected result, impact, and call budget.
Use its Confirm/Reject/Revise control:

| Decision | What follows |
| --- | --- |
| Confirm | One bounded Lead preparation. An artifact candidate then needs review confirmation; a consultation proposal needs execution confirmation. A material missing choice may produce a question. |
| Reject | No preparation call. An existing authoring objective stays open. |
| Revise | Supply nonempty instructions or a question. The exact pending recommendation closes; Lead can discuss it or offer a fresh proposal requiring its own Confirm. |
| Cancel or EOF | No decision is submitted and no preparation starts. |

This illustrative flow starts with a recommendation that includes a visible
draft; it is not a recorded provider run:

```text
Lead: <outline draft>; I recommend preparing its replacement candidate.
Runtime: shows purpose, core_documents/global_outline.md, sources, impact, cost.
You: Confirm preparation
Lead: <exact candidate and required reviewers>
You: Confirm review
Lead synthesis: decision=propose
You: Apply
Runtime: writes the exact candidate; the authoring objective is satisfied.
You: Commit, if requested
Runtime: creates the separately disclosed local commit.
```

Confirm does not launch an automatic sequence or ask you to confirm the same
preparation again. Review, Apply, and Commit each have their own decision.
An ordinary message such as “Yes, use that” is also meaningful: it is a new
instruction interpreted in context and supersedes a pending recommendation.
It is not the recommendation's structured Confirm. A new proposal still
requires its own review or execution decision.

### Discuss, hold, resume, discard, or start again

A Revise request can lead to an explanation or a question about the task, another topic, or how the participants work. Discussion keeps the authoring task separate: it does not turn that task into workflow guidance or require specialist work for every question.

Use `/task` in the focused conversation to choose an action, a task, and optional whole input material. The shell offers readable labels, including the source conversation for material from elsewhere in the session. Choose `No task association` for a discussion without a writing task. Enter a discussion question or fresh writing intent when prompted. Cancel or EOF submits no choice and starts no provider work.

The task panel and choices show full task IDs, revisions, and lifecycle status.
The panel separately shows the workflow step, its state, and the next safe action.
A prospective Resume or New task target is labeled pending until a fresh proposal
is admitted. The panel refreshes after the choice and its confirmation or
execution complete. Selection uses returned references; users do not construct
task IDs or revisions.

Choose the next action explicitly:

| Choice | Result |
| --- | --- |
| Discuss | Ask a question without authorizing execution. |
| Hold | Keep the task and its material while retiring a safely pending unconfirmed control. |
| Resume | State the next intent for the selected task. Review its fresh next-revision proposal and Confirm before specialists run. |
| Discard | End a safely idle task and its associated unconfirmed controls; retain its history and material. |
| New task | State a new intent and choose optional source material. A new task starts at revision 1 with its own proposed scope and confirmations. |

Select a whole retained draft, discussion answer, or final public response when you want to use it as material. The runtime verifies its source and digest within the session. Selection supplies input; it does not reuse an old proposal, specialist reply, READ receipt, final review, Apply, or Commit authority. Completed or discarded source tasks keep their status.

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

Pending Apply/Commit controls and unresolved operations retain their priority. Task choices cannot bypass them or reset spent calls. Reading the current choices does not start provider work or write artifacts.

See [explicit task API choices](../reference/runtime-api.md#explicit-task-choices-and-retained-messages) for the supported inspection and decision factories.

### Follow a draft through to an artifact

The lead interprets ordinary follow-ups in context; there are no required
command words. For example, this is an illustrative exchange, not a recorded
provider run:

```text
You: Draft the global outline for the direction we agreed on.
Lead: <Plot drafting proposal with the current task and revision>
You: structured Confirm for drafting
Plot: <substantive outline draft>
Lead: <integrated draft and a focused question about preparing it for review>
You: Yes, use that.
Lead: <exact replacement candidate for core_documents/global_outline.md>
Runtime: discloses the candidate and required reviewers; no file changes.
You: structured Confirm for review
Lead synthesis: decision=propose; the reviewed candidate is ready.
You: separate structured Apply
Runtime: applies the exact candidate; the authoring objective is satisfied.
You: separate structured Commit, if a local commit is requested
Runtime: creates only the disclosed local session-branch commit.
```

If the target or intended change is unclear, the lead asks a focused question.
It proposes consultation for required specialist drafting or review. Once the
request, evidence, and all required drafts for the current task/revision are
sufficient, it prepares the candidate. A fresh finished candidate supplied by
the user may enter review directly when the Lead discloses that interpretation;
selecting an earlier Lead message does not waive current drafting obligations.
If an existing core document is empty, a freshly revalidated empty-status receipt can support a
replacement; a nonempty target requires an attached reference or READ evidence.
Empty status proves only that the file has no content.

Runtime keeps the authoring objective in the conversation where it began.
Other conversations in the same session can see it through retained session
context, but continuation and cancellation belong to the originating
conversation. Side discussion, a review, a rejected step, a stale candidate,
or a failed attempt leaves the objective open. Only an exact matching Apply
result of `applied` or `unchanged` satisfies it. A cancellation must explicitly
close that objective; neither prose completion nor a null objective intent
does so. Commit remains a separate decision after authoring completion.

The visible drafting context is retained as whole messages with exact byte
digests. The stored source remains unchanged; the displayed runtime notice
about incomplete authoring is separate. The prepared candidate can omit
conversational framing and must not include that runtime notice. A draft
reference does not authorize a file read, review, or write.
Pending Apply and Commit controls are shown again unchanged when you send
ordinary input, without a new provider call or history append. Resolve that
control first. Known terminal outcomes allow a new cycle with fresh requests;
unknown or recovery outcomes remain blocked.

A draft from an earlier revision of the same task can be selected as input to a
fresh drafting or revision consultation. Its original message, digest, source
revision, and owning conversation remain visible. The new consultation needs its
own confirmation, owner drafts, and review of the exact final candidate. Earlier
drafts, reviews, or confirmations do not complete the new revision or authorize
Apply or Commit. Preparing a completed draft for artifact review still requires
the exact completed source for the current revision.

### Compose the next request while confirmed work runs

The interactive controller uses the live-composer boundary. On a capable
terminal, it can keep one ordinary editor open while
already-confirmed runtime work continues. Progress redraws preserve the exact
buffer and cursor, and the same terminal driver remains the only writer to the
terminal. Typed text is literal: for example, submitting `/status` through
this composer delivers that exact text; it does not execute the shell command
parser.

What happens when you submit depends on the authoritative runtime boundary,
not on what the progress animation appears to show:

| Runtime boundary when input is accepted | What the terminal does | Result for the next lead step |
| --- | --- | --- |
| `precommit` | Requests cancellation and waits for the exact running operation to join. | A proved join returns `replan` with the literal text. The lead can plan again from authoritative state. An uncertain or failed join returns `recovery_required` and does not deliver the text for replay. |
| `postcommit` | Does not cancel or roll back the operation; it waits for finalization. | Success returns `next_turn` with the literal text. Failure or cancellation during finalization returns `recovery_required`. |
| Operation completes before input is accepted | Closes the live span with the operation result. | `operation_completed` preserves the current unfinished buffer and cursor for the caller's next normal read. |

The interactive controller presents this interaction:

```text
Lead: Your confirmed continuity review is running.
story> Please also compare /status wording with ${core_documents/narrative_rules.md}
       [review progress redraws here; text and cursor remain editable]
```

If Enter is accepted before the durable commit boundary and cancellation joins,
the component returns:

```text
kind: replan
text: "Please also compare /status wording with ${core_documents/narrative_rules.md}"
```

If the durable boundary has already been crossed, it instead waits for that
operation and returns the same text as `next_turn`; it never rolls the completed
effect back. Ctrl-C returns `interrupted`, EOF returns `eof`, and Enter returns
submission, so those inputs cannot be confused with one another.

Ordinary focused shell input now uses this lead path. Terminal completion is
only presentation evidence: durable runtime state remains the authority for
whether work committed, needs recovery, or may start another lead turn.

## Exploration, adoption and ownership

The shell and public runtime use the same explicit, one-turn exploration choice.
See [the selection interface](lead-agent.md#exploration-and-adoption).

| User action or boundary | Owner and retained state | Required next authority |
| --- | --- | --- |
| Explore in conversation A | A owns a provisional report; the next ordinary turn uses normal mode. | No specialist, draft, objective or write authority. |
| Adopt whole or ranged material in B | B captures exact USER input from A and starts a fresh task at revision 1. | Explicit Authoring or Review only intent, exact targets and new Confirm. |
| Review only | B retains the exact supplied selection and actual review report. | No automatic rewrite or Apply. |
| Switch conversations | Each task keeps its owner, revision and consumed calls. | Resolve pending Apply/Commit or recovery in its named owner before new exploration/adoption. |
| Change conditions | The earlier candidate and confirmation remain historical. | Current inputs, fresh affected duties and complete review of the new final candidate. |
| Restart | Stored captures retain exact source report and selected-text pins. | Revalidate source and current control; no stale Confirm/Apply, replay or counter refund. |
| Record a direction | Editorial memory records the chosen direction. | An actual matching Apply result is still required to record an artifact effect. |

Rejected provider responses remain selectable answer content through public
response references, even when their structure or values are invalid. That
input-only path is separate from typed exploration idea adoption and grants no
READ, draft, review or approval authority.

## References and context

An attached reference is an indicator of what the agents should read. It is not
the file's entire content in the request record.

Use a whole-file indicator such as:

```text
${core_documents/global_outline.md}
```

Use a ranged indicator when only specific lines are relevant:

```text
${manuscript/part-01/opening.md}#L20-L32
```

In focused Lead input, `$` opens the canonical-artifact picker. `$$` opens that
same picker and then the independent text viewer, where Shift+Up/Down selects a
range. Closing the viewer restores the composer and inserts the selected
`${path}#Lstart-Lend`. Multiple references can be attached to one Lead request.
The durable reference records path and optional from/to lines;
runtime reads current bytes and gives the authorized agent only the required
content.

### Two layers of context

The lead's cooperation with specialists is the outer workflow. SEARCH, READ,
and ANSWER are inner actions of one provider invocation.

1. Runtime gives the lead identity, assignment, conversation input, reference
   identities, content-free core-document status, and required session memory:
   retained active/archived history plus current directives. Fresh threads replay
   full history; compatible threads receive missing turn content and new
   host/specialist facts. Relevant core content arrives through host-owned
   SEARCH/READ or explicit references. This also applies to workflow/meta turns
   and Lead synthesis.
2. The lead may ask runtime for a bounded SEARCH. A search result is only a
   locator.
3. The lead asks runtime to READ a selected locator. READ produces the content
   receipt that can support a claim.
4. The lead returns ANSWER with the minimum ordered evidence IDs it used.
5. If it proposes cooperation, runtime constructs assignments from that
   result. It then gives each specialist only its assignment and mandatory
   reference reads.
6. After all successful specialist replies, runtime gives the lead the typed
   replies and attribution IDs for synthesis.

The outer route is not a provider action, and SEARCH/READ are not a way for a
specialist to contact another specialist. A provider reply is attribution, not
story evidence until the lead uses it in a valid synthesis.
The Lead shares compatible continuation across the session's conversations;
specialists retain isolated scopes. Session memory supplies no current receipt
or confirmation authority. Archive status does not prevent its provider
disclosure. See [Lead session memory](agent-context.md#lead-session-memory) for
exact delivery, capacity, and recovery limits.

### Provider-shaped examples

See the [agent JSON field guide](../reference/provider-wire.md) for current payloads,
schema variants, and examples. Both providers use `agent-provider-wire-v10`.

Context actions use `agent_action.action_type`: `search_context`, `read_context`,
or `submit_result`. Results live in `task_result` and every built-in result has
`response_type`. Lead uses `lead-response-v7`; specialist assignments use
`specialist-assignment-response-v3` for draft/review or v4 for constraints; synthesis uses `consultation-summary-v4` or
`artifact-review-summary-v4`.

The request names `task_input`, `session_history`, `response_contract`, and
`contract_guidance`. Guidance explains the fields and allowed values agents actually
receive. `proposal_drafting_request` asks for a proposal to confirm later;
`document_change_request` tracks the user's requested document change through Apply.
Public Python examples elsewhere in this guide retain their API names.

## Confirming a proposed step

When extra work is needed, the lead response displays its purpose, specialists,
inputs, evidence, and a confirmation request. Inspect that exact proposal, then
submit a structured decision equivalent to:

```json
{
  "confirmation_request_id": "<request-id>",
  "session_id": "novel",
  "conversation_id": "<conversation-id>",
  "step_id": "<step-id>",
  "proposal_id": "<proposal-id>",
  "proposal_revision": 1,
  "proposal_digest": "<digest>",
  "kind": "confirm"
}
```

The three decisions have distinct effects:

| Decision | Effect |
| --- | --- |
| `confirm` | Starts exactly the disclosed consultation or artifact-review step. |
| `reject` | Records that the step will not run; no specialist or artifact call occurs. |
| `revise` | Requires a visible revision request, supersedes the old proposal, and asks for a fresh proposal. |

Free-form input such as `Yes`, `go ahead`, or `confirm this` is contextual Lead
conversation. If a consultation proposal is pending, a new ordinary turn
supersedes it before asking the lead for a new outcome. It can express approval
of a draft and lead to a candidate, but it does not start review or apply a
change. Pending Apply/Commit controls are redisclosed unchanged instead.

## Scenario 1: direct answer

```text
story[novel]> What does Reject do?
[Lead]
  Outcome: answer
  Lead: Reject records the decision and starts no specialist, artifact, or Git work.
  Next action: continue_with_lead
```

Provider wording varies. The stable result is a direct typed answer with no
proposal or decision picker; no specialist is selected.

## Scenario 2: one specialist consultation

```text
story[novel]> Is this scene too early? ${core_documents/part_outlines.md}
[Lead]
  Outcome: proposal
  Lead: A Plot specialist should check the scene timing.
  Next action: submit_confirmation
  State: awaiting_confirmation
Lead proposal decision
  Confirm — Authorize only the exact disclosed proposal.
  Reject  — Reject the proposal without running it.
  Revise  — Record a revision request without confirming it.

User selects: Confirm
[Lead]
  Outcome: recommendation
  Lead: The Plot review places the reveal one beat after the required trigger.
  Next action: continue_with_lead
  State: completed
```

Runtime re-reads the mandatory reference, calls only `plot_architect`, stores
its typed reply, and asks the lead to synthesize. Provider wording varies. The
specialist never becomes the front channel.

## Scenario 3: cross-domain disagreement and a second round

```text
You: Would revealing the secret here harm both pacing and Mara's credibility?
    ${core_documents/part_outlines.md}
    ${core_documents/character_sheets.md}

Lead proposal: Consult Plot and Character in parallel for timing and
              motivation. Confirm review?

You: Confirm the exact proposal.

Plot: conflict — the reveal is scheduled after the turning point.
Character: concern — Mara has not yet learned the fact needed for this choice.

Lead synthesis: Both objections remain visible. The prose can be drafted later,
                but the event and motivation are not currently consistent.
                next_intent=continue_lineage for a focused question.

You: Confirm the displayed round-two proposal.

Runtime: runs only the new focused assignments. Round two is terminal; no third
         specialist round is created.
```

A `completed` round records its result, not completion of the authoring task.
Drafting returns material; review requires concrete nonempty candidate inputs
and criteria. A brief or empty status alone cannot serve as review material.

The lead never takes a majority vote. Every material concern is retained in
the synthesis and its unresolved input IDs.

## Scenario 4: incomplete or conflicting core documents

If a core document is absent, empty, incomplete, or inconsistent, the lead
first describes the evidence state and proposes the smallest useful next step.

```text
You: The narrative rules are empty. Add a close-third-person rule.

Lead: The current narrative-rules source is empty. I propose Execution drafting
      from your brief. No file has changed. Confirm the draft assignment?

You: Confirm drafting.
Execution: drafted — returns the proposed close-third rule.
Lead synthesis: presents the draft material.
You: Prepare the final candidate from these drafts.
Lead: I integrated the draft into an exact replacement candidate. Confirm a
      separate Execution review of that immutable candidate?
You: Confirm review.

Execution: supports — the candidate is a clear rule for the requested POV.

Lead: Review is complete. The candidate is ready for a separate Apply decision.
      User impact: the core document is still unchanged.
```

The lead may instead ask a clarifying question, recommend completing the
document, or surface a conflict before proposing a change. Runtime does not
turn an explicit missing reference into a missing-file judgment: an invalid
`${missing/path.md}` reference fails before the substantive lead call.

For two conflicting nonempty sources, the lead cites both, routes all affected
domains, and proposes a synchronized candidate only when the evidence supports
one. It may reject your requested change when it would make the core documents
less consistent.

## Shared premises and contribution roles

For an unresolved motivation, the Lead can first propose a separate analyst consultation.
Its terminal report presents provisional whole options, the recommendation, each actual
reply's disposition and residual cost. Choose, reject, or defer the exact report through
the shell's `/premise` action. A choice supplies a captured source for a fresh authoring
proposal; it does not confirm that proposal or authorize Apply/Commit. Important choices
remain yours. A delegated minor choice must use your exact report-bound option allowlist.

Analysis and authoring retain separate limits and actual costs. Failed front attempts remain
visible; a fresh proposal cannot reset earlier spend. A draft that declares a conflict with
its captured premise is held, while your direction change starts a fresh explicit revision.
See [the complete premise state table](lead-agent.md#choosing-an-unresolved-premise) for
clarification, rejection, deferral, restart, budget exhaustion, and recovery behavior.


For dialogue polishing, the Lead can assign Execution to draft the prose and
Character to assess characterization constraints. Character need not rewrite
the dialogue. Both still review the same final candidate before any Apply.

If a motive must be chosen first, the proposal shows the exact selected motive,
its source revision and conversation, the dependent draft owner, and each
specialist's duty before Confirm. A missing choice needs clarification. After
confirmation, the host supplies that same capture to the dependent author and
synthesis. Changing the motive or its source requires a new task revision and
fresh owner drafts. Capturing a premise records the choice and provenance; it
does not certify the choice as a source fact.

A completed constraint assessment supplies evidence and concerns. It does not
count as a draft, support for a final review, completed authoring request, or
permission to write. All affected specialists remain required final reviewers.
The preparation recommendation and final specialist review have separate Confirm
decisions, and inherit the drafting lineage's round count and physical spend.
Final preparation for these confirmed drafts stays in their source conversation.
If you select the task elsewhere, focus that conversation to prepare its final
candidate; sharing source facts does not transfer execution authority.
Required preparation, READ, review, and synthesis calls must fit the existing
limits; the Lead must clarify an infeasible plan.

When a confirmed draft has no target path, the preparation recommendation can
select an exact destination without changing that draft's captured scope. Its
artifact domains must already belong to the captured specialist union. Confirm
binds the destination separately, and its READ, preparation, and review calls
must fit the inherited budget.

For a completed local draft, Lead can explicitly propose the same task's next
revision. That proposal needs fresh Confirm and new substantive owner drafts;
the completed revision's drafts cannot authorize its candidate.
If the old continuation cannot fit its remaining budget, Lead offers only the
fresh revision for drafting or revision work. The old revision and its spent
calls remain intact. Pending Apply or Commit decisions must still be resolved
through their existing controls before another authoring cycle.

When a completed draft is offered for continuation, the next Lead turn reserves
enough calls for preparation and every final reviewer. New alternatives in that
same turn share the remaining allowance. Continue with a fresh proposal when
those calls cannot fit.

## Scenario 5: editing a manuscript, then Apply and Commit

```text
You: Rewrite lines 20–32 so Mara reveals the secret.
    ${manuscript/part-01/opening.md}#L20-L32
    ${core_documents/global_outline.md}
    ${core_documents/character_sheets.md}
    ${core_documents/narrative_rules.md}

Lead: I propose Plot, Character, and Execution draft/revision assignments for
      the event, motivation, and prose. Confirm drafting?
You: Confirm the exact drafting proposal.
Specialists: drafted — return material to the Lead without writing files.
Lead synthesis: presents the draft material.
You: Prepare the final candidate from these drafts.
Lead: I integrated all required drafts into one exact range-patch candidate.
      Plot, Character, and Execution must review that final candidate.
You: Confirm the separate exact candidate review.

Runtime: gives every reviewer the same immutable candidate and the disclosed
         evidence. It does not write the manuscript.

Lead synthesis: Execution supports the prose form; Plot conflicts with the
                scheduled turning point; Character raises a motivation concern.
                decision=reject. No file changed.
```

If the synthesis instead selects `decision=propose`, the result is a reviewed
candidate, not an applied file. The next sequence is always separate:

```text
Lead: Reviewed candidate is ready. Apply it?
You: structured Apply confirmation
Runtime: revalidates the candidate and current artifact state, writes the exact
         disclosed paths atomically in this session's worktree, and reports
         the application result.

Lead: The artifact change applied successfully. Create the local story commit?
You: structured Commit confirmation
Runtime: stages only the disclosed changed paths and commits the local session
         branch after independently revalidating the same READY worktree and
         the physical identity used by Apply. It does not push, merge, or
         mutate a remote.
```

Reject or Revise at Apply leaves the reviewed candidate unapplied. A stale
candidate is not applied; the lead must prepare a new one. Commit confirmation
is not implied by Apply confirmation. If exact binding or physical-root
validation fails after Apply confirmation but before the repository's
`apply_artifact_changes()` method starts, runtime does not start artifact
application and durably returns `recovery_required` with
`operator_recovery`. After that method has started, an unclassified root,
security, or format failure is instead
`outcome_unknown` with `inspect_and_resolve_outcome`; inspect the exact
application before any new write.

The guarded Apply transaction validates complete READY state on entry, derives
the current complete artifact state and relationship result, then performs one
explicit complete READY check immediately before marking repository execution
started and calling `apply_artifact_changes()`. Its outer guard still performs
the complete postvalidation before the result can escape.

Later Commit inspection and recovery remain bound to the same physical root
used by Apply. If that root was replaced, they return operator recovery without
advancing the recorded Commit operation from the replacement's Git state.

The follow-on Commit request is prepared after a successful Apply. If request
preparation or publication fails, verified result-only Apply history projects
`reprepare_commit_request` across restart. Repreparation issues a fresh request
that needs its own structured decision; uncertain publication readback still
directs you to inspect the session.

## Scenario 6: ordinary discussion

```text
You: What makes a scene feel tense?
Lead: I propose an Execution assignment for the prose/tension recommendation.

You: Is the current scene tense enough under our rules?
Lead: I need concrete manuscript text and the narrative-rule evidence, then
      I can propose an Execution review with explicit criteria.

You: Would slowing this scene help the plot and the character arc?
Lead: I propose Plot and Character; an affected manuscript also requires Execution.
```

The lead clarifies material choices before routing. Workflow/status answers
can remain direct. Substantive story recommendations and critiques require
specialists even when no file edit is requested.

## Scenario 7: policy composition and isolation

Each bound agent receives immutable policy components in this exact order:

```text
lead_orchestrator
  = common + lead + runtime Lead contract overlay + runtime routing policy v1

plot_architect
  = common + specialist_collaboration + plot + runtime routing policy v1

character_custodian
  = common + specialist_collaboration + character + runtime routing policy v1

execution_specialist
  = common + specialist_collaboration + execution + runtime routing policy v1
```

The common component defines behavior true for every role: untrusted input,
runtime assignment, authorized context actions, privacy, and strict output.
The specialist-family component defines shared assignment and isolation rules.
The lead and each specialist add their own behavior and domain lens. The
runtime appends its routing policy to the captured prompt; customized workspace
files are preserved. Routing policy v1 is separate from Lead result v3 and
specialist/synthesis result v2.

Fresh Lead policies use `lead-response-v7`. The runtime overlay also applies
during Lead synthesis while preserving that invocation's separate minimal
result schema.

The runtime captures strict UTF-8, NUL-free snapshots before binding and
fingerprints every ordered file component and the runtime policy bytes.
A changed common component invalidates all role bindings; a changed specialist-family component invalidates the three
specialists; a changed lead or domain component invalidates only that role. A
provider cannot turn a prompt sentence into filesystem, confirmation, artifact,
or Git authority.

For example, the visible and internal exchange remains role-specific:

```text
You: Check the outline's pacing.
Lead input: route the Plot domain, with the supplied concrete outline reference.
Runtime input: common + specialist_collaboration + plot snapshots + runtime
               routing policy, then one isolated review assignment.
Plot output: agent_action.task_result has response_type=specialist_assignment_result, assignment_status=concerns_found, and response_text.
Lead output: The timing concern is ...; next action: ...
Runtime effect: the reply is stored and shown through the lead; no sibling
                policy or authority is added.
```

<a id="scenario-8-a-submitted-lead-result-is-unavailable"></a>

## Scenario 8: a Lead attempt times out or has no usable result

A read-only Lead request may stop before submission, time out, fail at the
provider boundary, or return unusable output. When runtime can durably close
that attempt, it preserves the original request and failure and exposes
`retry_lead_turn`. For example, a response timeout produces:

```text
story[alpha]> Review ${core_documents/global_outline.md} before answering.
[Lead]
  Outcome: recommendation
  Lead: The Lead provider attempt exceeded its response timeout.
        Your original request and failure remain in history.
        Send a new message or resubmit your request in this conversation.
  Next action: retry_lead_turn
story[alpha]>
```

The default response timeout is 900 seconds per provider call. A turn can make
several calls. Antigravity has a further 30-second outer watchdog grace;
Codex's sole watchdog stays at 900 seconds. Use the existing
`story chat --codex-timeout SECONDS` option to override either provider's call
limit.

Send a new message, revise the wording, or explicitly resubmit the request in
that same conversation. After quitting, the same recovery is available when
you reopen the session and focus the conversation:

```text
$ story chat alpha --workspace /path/to/story
story[alpha]> Review ${core_documents/global_outline.md} and focus on pacing.
```

This input starts a fresh turn with fresh reference and call identities.
The same retry is available after references were read when runtime can close
the exact failed attempt and its reference accounting. The authoring objective
remains open; the new turn reacquires current evidence.
Reopening, reading history, and projecting status do not submit provider work.
For an exact legacy unstarted, prepared-only, or unmatched submitted read-only
Lead attempt, the projection exposes retry without changing history. Explicit
new input first closes that prior attempt under the session and conversation
leases, then appends the new turn. Both requests and the prior failure remain
in history.

A failed Revise attempt leaves its old proposal superseded; retry creates no
replacement confirmation authority by itself. Only the matching unfinished
native operation can be retired, so a successful checkpoint from another
conversation remains intact. Failed closure writes, corrupt history, uncertain
specialist/synthesis work, and pending or uncertain Apply/Commit outcomes
retain their gates. Uncertain reference cleanup also requires recovery.
Successful specialists or full turns are not
automatically replayed. Existing bounded malformed-response correction and
proven pre-execution continuation replacement keep their separate constraints.

### Authentication during an active Lead operation

The native provider can require authentication after the shell's startup gate
has already succeeded. Story pauses new provider calls, lets active sibling
calls finish, and returns the terminal to `codex login` or bare `agy`. Complete
the provider-owned interaction and exit its interface normally if it stays open.
Story does not read or retain the native login terminal bytes.

A successful sign-in does not by itself authorize the submitted call to run
again. Story makes one automatic replacement only for Antigravity's exact
zero-turn authentication-timeout rejection when no independent timeout fact
exists. An actual timeout still permits sign-in assistance but forbids replay
and provider-thread reuse. That replacement keeps the request,
schema, correlation, expected provider thread, execution profile, and captured
evidence unchanged, but uses a new physical call ID and exchange. Both attempts
count against the operation's physical-call budget. Any Codex 401, ambiguous
Antigravity 401, started call, or source/configuration drift remains
non-replayable.

Recommendation Confirm and Revise keep the same pending operation through
permitted authentication assistance. Signing in neither repeats the structured
decision nor resets its physical-call reservation.

In a consultation, completed specialist siblings stay preserved while the
failed assignment is handled. If recovery cannot safely resume, runtime stores
the known replies and terminal failure state. Reopening the session never
replays that provider call; follow the displayed `failed` or `outcome_unknown`
next action. Python and other noninteractive callers receive the typed
authentication error without a terminal handoff. Authentication does not
change the selected task/revision, waive required drafts or candidate reviews,
or authorize Apply or Commit. An unfinished authoring objective remains open.

### Recommendation recovery

A stale request requires fresh planning and confirmation. Repeated or competing
decisions cannot start a second preparation. If restart finds a confirmed
preparation that never started provider work, runtime can close it as failed.
Another attempt needs fresh planning, a new request, and Confirm. A known result
can be finalized locally without another provider call. Unknown submitted outcomes,
failed closure writes, or uncertain reference cleanup require resolution first.
History and reopening do not replay preparation. See
[recommendation recovery](../operations/recovery-and-limitations.md#recommendation-preparation-recovery).

## Scenario 9: interruption, restart, and recovery

The visible state tells you what happened and what is safe next. Runtime stores
the state before provider work where needed and never assumes an uncertain call
succeeded.

```text
You: Confirm the consultation.
Runtime: durable running state is recorded, then the process is interrupted.

You later inspect:
State: interrupted or outcome_unknown
Next action: continue with the lead, or inspect and resolve the outcome.

Runtime: preserves known specialist replies and does not replay an uncertain
         submitted provider call.
```

If confirmation survived but its one-use start authority did not, the state is
`reconfirmation_required`; confirm the fresh exact request. If authoritative
history is corrupt or ambiguous, the state is `recovery_required`; repair
history before normal collaboration. If artifact evidence changed before
review or Apply, the candidate becomes `stale` and no reviewer or write starts.

A deadlocked debate is resumable by its existing step and lineage. The lead can
propose a focused continuation that carries the original inputs and reply
events; you do not need to copy a deadlocked candidate into a new conversation.

## What the durable exchange looks like

The terminal renders each validated collaboration event once in its durable
sequence. A proposal is not summarized into opaque prose; the user sees the
identity and limits that the confirmation control will bind:

```text
--- Collaboration · message ---------------------------------------------------
  Sequence: 7
  From: lead:lead_orchestrator
  To: user
  Step: step_scene_review
  Proposal: proposal_scene_review
  Revision: 1
  Digest: sha256:...
  Purpose: Check the scene against plot and character evidence.
  User impact: Read-only specialist advice; no files or Git state change.
  Side effects: provider_invocation
  Assignments
  - assignment_plot · plot_architect
    Round: 1
    Inputs: outline
    Maximum provider calls: 3
    Compare the scene decision with the global outline.
  Required sources
  - outline
    Reference: ${core_documents/global_outline.md}#L20-L44
    State: present
  Bounded cost
    Round: 1/2
    Maximum additional calls: 7
    Physical call limit: 12
  [confirmation] Choose the exact Confirm, Reject, or Revise control.
```

After review, artifact panels show paths, operation kinds, base/projected
digests, and bounded hunk/byte counts without printing replacement text. The
Apply request says that it changes artifacts but does not stage or commit. The
later Commit request names the branch, paths, proposed message, effective
impact, and prohibited remote effects. A local commit result explicitly says
that a paired remote push has not been authorized.

Compact and details-enabled modes contain the same semantic proposal,
assignment, confirmation, result, failure, and recovery facts. Details add only
durable event/batch identity. Plain and ANSI terminals use the same component
tree, and a restart cursor prevents already rendered sequence numbers from
appearing twice.

## State meanings and next actions

### Cooperation state

| State | What you see it means | Safe next action |
| --- | --- | --- |
| `awaiting_confirmation` | Exact consultation is disclosed but not started. | Confirm, Reject, Revise, or send a new lead turn. |
| `confirmed` | Your decision is durable; start is normally immediate. | Wait for the returned result; after restart, reconfirm if requested. |
| `reconfirmation_required` | The request remains exact, but start authority was lost. | Confirm the fresh request or Reject/Revise it. |
| `running` | Dispatches are durable and provider work may be active. | Wait for the owner or inspect durable progress. |
| `completed` | All required result facts and the visible lead result are stored. | Continue with the lead. |
| `rejected` | The requested step did not run. | Continue with the lead. |
| `superseded` | A newer request replaced this revision. | Follow the newer request. |
| `interrupted` | Cancellation stopped work and known results were preserved. | Continue with the lead; do not infer missing success. |
| `failed` | A known assignment or synthesis failure was stored. | Use preserved facts and ask the lead how to continue. |
| `outcome_unknown` | A submitted call may have completed, but no result proves it. | Inspect and resolve; never replay automatically. |
| `recovery_required` | Authoritative history cannot safely continue. | Repair history first. |

### Artifact and effect state

| State | User meaning | Safe next action |
| --- | --- | --- |
| `awaiting_consultation_confirmation` | Exact candidate and reviewers are disclosed. | Confirm, Reject, or Revise review. |
| `reviewing` | Reviewers are assessing the same immutable candidate. | Wait or inspect recovery. |
| `reviewed` | Lead accepted the review as a proposal; no file changed. | Request/confirm separate Apply. |
| `revision_required` | Review asks for a new candidate. | Ask the lead to regenerate. |
| `rejected` | Candidate or effect was declined; no corresponding effect occurred. | Continue with the lead. |
| `stale` | Source or candidate changed before the effect. | Re-read and prepare a new candidate. |
| `applied` | Runtime wrote the exact disclosed artifact change. | Review the result; decide separately about Commit. |
| `unchanged` | Apply found no net change to make. | Continue with the lead or prepare a new candidate. |
| `committed` | Runtime created the disclosed local story commit. | Continue with the lead. |
| `outcome_unknown` | The effect boundary is uncertain. | Inspect and resolve before another effect. |
| `recovery_required` | Durable effect history needs repair. | Operator recovery is required. |

The displayed `next_action` is guidance, not an additional authorization.

### Editorial scope inspection

Editorial inspection shows selected direction and its remaining coverage alongside
explicit conflicts, source uncertainty, and historical application attempts. A new
selection can coexist with unchanged manuscript bytes. Failed or uncertain Apply stays
visible; a historical successful receipt does not establish current manuscript content
or semantic fulfillment. Source observations require qualified current evidence.

Use the existing typed editorial USER controls to name exact scope and counterpart
receipts for coexistence, conflict, or replacement. Partial replacement preserves the
original residual scope. Unmapped scenes and stale range anchors need clarification;
geometry alone does not decide semantic conflict. See
[editorial decisions](lead-agent.md#editorial-decisions) for the scope and relation flow.

## Budgets and boundaries

See [authoring call limits and measured support](lead-authoring-call-support.md) for
current reservation rules, the observed existing-manuscript refusal, and a
verified fresh-revision/new-output example. Actual submitted calls and static
reservations are reported separately.


Runtime builds a feasible allocation before confirmation. One consultation
uses one to three specialist assignments; siblings receive disjoint context
and call reservations. A lineage permits at most two specialist rounds and the
configured physical-call limit. Round two is focused and terminal.

SEARCH, READ, and ANSWER continuations count as provider calls when submitted.
Failed, timed-out, and cancelled submitted calls also consume their reserved
spend. The runtime reports progress and preserves completed results; it does
not restart a broad run after one failure.

The lead may recommend a narrower follow-up when evidence or budget is
insufficient. Specialists cannot broaden their own assignment, source scope,
or budget. A full-workspace read, arbitrary path, unbounded search, provider
subagent, or hidden authority is never implied by a prompt.

Lead can spend one provider call selecting the result contract needed for its
reply, followed by a call that returns that result. Both calls count against the
disclosed budget. Original owners can offer workflow answers, clarifications,
draft conclusions, recommendations bound to a qualified completed draft, and
prepared proposals after exact confirmation directly. A result with one current
eligible member is also direct. SEARCH or READ can require a fresh selection.
The runtime reserves room within the existing five-call invocation limit and
reports exhaustion when it cannot fit. Failed synthesis preserves completed
specialist replies and keeps synthesis in a failed state.

### When exact context exceeds capacity

The current complete provider-context JSON has a 262,144 UTF-8-byte limit,
including envelope and escaping. A capacity notice identifies the measurement
stage and shows actual/maximum/excess bytes when available, without quoting story
or conversation content. The rejected submission does not invoke the provider;
existing history and failure records remain available for inspection.

Editorial-memory bounds are separate from the complete required-history payload.
Archive, a conversation change or restart can keep that history in delivery and
does not promise more capacity. Inspect the retained history and current task
before choosing the next request. Selective history delivery is a future policy
boundary, not an automatic summary, deletion or replay permission.

## A practical checklist

When collaborating with the lead:

1. State the desired story or workflow outcome.
2. Attach only the relevant `${path}` or ranged references.
3. Read the lead's evidence status, specialist purpose, candidate, and user
   impact before deciding.
4. Use structured Confirm, Reject, or Revise for the exact displayed request.
5. Treat a reviewed candidate as unchanged until Apply is separately confirmed.
6. Treat an applied candidate as uncommitted until Commit is separately
   confirmed.
7. Confirm the currently attached session and its ready worktree before
   artifact-sensitive work; never use `main` as a story-work fallback.
8. Follow the displayed state-specific next action after interruption or
   failure.

## Issue #583 integration evidence

This table distinguishes implemented behavior from the focused evidence
currently present at the #586 base. A documentation statement does not replace
a missing end-to-end check.

| #583 criterion | Current evidence | Status at this base |
| --- | --- | --- |
| Literal focused input reaches `RuntimeSession.lead_turn()` without a selector | `ActionKind.LEAD_INPUT`; controller dispatch; `test_ordinary_input_is_literal_lead_input_and_removed_commands_stay_unknown`; `test_lead_input_requires_attachment_and_conversation_then_routes_exactly` | Supported |
| Typed direct outcomes, proposals, evidence, impact, next action, and state render | `project_lead_turn_result`; collaboration-view focused tests | Supported for typed projection |
| Multiple whole/ranged references remain mandatory | `test_focused_lead_references_survive_quit_reopen_and_continue`; live `$`/`$$` driver and reference-activation tests; runtime forced-read tests | Supported |
| Confirm/Reject/Revise is structured; free text never confirms | `InteractiveLeadAdapter`; `test_literal_lead_turn_and_consultation_decisions_are_exact` | Supported |
| Specialist synthesis, artifact review, separate Apply, separate local Commit | Adapter's three distinct decision paths; `test_artifact_and_commit_controls_are_distinct_and_correlated` | Supported at controller/API seam |
| Progress, interruption, and restart-safe recovery | `test_failure_interruption_and_recovery_turns_never_replay_authority` plus existing progress contracts | Supported |
| Management commands remain provider-free | Parser/controller retain their command dispatch; removed-command zero-call test | Supported for focused parser/controller cases |
| Valid filesystem conversation state survives workspace reopen | `test_canonical_interactive_runtime_files_survive_reopen`; malformed paths fail closed | Supported |
| Fresh workspace completes a Lead turn, quits, reopens, and continues | `test_focused_lead_references_survive_quit_reopen_and_continue` preserves focused canonical state and executes both turns | Supported with deterministic provider fixtures |
| Installed terminal layer covers direct response, proposal decisions, references, provider failure, restart, and real-PTY ranged selection/restoration | #585 controller tests, #590 lifecycle tests, live reference-driver tests, and real-PTY Shift-range viewer tests | Supported with bounded deterministic fixtures |
| Module graph/index exact | Integrated `PYTHON_MODULE_INDEX.md` from #584 | Root/modularity evidence owns final certification |

These repository tests exercise the installed terminal/controller boundaries
with deterministic provider fixtures. They do not prove external provider
authentication or service availability; those environment facts are not a
product-behavior acceptance blocker.

For the exact Python signatures and contract fields, see the
[runtime API](../reference/runtime-api.md), [agent contracts](../reference/agent-contracts.md),
[canonical writing artifacts](../reference/writing-artifacts.md), and
[agent-context design](../design/agent-context.md).
