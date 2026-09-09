# Collaborate with the Lead Agent

The Lead Orchestrator is the normal front channel for story work. You tell the
lead what you want to understand, change, or discuss. The lead reads the
evidence it needs, decides whether specialist help adds value, explains the
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
| Lead | User conversation, evidence-based critique, routing, synthesis, and next-action recommendations | Filesystem, provider tools, specialist calls, artifact writes, or Git |
| Specialist | One assigned domain assessment over its assigned inputs | Sibling context, lead conversation, routing, writes, or Git |
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

Specialists are isolated. A Plot assignment cannot use a Character reply as if
it were its own evidence, and a specialist never becomes a second user-facing
lead.

## The normal request flow

```text
you -> lead turn
     -> runtime inventories bounded source status and attached references
     -> lead answers directly
        or proposes specialist consultation
        or proposes an exact artifact candidate
     -> you Confirm, Reject, or Revise the exact request
     -> runtime calls the disclosed specialists with isolated inputs
     -> lead synthesizes every reply and preserves disagreement
     -> you may separately confirm Apply, then separately confirm Commit
```

The runtime never treats a conversational “yes” as confirmation. A
confirmation is a structured decision bound to the exact request, revision,
and digest shown to you.

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
   retained active/archived history plus current directives and complete core
   documents. This also applies to workflow/meta turns and Lead synthesis.
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

For a story question, this excerpt shows identity, input aliases, core statuses,
and the answer contract. The separate required session-memory payload is
omitted from the example:

```json
{
  "workflow": "lead_front_channel",
  "identity": {"agent_id": "lead_orchestrator", "session_id": "novel"},
  "inputs": [
    {"input_id": "user_input", "kind": "user_input"},
    {"input_id": "reference_1", "kind": "core_document", "locator": "${core_documents/global_outline.md}"}
  ],
  "answer_contract": {
    "scope": "story_specific | workflow_meta",
    "evidence_status": "not_applicable | verified | missing | empty | incomplete | conflicting",
    "evidence_ids": "ordered runtime-issued IDs only",
    "kind": "answer | question | critique | recommendation | proposal"
  }
}
```

The lead can request a bounded search:

```json
{"action":"SEARCH","queries":[["opening reveal",["writing_artifact"]]]}
```

It then reads a returned opaque handle:

```json
{"action":"READ","handles":["<runtime-handle>"]}
```

Its final answer cites the resulting receipt, not the handle alone:

```json
{
  "scope": "story_specific",
  "evidence_status": "verified",
  "evidence_ids": ["reference_1", "read_1"],
  "kind": "critique",
  "message": "The requested sequence is earlier than the outline permits.",
  "proposal": null,
  "artifact_intent": null
}
```

A specialist receives its own assignment and returns only its typed assessment:

```json
{
  "assessment": "concern",
  "assignment_id": "as_plot_1",
  "input_ids": ["user_input", "reference_1"],
  "message": "The scene advances the reversal before its required trigger."
}
```

The lead's synthesis attributes every reply:

```json
{
  "scope": "story_specific",
  "evidence_status": "conflicting",
  "evidence_ids": ["reference_1", "read_1"],
  "message": "The motivation works, but the outline timing remains unresolved.",
  "next_intent": null,
  "reply_event_ids": ["reply_plot_1", "reply_character_1"],
  "unresolved_input_ids": ["reference_1", "read_1"]
}
```

The runtime rejects invented, stale, duplicated, reordered, or unused IDs.
It also rejects a SEARCH hit that was never READ when the lead cites its
content.

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

Free-form input such as `Yes`, `go ahead`, or `confirm this` is ordinary lead
conversation. If a proposal is pending, a new ordinary turn supersedes that
proposal before asking the lead for a new outcome. It does not start work.

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

The lead never takes a majority vote. Every material concern is retained in
the synthesis and its unresolved input IDs.

## Scenario 4: incomplete or conflicting core documents

If a core document is absent, empty, incomplete, or inconsistent, the lead
first describes the evidence state and proposes the smallest useful next step.

```text
You: The narrative rules are empty. Add a close-third-person rule.

Lead: The current narrative-rules source is empty. I can prepare a candidate
      that replaces that core document, then ask Execution to review it.
      No file has changed. Confirm review?

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

## Scenario 5: editing a manuscript, then Apply and Commit

```text
You: Rewrite lines 20–32 so Mara reveals the secret.
    ${manuscript/part-01/opening.md}#L20-L32
    ${core_documents/global_outline.md}
    ${core_documents/character_sheets.md}
    ${core_documents/narrative_rules.md}

Lead: I prepared one exact range-patch candidate. Plot, Character, and
      Execution are required. Confirm review?

You: Confirm review.

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
         disclosed paths atomically, and reports the application result.

Lead: The artifact change applied successfully. Create the local story commit?
You: structured Commit confirmation
Runtime: stages only the disclosed changed paths and commits the local session
         branch. It does not push, merge, or mutate a remote.
```

Reject or Revise at Apply leaves the reviewed candidate unapplied. A stale
candidate is not applied; the lead must prepare a new one. Commit confirmation
is not implied by Apply confirmation.

## Scenario 6: ordinary discussion

```text
You: What makes a scene feel tense?
Lead: I can give a general writing opinion; no story specialist is needed.

