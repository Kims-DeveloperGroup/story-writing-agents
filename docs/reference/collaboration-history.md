# Structured collaboration history

> **Availability:** schema-v2 collaboration history is an immutable, pure
> Python contract under `story_writing_agents.conversations`. It is not used by
> the current runtime, filesystem or SQLite conversation stores, transcript
> views, or shell. Later lead-agent work beginning with issue #467 may compose
> it. There is no migration or user action now.

Key terms: **proposal**—the complete disclosure for one possible step;
**decision**—one structured `confirm`, `reject`, or `revise` response to the
exact current request; **physical call**—one submitted provider invocation,
including a retry; **batch**—one all-or-nothing ordered append candidate;
**projection**—a validated summary that is evidence, never execution authority.

## What changes for a user

The contract can represent a future lead-led conversation without losing which
actor said or did what. It can record typed user, lead, specialist, runtime,
artifact, and Git actors; disclose the exact proposed work before it starts;
bind a structured decision to that proposal; retain successful sibling results
when another assignment fails; and expose a deterministic recovery state after
a restart.

It cannot yet run a lead or specialist, change a story artifact, mutate Git,
persist through either current conversation-storage adapter, or display a new
shell workflow. It does not reinterpret free text as permission. It does not
turn a stored confirmation or projected `next_safe_action` into executable or
reconstructible authority. No current command, transcript, storage selection,
or provider route changes.

Conceptually, the distinction is:

| Current conversation transcript | Schema-v2 collaboration history |
| --- | --- |
| One user message followed by one agent reply or runtime error | Ordered typed exchanges among user, lead, specialists, runtime, artifact, and Git actors |
| Schema version `1`; current runtime/storage/UI authority | Schema version `2`; pure future-facing contract only |
| Text content and complete-turn context | Proposals, exact decisions, dispatches, replies, terminal provider facts, results, and durable step states |
| Appends one visible event | Requires atomic batches for facts that must never be separated |
| Accepted by current transcript stores | Deliberately rejected by the v2 history projector, which accepts only canonical v2 events |

The two histories remain distinct. Schema-v1 `ConversationEvent` values are not
schema-v2 collaboration events, and `project_collaboration_history()` rejects
them. This separation defines no conversion workflow and requires no migration.

## Illustrative future interaction

The following is a presentation example only. No such UI, buttons, provider
calls, or lead workflow are active yet.

```text
Lead proposal
Purpose: Ask the plot architect to review outline lines 3-12.
Assignment: plot_architect, round 1, at most 2 physical calls
Input: ${core_documents/outline.md}, lines 3-12,
       revision sha256:1111...1111 (content is not embedded)
Impact: Read-only provider work; no artifact or Git mutation.
Budget: round 1 of 2; at most 2 additional calls; 12-call lineage ceiling

[Confirm]  [Reject]  [Revise]
```

A future application must submit an exact structured decision, not the visible
button label or conversational text. For example, this input:

```json
{"confirmation_request_event_id":"request_event","confirmation_request_id":"request_a","decision":"confirm","proposal_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","proposal_id":"proposal_a","proposal_revision":1}
```

must identify the authoritative request at the current history tail and the
same proposal ID, revision, and digest. Its atomic output is a
`confirmation` event followed by the corresponding `confirmed`, `rejected`, or
`superseded` state:

| Exact structured input | Exact co-batched state output |
| --- | --- |
| `confirm` | `confirmed` |
| `reject` | `rejected` |
| `revise` | `superseded` |

For `revise`, a changed proposal is a new, higher revision with a fresh
request. The decision is single-use. A free-text
message such as `Yes`, even when stored after the request, is non-authorizing
and makes that older request ineligible because it is no longer the exact tail.

Confirmation evidence proves only what was decided about a disclosed snapshot.
Before execution, an application must reacquire its own live capabilities and
validate current route, profile, policy, input, and resource facts. History
decoding or projection never recreates a provider, filesystem lease, Git
capability, credential, or other authority.

## Proposal closure and limits

`CollaborationProposal` discloses every one of these fields:

| Field | Meaning |
| --- | --- |
| `step_id`, `proposal_id`, `proposal_revision` | Lineage, proposal identity, and increasing revision |
| `step_kind`, `purpose` | Exact typed step and visible reason |
| `assignments` | Ordered, unique specialist assignments |
| `user_impact`, `side_effects` | Visible consequence and closed side-effect set |
| `budget` | Whole-lineage and whole-next-step physical-call reservation |
| `basis` | Route and policy fingerprints, participant profiles, and closed input revisions |
| `proposal_digest` | SHA-256 digest over every other proposal field, including order |

The supported step kinds are `lead_specialist_consultation`,
`specialist_advisory`, `artifact_mutation`, and `git_operation`. Disclosable
side effects are `provider_invocation`, `artifact_mutation`,
`git_index_mutation`, `git_commit`, `git_remote_mutation`, and `git_merge`.
These values describe possible effects; this contract performs none of them.

An assignment closes over one unique canonical specialist and contains:

```text
assignment_id = "assignment_plot"
specialist_id = "plot_architect"
round = 1
instruction = "Review the exact referenced outline range."
input_ids = ("input_outline",)
max_physical_calls = 2
```

Every proposal input is content-free identity and revision evidence:

```text
input_id = "input_outline"
kind = "core_document"
locator = "${core_documents/outline.md}"
state = "present"
revision_digest = "sha256:1111...1111"
start_line = 3
end_line = 12
```

Input kinds are `user_input`, `conversation_event`, `core_document`,
`manuscript`, `agent_state`, and `writing_artifact`; state is `present` or
`absent`. A locator, optional inclusive line range, and digest can identify the
source revision. They never carry file bytes. Assignments' `input_ids` must
close exactly over the proposal inputs: no declared input may be unassigned and
no assignment may cite an undeclared input. `agent_profiles` similarly close
over every called specialist and, when synthesis is budgeted, the lead.

The fixed budget limits are two rounds and 12 submitted physical calls across
the lineage. Retries count when submitted. A proposal adds exactly one round;
its per-assignment allocations plus `synthesis_max_physical_calls` must equal
`max_additional_physical_calls`, and spent plus additional calls cannot exceed
12. A lead-specialist consultation reserves at least one synthesis call. A
specialist advisory has exactly one assignment, allows only provider invocation,
and reserves zero synthesis calls.

For example, this advisory reservation is valid:

```text
round_limit = 2                 rounds_spent = 0
rounds_added = 1                physical_call_limit = 12
physical_calls_spent = 0        max_additional_physical_calls = 2
synthesis_max_physical_calls = 0
```

At the ceiling, assignments can reserve 5 and 6 calls and synthesis can reserve
1: exactly 12. A possible thirteenth call is rejected before execution facts
can enter canonical history.

## Ordered actors, events, and result correlation

Actors have one of the exact kinds `user`, `lead`, `specialist`, `runtime`,
`artifact`, and `git`. Lead and specialist actors also carry a canonical agent
ID; other actor kinds cannot. Sender/recipient pairs derive one of the closed
directions, so direction is not a second stored claim.

The eight event kinds are `message`, `assignment_dispatch`,
`specialist_reply`, `step_state`, `confirmation`, `execution_result`,
`interruption`, and `terminal_outcome`. Each stored event carries schema,
event/session/conversation/turn/step identity, contiguous sequence, canonical
UTC timestamp, atomic-batch metadata, typed actors, and its kind-specific
payload. Visible message and reply content is capped at 32,768 strict-UTF-8
bytes and cannot contain surrogate code points. Payloads contain visible facts,
not hidden reasoning.

A running attempt dispatches assignments in proposal order. Every reply binds
the exact proposal ID/revision/digest, execution attempt, assignment, and
dispatch event. Each provider-facing invocation records a terminal outcome with
its scope, submitted-call count, and assignment ID where applicable. The
execution result then correlates all proposal assignments in their original
order and contains:

```text
proposal_id = "proposal_a"
proposal_revision = 1
proposal_digest = "sha256:aaaa...aaaa"
execution_attempt_id = "attempt_a"
assignment_outcomes = (
    ("assignment_plot", "succeeded", 1, "reply_assignment_plot"),
    ("assignment_character", "failed", 1, None),
)
synthesis_outcome = "not_attempted"
synthesis_submitted_physical_calls = 0
synthesis_result_event_id = None
overall_outcome = "failed"
visible_exchange_event_ids = (
    "reply_assignment_plot",
    "terminal_assignment_plot",
    "terminal_assignment_character",
)
```

That partial failure preserves the successful plot reply and both terminal
assignment facts. Because any assignment failed, overall outcome is `failed`
and synthesis must be `not_attempted` with zero submitted calls and no
synthesis result. An advisory also has zero synthesis by design, even when its
single assignment succeeds.

## Atomic batch flow

The storage-neutral history is an ordered sequence of nonempty batches. One
batch is restricted to one session conversation and turn, contains unique event
IDs, and compares `expected_last_sequence` with the authoritative tail.
Storage assigns contiguous sequences, canonical UTC timestamps, ordinal/size,
and the digest of the exact caller-owned batch.

```text
proposal + awaiting_confirmation (request is last)
    -> structured decision + confirmed/rejected/superseded
    -> running + every assignment_dispatch in proposal order
    -> replies/messages + every terminal_outcome + execution_result (last)
    -> visible terminal notice + completed/failed/interrupted

running + interruption + outcome_unknown
```

The paired facts above must be co-batched. An interrupted finalization also
co-batches its interruption record. A store must expose only the entire batch
or none of it; a partial stored batch makes the history corrupt.

`CollaborationEventStore` is the sole store port:

```python
append_batch(batch: CollaborationEventBatch) -> CollaborationAppendReceipt
read(session_id: str, conversation_id: str) -> tuple[CollaborationEvent, ...]
read_context(session_id: str, conversation_id: str) -> CollaborationHistoryProjection
```

It is a protocol, not an implementation. Callers own a conforming adapter and
its persistence, locking, timestamp assignment, durability, retention, and
access controls. The current filesystem and SQLite conversation stores do not
implement it. The store decides whether an atomically committed candidate is
`APPENDED` or an exact retry is `ALREADY_COMMITTED`; an exact retry returns the
original sequence range and timestamps.

| Append result/failure | Meaning | Safe response |
| --- | --- | --- |
| `APPENDED` | Entire batch committed once | Continue from returned receipt/history |
| `ALREADY_COMMITTED` | Same batch ID and exact content already committed | Treat as success; use original sequences/timestamps |
| `stale_sequence` | Expected tail no longer matches | Read complete history and form a new candidate |
| `batch_id_conflict` | Batch ID exists with different content | Stop; choose a fresh ID only for genuinely new work |
| `event_id_conflict` | An event ID already exists elsewhere | Stop and repair caller identity generation |
| `invalid_transition` | Candidate violates state, correlation, ordering, or co-batch rules | Do not append or execute it |
| `corrupt_history` | Existing history is not canonical | Stop normal work and repair authoritative history |

The last five rows are the complete fixed `CollaborationAppendFailure` set.
`CollaborationAppendError.failure` carries that semantic value; exception text
adds no authority or hidden diagnostic detail.

## State and user-action contract

These are the complete and exact states. “Next action” names the
`CollaborationHistoryProjection.next_safe_action` value; it is evidence for an
application, never permission to perform the action.

