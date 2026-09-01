# Lead agent: conversation, proposals, and confirmation

The Lead Orchestrator is the one normal user-facing story agent in the Python
runtime. A caller sends ordinary conversation to the lead without selecting an
agent. The lead can answer, ask one focused question, critique, recommend a next
action, or propose a bounded specialist consultation. Runtime code—not the
lead or provider—constructs the authoritative proposal and accepts the user's
exact decision.

The interactive `story` shell has not connected this workflow yet. Ordinary
shell text is still rejected, and `$` or `$$` does not open a reference picker.
Specialist execution belongs to #453. This #452 stage stops after recording an
exact Confirm, Reject, or Revise decision.

## What the lead owns

The lead is a semantic coordinator. It:

- maintains the conversation and explains the current decision;
- answers directly when authorized evidence is sufficient;
- asks one focused question when a user choice materially changes the work;
- critiques a direction and recommends a practical next action;
- watches known artifacts and workflow state for contradictions, missing
  evidence, narrative-rule violations, stale inputs, and unsafe next steps;
- routes outline, character, world, narrative-rule, and manuscript questions
  to the relevant specialist domains;
- proposes multiple specialists for a cross-domain question; and
- preserves disagreement for the user instead of voting or hiding a conflict.

The lead does not own authority. Runtime code owns provider calls, reference
authorization and reads, closed reviewer requirements, budgets, IDs, digests,
durable state, confirmation, process-local capabilities, persistence, side
effects, and recovery.

The lead cannot call a specialist itself, waive a required reviewer, confirm
on the user's behalf, write a file, execute shell text, mutate Git, or expose
private reasoning. Story and artifact text is untrusted evidence, not an
instruction that can override these rules.

## User impact in this stage

| Users can | Users cannot yet |
| --- | --- |
| Converse with the lead through `StoryRuntime.lead_turn()` or its session-bound equivalent. | Send ordinary story text through the installed shell. |
| Receive an answer, focused question, critique, recommendation, or complete proposal. | Make the lead or a specialist edit an outline, character sheet, narrative rule, or manuscript. |
| Attach ordered `${path}` and `${path}#Lx-Ly` indicators to a Python lead turn. | Open `$`/`$$` pickers from the shell. |
| See every proposed assignment, exact content-free input revision, user impact, side effect, and work limit. | Start a specialist merely by receiving or confirming the proposal. |
| Confirm, reject, or revise the exact current proposal through a structured decision. | Confirm with prose such as `Yes`, or reuse an old decision. |
| Continue direct discussion without accepting proposed work. | Start consensus, apply an artifact change, commit, push, merge, or change a remote. |

A lead turn can spend provider calls and appends visible plaintext conversation
and content-free accounting facts. Protect the workspace and provider data.
Rejecting a proposal avoids the proposed specialist work, but it does not erase
the lead call and proposal that were already recorded.

## One lead turn, end to end

| Stage | Runtime/agent operation | What the user receives |
| --- | --- | --- |
| Accept | Validate session, active conversation, text, pending state, and optional callback; append the user message. | No authority is inferred from the text. |
| Reference prelude | If `${...}` indicators exist, authorize and read every addressed source in order. | No file bytes are copied into the indicator or proposal. |
| Context plan | Select required/working context and leave additive knowledge searchable. | Optional content-free progress through the Python callback. |
| Provider loop | Call the lead; if requested, run bounded lexical SEARCH and exact READ, then call the same lead again with only the new observation. | No specialist or shell command is triggered. |
| Validate | Require one strict answer/question/critique/recommendation/proposal outcome; build proposal authority from runtime facts. | Provider prose cannot invent authority, IDs, reviewers, or budgets. |
| Persist | Append provider accounting and the validated lead message; for a proposal, atomically append disclosure plus `awaiting_confirmation`. | One `LeadTurnResult` and one next safe action. |
| Decide | On an exact structured Confirm/Reject/Revise, reread the authoritative tail and append the decision/state. | One `LeadDecisionResult`; #452 stops before specialist execution. |

SEARCH and READ are read-only context operations. Answering, critiquing,
recommending, proposing, and recording the conversation are lead/runtime
operations. Specialist calls, debate, synthesis, artifact writes, publication,
and Git remain outside this stage.

## Call the lead from Python

Assume `runtime` has an initialized workspace, a configured ready backend, and
an existing active conversation. Call the lead without an `agent_id`:

