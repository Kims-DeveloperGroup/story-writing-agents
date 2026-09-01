# Lead agent: conversation and specialist consultation

The Lead Orchestrator is the normal user-facing story agent in the Python
runtime. Users discuss story work with the lead; they do not need to choose an
agent for every turn. The lead can answer directly, ask a focused question,
critique or recommend, or propose a bounded consultation with one to three
specialists. An exact user confirmation starts the disclosed consultation.

The lead makes the semantic choice: whether a turn is about the story or only
the workflow, what evidence matters, whether specialist help is useful, which
specialist domains apply, and what the combined feedback means for the user.
Every story-specific judgment must name current runtime-issued evidence. The
runtime checks that evidence against a closed rule matrix before showing or
persisting the judgment. Runtime code does not interpret prose or decide that
the lead chose the right story facts; it is the authority and execution
boundary that inventories sources, performs authorized SEARCH/READ operations,
validates results, constructs proposals, records confirmation, calls providers,
persists results, and recovers interrupted work.

This distinction will also govern later story changes: the lead will propose
and trigger a write or Git commit only after the user confirms that exact
operation, while runtime code will perform the filesystem or Git operation.
The current implementation is read-only. It can recommend a change, but it
cannot apply an artifact change, stage files, or create a commit.

The installed `story` shell has not connected this workflow. Applications use
the async Python methods described below.

## Lead responsibilities and limits

The lead is expected to:

- use core documents as the story's governing evidence;
- compare a request, suggestion, or proposed content with referenced core
  documents and manuscripts;
- surface missing, empty, incomplete, or conflicting evidence only when the
  current invocation has the required runtime receipt;
- recommend how user input, core documents, and manuscripts can be brought
  back into sync;
- answer directly when available evidence is sufficient;
- select only the specialist domains that add value;
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
| Converse with the lead through `StoryRuntime.lead_turn()` or `RuntimeSession.lead_turn()`. | Send ordinary story text through the installed shell. |
| Attach several ordered whole-file or ranged `${path}` references and inspect the result's content-free evidence. | Treat an explicit missing `${path}` as proof of absence; it fails before the substantive lead call. |
| Receive an evidence-grounded answer, question, critique, recommendation, or consultation proposal. | Assume runtime performed semantic conflict detection or proved that every relevant artifact was selected. |
| Confirm, reject, or revise the exact current proposal. | Confirm with free text such as `Yes`, or replay stale confirmation fields. |
| Run one to three specialists in parallel after confirmation and receive a typed lead synthesis. | Apply a proposed core-document or manuscript edit. |
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
  lead returns scope + evidence status + ordered evidence IDs
  runtime validates the closed evidence matrix and current receipts
  lead answers directly
    OR runtime discloses a proposal -> awaiting_confirmation

invalid/stale/invented evidence
  -> lead_outcome_failure -> retry_lead_turn
  rejected provider call is closed and is never replayed

user -> decide_lead_step(exact Confirm)
  runtime records confirmed
  runtime atomically records running + every ordered dispatch
  runtime calls 1-3 specialists concurrently with isolated evidence
  if all succeed: runtime calls the lead for synthesis
  runtime atomically records replies/outcomes/result
  runtime locally finalizes -> completed / failed / interrupted
    OR proposes same-step revision+1 -> awaiting_confirmation
```

`confirmed` is normally a transient durable boundary inside
`decide_lead_step()`: the method continues into execution and returns the final
authoritative `turn`. If the durable start append fails, no specialist call is
made. If the process restarts after confirmation but before start authority can
be used, the proposal becomes `reconfirmation_required` instead of being
silently executed.

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
| `answer` | Evidence supports a direct answer. | `continue_with_lead` |
| `question` | One material user choice is missing. | `reply_to_lead` |
| `critique` | The lead can evaluate the direction without added work. | `continue_with_lead` |
| `recommendation` | The lead can recommend the next action, or is reporting a workflow/recovery state. | Usually `continue_with_lead`; otherwise state-specific. |
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
Provider work: lead only; no story reference/SEARCH/READ; no specialist
```