| State | Internal meaning | External user meaning | Allowed next action |
| --- | --- | --- | --- |
| `awaiting_confirmation` | A complete proposal and fresh request are the durable tail | Review exactly the displayed proposal | `submit_confirmation`: submit one structured confirm/reject/revise decision while the request remains the exact tail |
| `reconfirmation_required` | Previously confirmed proposal is retained, but fresh authority is required | Review the unchanged proposal again after restart or revalidation | `submit_confirmation`: answer the fresh request; old confirmation is not reused |
| `confirmed` | One exact structured confirm decision is durable | Decision recorded; execution has not necessarily begun | `start_or_reconfirm`: revalidate live facts, then start atomically or request fresh confirmation |
| `running` | One execution attempt and every ordered dispatch are durable | Confirmed work is in progress | `none` while no result exists; if a complete result is durable, `finalize_result` |
| `completed` | Known successful result and visible terminal notice are durable | Proposed step finished successfully | `none`; a lead may disclose a new higher-revision round within the fixed limits |
| `rejected` | Structured reject decision is durable; no work started | The proposal will not run | `continue_with_lead`; any later work needs a fresh proposal/request |
| `superseded` | Structured revise decision retired this proposal | The lead must propose a changed revision | `continue_with_lead`; review a fresh, higher-revision proposal |
| `interrupted` | Known cancelled execution result and notice are durable | The attempt stopped with a known cancellation | `continue_with_lead`; same-lineage provider work stays blocked, and only `recovery_required` may follow |
| `failed` | Known failed execution result and notice are durable | The attempt failed; successful sibling facts remain visible | `continue_with_lead`; same-lineage provider work stays blocked, and only `recovery_required` may follow |
| `outcome_unknown` | Restart/interruption left no authoritative result for a submitted attempt | The system cannot prove whether work completed | `inspect_and_resolve_outcome`; do no more work and never replay the attempt |
| `recovery_required` | Authoritative history needs external repair | Normal collaboration cannot safely continue | `repair_authoritative_history`; no later state transition is accepted |

Legal state transitions are deliberately narrower than conversational intent.
In particular, only `confirmed` can enter `running`; `outcome_unknown` can only
enter `recovery_required`; and `recovery_required` is terminal in this contract.

## Restart and no-replay cases

- If the durable tail is `awaiting_confirmation`, only a structured decision
  bound to that exact tail can decide it. Any intervening event requires a new
  request.
- If the tail is `confirmed` when authority has been lost across restart, the
  application records `reconfirmation_required` with a globally fresh request
  ID. It must not reconstruct authority from the old confirmation.
- If `running` has a complete, authoritative execution result, projection
  returns `finalize_result`; the application can append the correlated visible
  notice and matching terminal state without repeating provider work.
- If `running` has no authoritative result after restart, interruption and
  `outcome_unknown` are recorded together. The attempt is never replayed and a
  late result is not accepted after that state.
- After `outcome_unknown`, no call-bearing work, result, new proposal, or
  continuation is valid. Inspection may lead only to `recovery_required`,
  followed by out-of-band authoritative repair.
- A partial assignment failure is not an unknown outcome: known successful and
  failed sibling facts enter the one result batch, synthesis is prohibited,
  and the step can finalize as `failed`.

## Python value, wire, and projection example

This example uses only the implemented `story_writing_agents.conversations`
facade. It performs no I/O or provider call. The manual storage-metadata step is
only an in-memory illustration; a future concrete store would own it.