```python
result = await runtime.lead_turn(
    "novel",
    conversation_id,
    "Does the opening conflict with the outline?",
)
```

The bound form supplies the session ID:

```python
session = runtime.session("novel")
result = await session.lead_turn(
    conversation_id,
    "Does the opening conflict with the outline?",
)
```

The result is a `LeadTurnResult` imported from
`story_writing_agents.conversations`. Its `kind` is exactly one of:

| Kind | Meaning | `next_action` |
| --- | --- | --- |
| `answer` | The lead can answer from authorized evidence. | `continue_with_lead` |
| `question` | One user choice is required before the lead can proceed usefully. | `reply_to_lead` |
| `critique` | The lead can evaluate the current direction without added work. | `continue_with_lead` |
| `recommendation` | The lead can recommend the next action without added work. | Normally `continue_with_lead`; recovery projections use the state-specific action. |
| `proposal` | Added specialist work would be useful or required. | `submit_confirmation` |

Fresh provider-produced direct outcomes have no proposal and `state=None`. They
do not create a synthetic `completed` step. History normalization also uses a
recommendation result to expose an existing proposal or a blocking recovery
state; in that case the returned `state` and `next_action` are authoritative.

### Direct-answer scenario

```text
User input
Does the earlier lead response already settle this question?

LeadTurnResult
kind: answer
message: The earlier response supplies enough evidence.
proposal: none
state: none
next_action: continue_with_lead
```

The lead provider was called, and it may have used bounded SEARCH and READ
actions. No specialist was proposed or invoked.

## How routing works

The prompt gives the lead these specialist domains:

| Specialist | Semantic domain |
| --- | --- |
| Plot & Structure Architect (`plot_architect`) | Outlines, plot structure, flow, and pacing |
| Character & World Custodian (`character_custodian`) | Characters, relationships, world facts, and continuity |
| Tone & Execution Specialist (`execution_specialist`) | Narrative rules, manuscripts, prose, POV, tone, and writing operations |

Runtime code independently enforces mandatory reviewers from closed facts. It
does not run language inference over user or provider prose:

| Referenced core document | Required specialist |
| --- | --- |
| `core_documents/global_outline.md` | `plot_architect` |
| `core_documents/part_outlines.md` | `plot_architect` |
| `core_documents/character_sheets.md` | `character_custodian` |
| `core_documents/narrative_rules.md` | `execution_specialist` |

A manuscript or conversation reference alone does not create a runtime-
mandatory reviewer. The lead should still route a manuscript request according
to its semantic responsibility. Runtime rejects an unknown, duplicate,
lead-as-specialist, noncanonical-order, infeasible, unassigned-input, or
mandatory-reviewer-omitting proposal. It never silently adds undisclosed work.

## References are read requirements

One turn may contain several ordered indicators:

```text
Check these sources in order:
${core_documents/global_outline.md}
${core_documents/character_sheets.md}
${manuscript/part-01/opening.md}#L20-L32
```

An indicator carries a path or a path plus inclusive line bounds. It does not
embed file content and does not authorize a write. Before the substantive lead
call, the runtime authorizes the complete ordered batch, performs a separate
mandatory-reference prelude, captures the exact whole or ranged bytes, and
delivers them as untrusted context. It revalidates the operation before
accepting the lead outcome.

The durable proposal retains only input aliases, indicators, ranges, states,
and revision digests. It does not retain the referenced bytes. A later assigned
specialist must independently read its own references; the lead's earlier read
cannot be borrowed as specialist evidence.

## A runtime-built proposal

For a cross-domain request, the provider may express the semantic intent to
consult Plot and Character. Runtime then validates every alias and required
reviewer, derives the current input revisions and participant profiles,
allocates a feasible budget, creates IDs, and computes the proposal digest.

A content-light user-visible projection looks like:

```text
Proposal <step-id> / revision 1
Purpose: Check plot causality and character motivation.
Assignments:
- Plot & Structure Architect: check causal fit against the outline and opening range.
- Character & World Custodian: check motivation against the character sheet.
Inputs:
- ${core_documents/global_outline.md} @ sha256:<digest>
- ${core_documents/character_sheets.md} @ sha256:<digest>
- ${manuscript/part-01/opening.md}#L20-L32 @ sha256:<digest>
User impact: specialist advice and durable collaboration records only.
Side effects: bounded provider invocation and durable records.
Cannot change: writing artifacts, Git index, commits, remotes, or merges.
Budget: round 1 of 2; 2/12 calls spent; at most 10 additional calls.
Proposal digest: sha256:<digest>
Actions: Confirm / Reject / Revise
```