For a story-specific provider judgment, `result.evidence` identifies its
declared `scope`, `status`, and ordered content-free receipts. A workflow/meta
judgment also has an evidence value, but it is exactly
`workflow_meta`/`not_applicable` with no receipts. Runtime-generated recovery
notices instead have `evidence=None`.

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
interrupted, uncertain, recovery, or round-two-proposal turn—not a separate
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
    revision_request="Limit the consultation to plot causality.",
)
replacement = (await runtime.decide_lead_step(revision)).turn
```

Free text is never confirmation. Calling `lead_turn(..., "Yes")` while a
proposal is pending atomically supersedes that proposal before asking the lead
for a new outcome.

## How specialist routing works

| Specialist | Domain |
| --- | --- |
| Plot & Structure Architect (`plot_architect`) | Global/part outlines, plot structure, causality, flow, and pacing |
| Character & World Custodian (`character_custodian`) | Character sheets, relationships, world facts, motivation, and continuity |
| Tone & Execution Specialist (`execution_specialist`) | Narrative rules, manuscripts, prose, POV, tone, and writing execution |

Runtime enforces mandatory reviewers for explicitly referenced core documents:

| Core document | Required specialist |
| --- | --- |
| `core_documents/global_outline.md` | `plot_architect` |
| `core_documents/part_outlines.md` | `plot_architect` |
| `core_documents/character_sheets.md` | `character_custodian` |
| `core_documents/narrative_rules.md` | `execution_specialist` |

The lead chooses semantic routing for user input and manuscripts. Runtime does
not infer domains from prose, and a manuscript reference alone does not force a
reviewer. It rejects proposals with unknown or duplicate specialists,
noncanonical order, missing mandatory reviewers, unassigned inputs, or an
infeasible budget. It never adds undisclosed work.

Each specialist receives only its assignment's ordered inputs and fresh
mandatory-reference reads. A specialist cannot inventory, SEARCH, or READ a
sibling's artifact-only evidence. The lead synthesis receives all typed replies
and the directly referenced story-artifact inputs; one agent's previous read
receipt is never borrowed as another agent's evidence.

## How a lead judgment is grounded

The provider declares one semantic scope and one evidence status. Runtime does
not use NLP to relabel either value. Ambiguous story/workflow requests are a
prompt rule for the lead: classify them as story-specific and ask a grounded
question. Runtime then accepts only these combinations:

| Declared scope and status | Minimum current evidence | Allowed visible kinds |
| --- | --- | --- |
| `workflow_meta` / `not_applicable` | No receipts | `answer`, `question`, `critique`, `recommendation` |
| `story_specific` / `verified` | One nonempty attached reference or final-answer-used READ | All five kinds |
| `story_specific` / `missing` | An absent canonical-core status, or a complete non-truncated zero-hit SEARCH whose authorized scope includes writing artifacts | `question`, `recommendation`, `proposal` |
| `story_specific` / `empty` | A zero-byte canonical-core status or a zero-byte whole attached reference | `question`, `recommendation`, `proposal` |
| `story_specific` / `incomplete` | At least one current content or canonical-core status receipt | `question`, `critique`, `recommendation`, `proposal` |
| `story_specific` / `conflicting` | At least two distinct, nonempty current content receipts | `question`, `critique`, `recommendation`, `proposal` |

Every other combination fails closed. Evidence IDs are invocation-local and
must be unique and in runtime order: attached references, four core statuses,
then completed SEARCH/READ actions. Only a READ named in the final ANSWER's
`u` field may support content and become a proposal input. Core-status and
zero-hit SEARCH receipts are evidence only, never assignment inputs.

An exact explicit reference has stronger failure semantics than an exploratory
search. `${manuscript/part-01/missing.md}` raises
`ReferenceOperationFailure`; the provider cannot convert it into `missing`.
A whole reference to an existing zero-byte file may prove `empty`, while a
range into that file fails as out of bounds.

## Explicit story-writing scenarios

These scenarios define the lead behavior that the current read-only
consultation supports and the later write workflow must preserve.

Each scenario separates what the user sees from what runtime enforces. The
word “proposes” below means a consultation proposal only. It never means that
an artifact edit or commit has been authorized.

### 1. Incomplete or conflicting core evidence

```text
User input
The part outline names the antagonist, but the character sheet has no entry.
Check ${core_documents/part_outlines.md} and
${core_documents/character_sheets.md}. What should we fix?