```python
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationBudget,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationSideEffect,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    collaboration_batch_digest,
    decode_collaboration_batch,
    decode_collaboration_proposal,
    encode_collaboration_batch,
    encode_collaboration_proposal,
    project_collaboration_history,
)

digest = "sha256:" + "1" * 64
proposal = CollaborationProposal.create(
    step_id="step_a",
    proposal_id="proposal_a",
    proposal_revision=1,
    step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
    purpose="Ask the plot architect to review the outline range.",
    assignments=(
        CollaborationAssignment(
            assignment_id="assignment_plot",
            specialist_id="plot_architect",
            round=1,
            instruction="Review the exact referenced outline range.",
            input_ids=("input_outline",),
            max_physical_calls=2,
        ),
    ),
    user_impact="Read-only provider work; no artifact or Git mutation.",
    side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
    budget=CollaborationBudget(
        round_limit=2,
        physical_call_limit=12,
        rounds_spent=0,
        rounds_added=1,
        physical_calls_spent=0,
        max_additional_physical_calls=2,
        synthesis_max_physical_calls=0,
    ),
    basis=CollaborationProposalBasis(
        route="specialist_advisory",
        route_fingerprint=digest,
        agent_profiles=(CollaborationAgentProfile("plot_architect", digest),),
        context_policy_fingerprint=digest,
        budget_policy_fingerprint=digest,
        inputs=(
            CollaborationInputRevision(
                input_id="input_outline",
                kind=CollaborationInputKind.CORE_DOCUMENT,
                locator="${core_documents/outline.md}",
                state=CollaborationInputState.PRESENT,
                revision_digest=digest,
                start_line=3,
                end_line=12,
            ),
        ),
    ),
)
assert decode_collaboration_proposal(encode_collaboration_proposal(proposal)) == proposal

lead = CollaborationActor(CollaborationActorKind.LEAD, "lead_orchestrator")
runtime = CollaborationActor(CollaborationActorKind.RUNTIME)
user = CollaborationActor(CollaborationActorKind.USER)
request = CollaborationStepStatePayload(
    state=CollaborationStepState.AWAITING_CONFIRMATION,
    proposal_id=proposal.proposal_id,
    proposal_revision=proposal.proposal_revision,
    proposal_digest=proposal.proposal_digest,
    confirmation_request_id="request_a",
)
common = dict(
    schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
    session_id="session_a",
    conversation_id="conversation_a",
    turn_id="turn_a",
    step_id="step_a",
)
batch = CollaborationEventBatch(
    batch_id="batch_proposal",
    expected_last_sequence=0,
    events=(
        CollaborationEventDraft(
            **common,
            event_id="proposal_event",
            kind=CollaborationEventKind.MESSAGE,
            sender=lead,
            recipient=user,
            payload=proposal,
        ),
        CollaborationEventDraft(
            **common,
            event_id="request_event",
            kind=CollaborationEventKind.STEP_STATE,
            sender=runtime,
            recipient=user,
            payload=request,
        ),
    ),
)
batch = decode_collaboration_batch(encode_collaboration_batch(batch))
batch_digest = collaboration_batch_digest(batch)
history = tuple(
    CollaborationEvent(
        **common,
        event_id=draft.event_id,
        sequence=index,
        timestamp=f"2026-08-31T00:00:0{index}+00:00",
        batch_id=batch.batch_id,
        batch_ordinal=index - 1,
        batch_size=len(batch.events),
        batch_digest=batch_digest,
        kind=draft.kind,
        sender=draft.sender,
        recipient=draft.recipient,
        payload=draft.payload,
    )
    for index, draft in enumerate(batch.events, start=1)
)
projection = project_collaboration_history(history)
assert projection.current_state is CollaborationStepState.AWAITING_CONFIRMATION
assert projection.confirmation_request_id == "request_a"
assert projection.next_safe_action.value == "submit_confirmation"
```

Encoding uses strict compact canonical UTF-8 JSON with sorted keys and no BOM
or trailing newline. Decoding accepts exact `bytes` only, rejects duplicate,
extra, or missing fields, non-finite numbers, noncanonical whitespace/order,
invalid UTF-8, and any schema other than `2`, and verifies embedded digests.
Proposal and batch digests are `sha256:` plus 64 lowercase hexadecimal digits.
The decoder bounds a document at 8 MiB; individual visible text remains subject
to its tighter field limit.

## Privacy, authority, and non-goals

Canonical v2 history may contain user-visible proposal text, instructions,
messages, specialist replies, notices, and interruption text. Applications must
treat those values as sensitive conversation data and apply their own storage,
access, retention, and deletion policy. File inputs contribute locator, range,
state, and digest only—never bytes. Provider credentials, capabilities, raw
provider protocol bodies, hidden reasoning, and reconstructed authorization do
not belong in any payload.

This contract does not define a concrete store, filesystem path, SQLite schema,
CLI command, UI, provider call, lead-agent behavior, artifact/Git executor,
credential model, migration, complete workspace backup, or recovery tool. It
also does not provide the full lead-agent usage guide; that later workflow is
owned by issue #460. See [conversation transcripts](conversation-transcripts.md)
for the active schema-v1 path, [persistence](../design/persistence.md) for
authority ownership, and [package API](package-api.md#structured-collaboration-history-boundary)
for the supported import boundary.