The maximum additional calls are a reservation, not a prediction that every
slot will be used. The exact values depend on already submitted calls,
references, assignments, and synthesis allocation.

## Confirm, reject, or revise

Import decision values from the conversations facade:

```python
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    LeadStepDecision,
)
```

Only a complete decision that copies the exact current request and proposal
facts is eligible:

```python
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
```

The runtime rereads the authoritative tail rather than trusting caller-supplied
sequence authority. It rederives mutable references, routes, profiles, context
policy, and budget facts before accepting Confirm.

| Decision | Durable result | Provider/specialist effect in #452 |
| --- | --- | --- |
| Confirm | `confirmed`; `start_or_reconfirm` | None. Confirmation records evidence and creates only a private process-local start capability for the later execution owner. |
| Reject | `rejected`; `continue_with_lead` | None. The proposed work does not run. |
| Revise | Old revision becomes `superseded`; a replacement lead turn follows. | Only the replacement lead call runs. The step ID stays, revision increases, and proposal ID/digest/request are fresh. |

For example, Reject produces a durable result without another provider call:

```text
User decision: reject <exact current proposal fields>
LeadDecisionResult:
  state: rejected
  next_action: continue_with_lead
  revised_turn: none
Effect: no specialist call, artifact change, or Git operation
```

Revise requires visible revision text:

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
    revision_request="Limit the review to plot causality.",
)
revised = await runtime.decide_lead_step(revision)
replacement = revised.revised_turn
```

When Revise returns a replacement proposal, follow
`replacement.next_action`; the enclosing decision records the superseded
revision and therefore reports `continue_with_lead`.

### Why free-form “Yes” does not confirm

This is an ordinary lead turn, not a decision:

```python
next_result = await runtime.lead_turn("novel", conversation_id, "Yes")
```

If a proposal is pending, runtime first appends the user message and
`superseded` state as one atomic batch. Only then may it call the lead for a new
outcome. The old proposal can no longer be confirmed. A clarification behaves
the same way. If the application makes no call—for example, it exits while
idle—the pending proposal remains unchanged.

Wrong or replayed request IDs, proposal IDs, revisions, digests, conversations,
sessions, steps, or stale tails are rejected before proposed work. A provider
claim that the user confirmed has no authority.

## Requests to modify story artifacts

Users can already ask the lead to evaluate or plan a modification:

```text
Recommend how to revise ${core_documents/narrative_rules.md} before changing it.
```

The lead can answer, critique, recommend, or propose a read-only specialist
consultation. It cannot apply the modification in #452. The same boundary
applies to global and part outlines, character sheets, narrative rules, and
manuscripts.

| Example user request | Lead behavior now | What cannot happen now |
| --- | --- | --- |
| “Recommend a safer outline change after reading `${core_documents/global_outline.md}` and `${core_documents/part_outlines.md}`.” | Read both references; critique/recommend or propose Plot review. | Replace either outline file. |
| “Check `${core_documents/character_sheets.md}` before revising this motivation.” | Read the sheet; answer or propose Character review. | Replace the character sheet. |
| “Evaluate `${core_documents/narrative_rules.md}` before changing the POV rule.” | Read the rules; critique/recommend or propose Execution review. | Replace narrative rules. |
| “Plan a revision of `${manuscript/part-01/opening.md}#L20-L32`.” | Read the range; answer or semantically propose Execution review. | Overwrite or create a manuscript revision. |

Artifact application, consensus phases, and Git operations are separate future
steps. In particular, confirming a consultation never confirms a commit. A Git
commit must later disclose its own effect and receive a separate exact user
confirmation; this stage cannot create or execute that Git proposal.

## What the provider receives

Applications call `lead_turn`; they do not call separate public search, read,
or answer provider endpoints. Runtime binds the lead and drives one compact
wire-v5 contextual invocation. The role prompt is stable binding material. The
initial dynamic payload contains the current command, identities, strict answer
contract, and content-free input aliases—not the full conversation history.

The following shapes are sanitized from the lead orchestration fixtures.
Generated correlations, IDs, handles, digests, and byte counts vary.

### Initial lead call