Lead proposal
- plot_architect: assess the outline dependency
- character_custodian: assess missing character evidence
State: awaiting_confirmation
User action: Confirm

Specialist replies
- plot_architect / concern: the part outline depends on an undefined actor
- character_custodian / insufficient_evidence: motivation and continuity cannot
  be verified from the character sheet

Lead synthesis
The character evidence is incomplete. Define the antagonist in the character
sheet or revise the part outline; do not treat the current manuscript direction
as consistent yet.
Unresolved inputs: both referenced core documents
Final state: completed
```

Internally, both mandatory references are read before the substantive call;
the lead returns `story_specific`/`incomplete` or `conflicting` plus their
ordered `reference_*` IDs. Runtime validates the status, binds the same
evidence into the proposal, and revalidates both files after Confirm before it
calls either specialist. Externally, the user sees the gap, exact read-only
consultation impact, confirmation state, attributed replies, and synthesis.
The lead recommends a correction but does not change either file.

The other core-gap cases differ deliberately:

```text
Canonical core file absent -> core status receipt -> missing
Canonical core file exists at zero bytes -> core status/whole reference -> empty
Relevant canonical search has no hit -> complete zero-hit SEARCH -> missing
Explicit ${missing/path.md} -> ReferenceOperationFailure, not a lead judgment
```

### 2. Writing or revising core documents

```text
User input
Add a rule that every scene uses Mara's close third-person POV.
Check ${core_documents/character_sheets.md} and
${core_documents/narrative_rules.md} first.

Lead proposal
- character_custodian: verify whether the POV rule conflicts with character use
- execution_specialist: verify prose/POV consequences
User action: Confirm

Runtime calls
Both specialists run concurrently with their own input subsets. Only after both
succeed does the runtime call lead_orchestrator for synthesis.

Visible result
The lead recommends a narrower rule for Mara-led scenes and explains the
character/narrative-rule conflict. State: completed.
```

Internally, the lead selects the specialists, but runtime constructs the exact
assignments, route, participant profiles, budgets, impact, and proposal digest.
The consultation begins only after the exact structured Confirm. Externally,
the lead may reject the requested direction semantically or recommend revised
wording. It cannot yet construct or apply the actual document change. The
planned flow will require a new exact artifact-change proposal and confirmation;
it will not reuse the consultation confirmation.

### 3. Reviewing a manuscript edit against core documents

```text
User input
Rewrite ${manuscript/part-01/opening.md}#L20-L32 so Mara reveals the secret.
Verify it against ${core_documents/global_outline.md},
${core_documents/character_sheets.md}, and
${core_documents/narrative_rules.md}.

Lead proposal
- plot_architect: check plot timing
- character_custodian: check motivation/continuity
- execution_specialist: check POV and scene execution
User action: Confirm

Specialist replies
- plot_architect / conflict: the reveal is scheduled for a later turning point
- character_custodian / concern: current motivation does not justify disclosure
- execution_specialist / supports: the requested prose form can follow the POV rule

Lead synthesis
The prose technique is compatible, but the event timing and motivation are
not. Revise the request or first update the governing outline and character
evidence. The unresolved input IDs for every conflict/concern remain visible.
Final state: completed.
```

Internally, runtime revalidates the referenced outline, character sheet,
narrative rules, and manuscript range at confirmation, then gives each
specialist only its disclosed inputs. Externally, the user receives one lead
synthesis that preserves the plot and character objections rather than
averaging them away. No manuscript bytes are changed. A future artifact Apply
step must disclose the exact patch/effect and receive separate confirmation; a
later Git Commit step must receive another separate confirmation. The lead
triggers those semantic steps, while runtime code performs the write and Git
operations.

### 4. Ordinary discussion

The lead uses the smallest useful path:

```text
User: What does Reject do?
Lead: Direct workflow/meta answer. No story evidence, proposal, or specialist.

User: Is this beat structurally too early?
Lead: May propose one plot_architect consultation; after confirmation, returns
      that reply through a lead synthesis.

User: Would this reveal hurt both pacing and character credibility?
Lead: May propose plot_architect + character_custodian in parallel; after all
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

## Additional execution scenarios

### Disagreement is preserved

Every specialist reply has exactly:

