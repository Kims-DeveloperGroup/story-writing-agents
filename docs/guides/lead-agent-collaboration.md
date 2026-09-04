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

The terminal package now has a live-composer boundary for a later controller
integration. On a capable terminal, it can keep one ordinary editor open while
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

For example, a later controller can present this interaction:

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

This leaf does **not** make ordinary shell story input available. The current
controller still rejects story instructions before a provider call, and it
does not yet route confirmed lead work through the live composer. Later
controller and presentation work must map these closed outcomes into lead
replanning and semantic output. Terminal completion is only presentation
evidence: durable runtime state remains the authority for whether work
committed, needs recovery, or may start another lead turn.

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

In a capable text-input component, `$` opens the canonical writing-artifact
picker and `$${file_name}` opens the independent text viewer for a selected
file. The viewer remains a separate component while you move through lines;
selecting a range and closing it returns to the composing prompt. Multiple
references can be attached to one lead request. The durable reference records
the path and optional from/to lines; runtime reads current bytes and gives the
authorized agent only the required content.

### Two layers of context

The lead's cooperation with specialists is the outer workflow. SEARCH, READ,
and ANSWER are inner actions of one provider invocation.

1. Runtime gives the lead identity, assignment, conversation input, reference
   identities, and content-free core-document status.
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

### Provider-shaped examples

For a story question, runtime sends a compact lead request containing identity,
input aliases, core statuses, and the answer contract—not an unrestricted
workspace dump:

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
You: What does Reject do?

Lead: Reject records the decision and starts no specialist, artifact, or Git
      work. You can continue with the lead.

Runtime: calls only the lead; scope=workflow_meta, evidence_status=
         not_applicable, no proposal and no confirmation request.
```

No specialist is selected when the lead can answer the workflow question.

## Scenario 2: one specialist consultation

```text
You: Is this scene too early in the part outline?
    ${core_documents/part_outlines.md}

Lead: I need a Plot review of the scene's timing. Purpose: compare the scene
      with the current part outline. Confirm review?

You: structured kind=confirm for the displayed proposal

Runtime: re-reads the referenced outline, calls plot_architect with only its
         assignment, stores the typed reply, then calls the lead to synthesize.

Lead: The scene is one beat early because the required trigger is scheduled
      later. Next action: revise the scene or ask for a candidate.
```

The specialist does not contact you directly. The lead remains the front
channel and explains the actionable result.

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

## Scenario 8: interruption, restart, and recovery

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

For the exact Python signatures and contract fields, see the
[runtime API](../reference/runtime-api.md), [agent contracts](../reference/agent-contracts.md),
[canonical writing artifacts](../reference/writing-artifacts.md), and
[agent-context design](../design/agent-context.md).