You: Is the current scene tense enough under our rules?
Lead: I need the relevant manuscript and narrative-rule evidence. I will either
      answer from those sources or propose an Execution consultation.

You: Would slowing this scene help the plot and the character arc?
Lead: I propose Plot and Character because both domains are affected.
```

The lead clarifies a material choice before routing. If no specialist has a
useful domain lens, it answers directly and does not manufacture a consultation.

## Scenario 7: policy composition and isolation

Each bound agent receives immutable policy components in this exact order:

```text
lead_orchestrator
  = common + lead

plot_architect
  = common + specialist_collaboration + plot

character_custodian
  = common + specialist_collaboration + character

execution_specialist
  = common + specialist_collaboration + execution
```

The common component defines behavior true for every role: untrusted input,
runtime assignment, authorized context actions, privacy, and strict output.
The specialist-family component defines shared assignment and isolation rules.
The lead and each specialist add only their own behavior and domain lens.

The runtime captures strict UTF-8, NUL-free snapshots before binding and
fingerprints every ordered component. A changed common component invalidates
all role bindings; a changed specialist-family component invalidates the three
specialists; a changed lead or domain component invalidates only that role. A
provider cannot turn a prompt sentence into filesystem, confirmation, artifact,
or Git authority.

For example, the visible and internal exchange remains role-specific:

```text
You: Check the scene's pacing.
Lead input: route only the Plot domain, with the supplied outline reference.
Runtime input: common + specialist_collaboration + plot snapshots, then one
                isolated plot assignment.
Plot output: {"assessment":"concern", "assignment_id":"as_plot_1", ...}
Lead output: The timing concern is ...; next action: ...
Runtime effect: the reply is stored and shown through the lead; no sibling
                policy or authority is added.
```

## Scenario 8: a submitted Lead result is unavailable

Suppose a referenced request passes its reference read, but the substantive
Lead call is submitted and its response never becomes an authoritative
result. The first shell does not expose provider stderr or report an ordinary
retryable failure. It shows the typed uncertainty instead:

```text
story[alpha]> Review ${core_documents/global_outline.md} before answering.
[Lead]
  Outcome: recommendation
  Lead: The Lead provider call was submitted, but no reliable result was
        recorded. Do not retry or replay it; authoritative state is preserved.
        Inspect and resolve the outcome, or create and focus another
        conversation for intentionally distinct work.
  Next action: inspect_and_resolve_outcome
  State: outcome_unknown
  next action: External work may have happened. Do not replay it; inspect and recover.
story[alpha]>
```

After quitting, reopen the same session and conversation:

```text
$ story chat alpha --workspace /path/to/story
story[alpha]> Do not replay the submitted call.
[Lead]
  Outcome: recommendation
  Lead: The Lead provider call was submitted, but no reliable result was
        recorded. Do not retry or replay it; authoritative state is preserved.
        Inspect and resolve the outcome, or create and focus another
        conversation for intentionally distinct work.
  Next action: inspect_and_resolve_outcome
  State: outcome_unknown
  next action: External work may have happened. Do not replay it; inspect and recover.
story[alpha]> /conversation new Distinct recovery work
Subject: Distinct recovery work
```

The ordinary line after restart asks the runtime to continue, but the runtime
returns the preserved result without a new provider call and does not append
that line as a new durable user event. Creating and focusing a distinct
conversation remains available for intentionally new work.

Internally, the collaboration-history projection is the sole classifier. An
exact proposal-free Lead call with `PREPARED` followed by unmatched
`SUBMITTED` projects as `outcome_unknown` with
`inspect_and_resolve_outcome`. A `PREPARED`-only call is different: it proves
no submission, is not promoted to this state, and must follow its own returned
recovery action. Session recovery accepts only the exact unmatched-submission
shape as owner `collaboration_history` with failure
`owner_outcome_unknown`; downstream proposal, review, Apply, or Commit facts
make the shape invalid. Transition assessment permits only entry into that
session for inspection (`ENTER_DESTINATION`). It still blocks leaving or
pausing that owner, and other `outcome_unknown` owners remain blocked from
entry too.

Entry is inspection access, not permission to replay or resolve the uncertain
call. It grants no provider call, Apply, artifact write, Commit, or other
effect authority.

### Authentication during an active Lead operation

The native provider can require authentication after the shell's startup gate
has already succeeded. Story pauses new provider calls, lets active sibling
calls finish, and returns the terminal to `codex login` or bare `agy`. Complete
the provider-owned interaction and exit its interface normally if it stays open.
Story does not read or retain the native login terminal bytes.

A successful sign-in does not by itself authorize the submitted call to run
again. Story makes one automatic replacement only for Antigravity's exact
zero-turn authentication-timeout rejection. That replacement keeps the request,
schema, correlation, expected provider thread, execution profile, and captured
evidence unchanged, but uses a new physical call ID and exchange. Both attempts
count against the operation's physical-call budget. Any Codex 401, ambiguous
Antigravity 401, started call, or source/configuration drift remains
non-replayable.

In a consultation, completed specialist siblings stay preserved while the
failed assignment is handled. If recovery cannot safely resume, runtime stores
the known replies and terminal failure state. Reopening the session never
replays that provider call; follow the displayed `failed` or `outcome_unknown`
next action. Python and other noninteractive callers receive the typed
authentication error without a terminal handoff.

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

## Budgets and boundaries

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
7. Follow the displayed state-specific next action after interruption or
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