```json
{
  "assessment": "concern",
  "assignment_id": "<assignment-id>",
  "input_ids": ["user_input", "reference_1"],
  "message": "The reveal conflicts with the current pacing constraint."
}
```

`assessment` is one of `supports`, `concern`, `conflict`, or
`insufficient_evidence`. Synthesis must cite every reply event in order and
must retain every input ID from non-supporting replies in
`unresolved_input_ids`. The lead cannot erase disagreement by voting.

### Partial failure

If one of several specialists fails, siblings are allowed to finish and their
typed replies are preserved. The runtime does not call the lead for semantic
synthesis, because the evidence set is incomplete. The user receives a
`failed` recommendation explaining that known sibling results were retained;
`next_action` is `continue_with_lead`.

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

### A focused second round

After all round-one assignments and synthesis succeed, the synthesis may
propose one same-lineage continuation. Runtime creates proposal revision 2 on
the same step, adds the round-one reply-event inputs, carries actual submitted
call spend, and returns `awaiting_confirmation`. Nothing in round two starts
until the user confirms the new request. Round-two synthesis cannot request a
third round.

```text
Round 1: concern remains -> completed result + revision 2 proposal
User: Confirm revision 2
Round 2: focused specialist reassessment -> final completed result
Maximum: 2 specialist rounds and 12 submitted physical calls
```

### Direct specialist advisory

An application can disclose a one-specialist proposal without first calling
the lead provider:

```python
proposal = await runtime.propose_specialist_advisory(
    "novel",
    conversation_id,
    "plot_architect",
    "Advise on the immediate pacing question.",
)
```

The session-bound form omits `session_id`. Proposal construction is
provider-free; exact confirmation runs one specialist, reserves zero synthesis
calls, returns the specialist's message directly, and cannot chain to another
round. Runtime rejects an advisory whose referenced core document requires a
different specialist.

## What providers receive

Applications do not call separate provider search/read/answer endpoints.
Runtime binds the selected agent and drives one bounded contextual invocation.
The samples below are sanitized from the orchestration acceptance fixtures;
generated correlations, IDs, handles, digests, and compact planning counters
vary.

### Lead turn: initial input, SEARCH, READ, and ANSWER

For this user input:

```text
Does the planned reveal conflict with the current opening?
```

the initial compact call includes required workflow material and four
content-free canonical-core statuses. Inventory does not send story bodies. A
meta turn with no explicit reference and no provider SEARCH/READ therefore
receives no new story content through this path and does not gain story
evidence merely because runtime inventoried the status facts.

```json
{
  "c": "<correlation-1>",
  "q": ["lead", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["lead", "outcome-v1"]],
  "i": {
    "workflow": "lead_front_channel",
    "command": "Does the planned reveal conflict with the current opening?",
    "identity": {"agent_id": "lead_orchestrator", "session_id": "novel", "conversation_id": "<conversation-id>", "turn_id": "<turn-id>", "step_id": "<step-id>"},
    "input_aliases": [{"input_id": "user_input", "kind": "user_input", "locator": "conversation/<turn-id>/user_input", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null}],
    "answer_contract": {"scope": "story_specific | workflow_meta", "evidence_status": "not_applicable | verified | missing | empty | incomplete | conflicting", "evidence_ids": "ordered runtime-issued evidence IDs only", "kind": ["answer", "question", "critique", "recommendation", "proposal"], "proposal": {"relation": "new_step", "purpose": "visible purpose", "assignments": "required only for proposal"}}
  },
  "core_statuses": [
    {"evidence_id": "status_1", "locator": "core_documents/global_outline.md", "status": "present", "utf8_bytes": 428, "source_id": "<source-id>", "manifest_digest": "sha256:<digest>", "revision": "sha256:<digest>", "source_digest": "sha256:<digest>"},
    {"evidence_id": "status_2", "locator": "core_documents/part_outlines.md", "status": "empty", "utf8_bytes": 0, "source_id": "<source-id>", "manifest_digest": "sha256:<digest>", "revision": "sha256:<digest>", "source_digest": "sha256:<empty-digest>"}
  ]
}
```

The sample abbreviates the remaining two status rows. A provider that needs
the manuscript can request a bounded lexical SEARCH:

```json
{"a":"s","c":"<correlation-1>","d":[["planned reveal opening",["w"]]],"u":[]}
```