```json
{
  "c": "<correlation-1>",
  "q": [
    "lead",
    "sa",
    "wcdo",
    [4, 2, 2, 4, 8, 8, 16, 200],
    ["lead", "outcome-v1"]
  ],
  "i": {
    "workflow": "lead_front_channel",
    "command": "Use bounded conversation history if needed.",
    "identity": {
      "agent_id": "lead_orchestrator",
      "session_id": "novel",
      "conversation_id": "<conversation-id>",
      "turn_id": "<turn-id>",
      "step_id": "<step-id>"
    },
    "input_aliases": [
      {
        "input_id": "user_input",
        "kind": "user_input",
        "locator": "conversation/<turn-id>/user_input",
        "revision_digest": "sha256:<digest>",
        "start_line": null,
        "end_line": null
      }
    ],
    "answer_contract": {
      "kind": ["answer", "question", "critique", "recommendation", "proposal"],
      "message": "nonempty user-visible text",
      "proposal": {
        "relation": "new_step",
        "purpose": "visible purpose",
        "assignments": "required only for proposal"
      },
      "assignments": {
        "agent_id": "canonical specialist ID",
        "instruction": "complete visible assignment",
        "input_ids": "nonempty runtime-supplied aliases"
      }
    }
  }
}
```

When references are attached, a separate prelude consumes one submitted call.
The following substantive payload adds the selected bytes after authorization:

```json
{
  "c": "<correlation>",
  "q": ["lead", "sa", "wcdo", [3, 2, 2, 4, 8, 8, 16, 200], ["lead", "outcome-v1"]],
  "i": {"workflow": "lead_front_channel", "command": "Check the attached sources.", "identity": {}, "input_aliases": [], "answer_contract": {}},
  "untrusted_writing_artifact_context": [
    {"ordinal": 1, "content": "<authorized outline bytes>"},
    {"ordinal": 2, "content": "<authorized manuscript range bytes>"}
  ]
}
```

### SEARCH → observation → READ → observation → ANSWER

The provider can ask for bounded lexical discovery:

```json
{"a":"s","c":"<correlation-1>","d":[["visible lead response",["c"]]],"u":[]}
```

Runtime performs only authorized workspace search and calls the same provider
again with the new observation:

```json
{
  "c": "<correlation-2>",
  "q": ["lead", "sra", "wcdo", [3, 1, 2, 4, 7, 8, 16, 200], ["lead", "outcome-v1"]],
  "o": ["s", false, [[1, ["source", 4, "conversation:<id>", "conversation", ["revision=source_digest", "sha256:<digest>"], 4375], ["boundary", "records", 4, 4, null, null, "sha256:<digest>", 827], ["provenance", "conversation_storage", "conversation/<id>"], "c", "<handle>", ["snippet", "Visible lead response.", 22]]]]
}
```

The provider may request the invocation-local handle:

```json
{"a":"r","c":"<correlation-2>","d":["<handle>"],"u":[]}
```

Runtime recaptures and revalidates the exact source before returning a bounded
record:

```json
{
  "c": "<correlation-3>",
  "q": ["lead", "sa", "wcdo", [2, 1, 1, 4, 7, 8, 15, 200], ["lead", "outcome-v1"]],
  "o": ["r", [[["source", 4, "conversation:<id>", "conversation", ["revision=source_digest", "sha256:<digest>"], 4375], ["boundary", "records", 4, 4, null, null, "sha256:<digest>", 827], ["provenance", "conversation_storage", "conversation/<id>"], "c", "<handle>", "<canonical readable event JSON>"]]]
}
```

The provider's terminal answer names every READ handle it used:

```json
{
  "a": "a",
  "c": "<correlation-3>",
  "d": [{
    "kind": "answer",
    "message": "The prior lead response supplies enough evidence.",
    "proposal": null
  }],
  "u": ["<handle>"]
}
```

SEARCH and READ are inner, call-local context actions. They are not web search,
shell execution, specialist calls, durable authorization, or side effects.

### Provider intent versus runtime authority

The provider may return only visible semantic proposal intent:

```json
{
  "a": "a",
  "c": "<correlation>",
  "d": [{
    "kind": "proposal",
    "message": "Plot causality and motivation need separate review.",
    "proposal": {
      "relation": "new_step",
      "purpose": "Check plot causality and character motivation.",
      "assignments": [
        {
          "agent_id": "plot_architect",
          "instruction": "Check causal fit against the outline.",
          "input_ids": ["user_input", "reference_1", "reference_3"]
        },
        {
          "agent_id": "character_custodian",
          "instruction": "Check motivation against the character sheet.",
          "input_ids": ["reference_2"]
        }
      ]
    }
  }],
  "u": []
}
```