Runtime returns a compact continuation observation. A hit includes source,
revision, boundary, provenance, snippet, and an opaque invocation-local
handle; it is not yet content evidence. The provider asks to READ that handle:

```json
{"a":"r","c":"<correlation-2>","d":["<handle>"],"u":[]}
```

The READ continuation contains the selected untrusted text and a new evidence
ID such as `read_2_1`. The provider's final ANSWER must name the handle in `u`
and cite the evidence ID in its strict semantic result:

```json
{
  "a": "a",
  "c": "<correlation-3>",
  "d": [{
    "scope": "story_specific",
    "evidence_status": "conflicting",
    "evidence_ids": ["read_2_1", "read_2_2"],
    "kind": "critique",
    "message": "The opening reveals the secret before the outline's turning point.",
    "proposal": null
  }],
  "u": ["<handle-1>", "<handle-2>"]
}
```

Runtime maps only those current used READs to durable content-free receipts,
checks that two distinct nonempty receipts can prove `conflicting`, persists a
typed lead judgment, and returns its evidence to the caller. It does not
independently decide that the two passages are semantically inconsistent.

A complete, non-truncated zero-hit SEARCH instead includes a content-free
`search_<action>` receipt in the observation. That receipt can support
`missing` only when the searched scope includes canonical writing artifacts.
A conversation-only zero hit, a truncated search, a hit that was never READ,
or a READ omitted from final `u` cannot ground the claim.

### Specialist assignment: initial input

The dynamic material includes the exact assignment, its input subset, and the
strict answer contract. Authorized reference bytes are added separately under
`untrusted_writing_artifact_context` after the mandatory-reference prelude.