The provider cannot supply step, proposal, confirmation, or attempt IDs;
revision or digest; budget or policy facts; user impact or side effects;
capabilities; artifact or Git authority; provider metadata; or private
reasoning. Unknown, duplicate, private, mixed, or noncanonical fields fail
closed. Runtime constructs the different canonical proposal shown earlier.

## State transitions: external and internal meanings

| State | What the user should understand | Runtime action or next safe action |
| --- | --- | --- |
| `awaiting_confirmation` | Added work has not started. The exact disclosed proposal is pending. | Confirm, Reject, Revise, or submit another ordinary message. |
| `confirmed` | The exact decision is durable. #452 still has not started specialist work. | A live runtime may later start; otherwise reconfirmation is required. |
| `reconfirmation_required` | Durable confirmation survived, but process-local start authority did not. The proposal and digest are unchanged. | Use the fresh request to Confirm, Reject, or Revise. |
| `rejected` | The proposal did not run. | Continue with the lead. |
| `superseded` | That proposal revision is permanently unconfirmable. | Review its replacement or continue with the lead. |
| `running` | External work may have begun. #452 recognizes but never creates this state. | Wait for a live owner or finalize an already durable result. |
| `completed` | A complete execution result is durable. #453 owns creating it. | No history-derived action is automatically authorized. |
| `interrupted` | Execution stopped with a durable result. #453 owns creating it. | Continue according to the visible recovery guidance. |
| `failed` | Execution failed with a durable result. #453 owns creating it. | Continue according to the visible recovery guidance. |
| `outcome_unknown` | External work may have happened, but no authoritative result proves what occurred. | Inspect and resolve; never replay automatically. |
| `recovery_required` | Authoritative history or call-boundary facts are uncertain or corrupt. | Repair authoritative history before normal collaboration. |

Internally, proposal disclosure and its confirmation request are one atomic
batch whose request is the tail. A decision and resulting state are also one
atomic batch. Confirmation is evidence, not an executable credential. The
runtime may mint a private, nonserializable, single-use capability only after
the confirmation batch is durable and current facts still match.

## Restart and failure recovery

- A failed confirmation append starts no work; submit a fresh valid decision
  only after rereading authoritative state.
- Restart at `confirmed` cannot reconstruct the private capability. The next
  lead call returns `reconfirmation_required` with a fresh request and the same
  proposal/digest without calling the provider.
- Restart at unresolved `running` with no authoritative result records
  `outcome_unknown` and never replays the call or side effect.
- A known durable result at `running` allows only deterministic local
  finalization; it does not authorize another provider call.
- A durable user request without a provider-preparation/outcome marker, a
  dangling submitted lead call, corrupt history, or uncertain authority returns
  `recovery_required` and blocks normal collaboration.
- Invalid provider JSON is never reinterpreted as prose or a proposal.
- Reference, route, profile, context-policy, or budget drift rejects Confirm
  while leaving the proposal unexecuted.

## Work limits

One collaboration lineage permits at most two specialist rounds and twelve
submitted physical provider calls. The lineage counts:

- mandatory-reference preludes;
- the initial lead call;
- each SEARCH, READ, and ANSWER continuation;
- submitted failures, timeouts, and cancellations; and
- later specialist and lead-synthesis calls.

A PREPARED call that did not cross the provider boundary does not count. A
submitted call counts once even if it later fails. One context invocation is
also capped at five physical calls and may receive a lower limit when fewer
lineage calls remain. When only the final substantive slot remains, the
provider must answer. Proposal construction reserves disjoint assignment and
synthesis limits before confirmation, making physical call thirteen and
specialist round three unreachable.

## Current boundary and next integration

This stage implements the lead front channel, context actions, runtime-built
proposals, exact decisions, durable accounting, and recovery normalization in
the Python API. It does not implement specialist dispatch, replies, synthesis,
execution results, artifact publication, consensus, Git operations, or the
interactive conversation UI. Those capabilities must preserve this exact
confirmation and authority boundary when later issues connect them.

See the [runtime API](../reference/runtime-api.md),
[collaboration-history reference](../reference/collaboration-history.md),
[agent-context design](../design/agent-context.md), and
[artifact-reference guide](artifact-references.md).