```json
{
  "c": "<correlation-1>",
  "q": ["specialist_assignment", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["specialist_assignment", "outcome-v1"]],
  "i": {
    "workflow": "lead_specialist_consultation",
    "assignment": {
      "assignment_id": "<assignment-id>",
      "instruction": "Check the outline against the opening range.",
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

### Specialist SEARCH, READ, and ANSWER

The provider can request bounded lexical search:

```json
{"a":"s","c":"<correlation-1>","d":[["opening causality",["w","c"]]],"u":[]}
```

Runtime searches only this invocation's authorized sources and returns an
opaque handle in the continuation observation. The provider requests that
handle:

```json
{"a":"r","c":"<correlation-2>","d":["<handle>"],"u":[]}
```

After runtime recaptures and validates the source, the provider answers and
names every READ handle it used:

```json
{
  "a": "a",
  "c": "<correlation-3>",
  "d": [{
    "assessment": "concern",
    "assignment_id": "<assignment-id>",
    "input_ids": ["user_input", "reference_1", "reference_3"],
    "message": "The opening advances the reveal before its outline cause."
  }],
  "u": ["<handle>"]
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
  "q": ["lead_synthesis", "sa", "wcdo", [4, 2, 2, 4, 8, 8, 16, 200], ["lead_synthesis", "outcome-v1"]],
  "i": {
    "workflow": "lead_synthesis",
    "inputs": [
      {"input_id": "user_input", "kind": "user_input", "locator": "conversation/<turn-id>/user_input", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null, "text": "Check the opening."},
      {"input_id": "reference_1", "kind": "core_document", "locator": "${core_documents/global_outline.md}", "revision_digest": "sha256:<digest>", "start_line": null, "end_line": null},
      {"input_id": "read_2_1", "kind": "manuscript", "locator": "${manuscript/part-01/opening.md}#L20-L32", "revision_digest": "sha256:<digest>", "start_line": 20, "end_line": 32}
    ],
    "replies": [
      {"assessment": "concern", "assignment_id": "<assignment-1>", "input_ids": ["user_input", "reference_1", "read_2_1"], "message": "The reveal is too early.", "reply_event_id": "<reply-event-1>"},
      {"assessment": "supports", "assignment_id": "<assignment-2>", "input_ids": ["user_input"], "message": "The character motivation is plausible.", "reply_event_id": "<reply-event-2>"}
    ],
    "answer_contract": {
      "scope": "story_specific",
      "evidence_status": "conflicting",
      "evidence_ids": ["reference_1", "read_2_1"],
      "message": "nonempty visible synthesis",
      "reply_event_ids": ["<reply-event-1>", "<reply-event-2>"],
      "unresolved_input_ids": ["user_input", "reference_1", "read_2_1"],
      "next_intent": "null or an exact same-lineage proposal intent"
    }
  }
}
```

The terminal provider answer is exact JSON:

```json
{
  "a": "a",
  "c": "<synthesis-correlation>",
  "d": [{
    "scope": "story_specific",
    "evidence_status": "conflicting",
    "evidence_ids": ["reference_1", "read_2_1"],
    "message": "Motivation is plausible, but the reveal still conflicts with the outline timing.",
    "reply_event_ids": ["<reply-event-1>", "<reply-event-2>"],
    "unresolved_input_ids": ["user_input", "reference_1", "read_2_1"],
    "next_intent": null
  }],
  "u": []
}
```

Synthesis preserves the proposal's story-specific scope, evidence status, and
ordered evidence IDs unchanged; specialist reply IDs remain separate
attribution. Synthesis may also use bounded SEARCH/READ before ANSWER, but new
reads do not replace the proposal evidence lineage. It cannot dispatch another
agent, write an artifact, or authorize Git. A strict `next_intent` is only a
semantic request for runtime to construct a fresh round-two proposal.

## State meanings and next actions

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

Evidence rejection is not a proposal state. The returned turn has `state=None`,
`evidence=None`, kind `recommendation`, and next action `retry_lead_turn`. The
visible message says that runtime evidence validation failed. A typed
`lead_outcome_failure` event closes the exact submitted call, so retrying starts
a fresh turn and never replays the rejected result.

The history projection may expose `none`, `finalize_result`,
`start_or_reconfirm`, `inspect_and_resolve_outcome`, or
`repair_authoritative_history` as state-specific next actions. These describe
what is safe; they do not themselves authorize execution.

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

Restart behavior follows durable facts:

- `confirmed` without the private capability becomes
  `reconfirmation_required` with zero provider calls;
- `running` plus an authoritative result is finalized locally with zero
  provider calls;
- unresolved `running` without a live owner/result becomes `outcome_unknown`
  and is never replayed; and
- corrupt or ambiguous authority becomes `recovery_required`.

Outer cancellation cancels and joins all child tasks, persists every knowable
result, clears the live marker, and re-raises cancellation to the caller.

## Budget and accounting

One lineage permits at most two specialist rounds and twelve submitted physical
provider calls. Each invocation receives a disjoint reservation; siblings
cannot borrow each other's unused calls. A consultation reserves at least one
lead-synthesis call; a direct advisory reserves zero synthesis calls.

Submitted calls include:

- mandatory-reference prelude;
- initial provider call;
- SEARCH, READ, and final ANSWER continuations; and
- calls that were submitted but then failed, timed out, or were cancelled.

Every call first records `PREPARED`. Only `SUBMITTED` counts as spent. An
invocation is capped at five physical calls and may receive less when the
lineage has fewer calls left. Runtime constructs feasible allocations before
confirmation, carries actual spend into round two, and makes round three and
physical call thirteen unreachable.

## Current versus planned change authority

The implemented consultation ends with feedback, a recommendation, or a fresh
consultation proposal. It never constructs or applies an artifact patch and
never stages or commits Git.

The planned story-change workflow has two additional, independently confirmed
operations:

```text
consultation result
  -> lead proposes exact Artifact Apply
  -> user confirms Apply
  -> runtime writes the artifact and verifies the result
  -> lead proposes exact Git Commit
  -> user confirms Commit
  -> runtime stages and commits the disclosed files
```

The lead is the semantic trigger and user-facing coordinator at every step;
runtime code is the only executor. Consultation confirmation never doubles as
Apply or Commit confirmation.

The planned responsibilities are recorded separately:

- [#489 grounds lead judgments in canonical artifact evidence](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/489);
- [#490 constructs exact lead-reviewed artifact change proposals](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/490);
- [#491 applies a lead-triggered artifact change after exact confirmation](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/491); and
- [#492 triggers a separately confirmed commit after lead-directed edits](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/492).

See the [runtime API](../reference/runtime-api.md),
[collaboration-history reference](../reference/collaboration-history.md),
[agent-context design](../design/agent-context.md), and
[artifact-reference guide](artifact-references.md).
