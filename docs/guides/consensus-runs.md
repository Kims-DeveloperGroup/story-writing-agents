# Run a phase-confirmed consensus workflow

Python callers can move a prompt or complete supplied draft through a durable,
stepwise consensus workflow. Every phase is proposed first and runs only after
an exact user decision. The installed `story` shell still has no command for
starting or advancing consensus.

## User impact

You can submit input, inspect the current phase, Confirm, Reject, or Revise its
exact request, explicitly request each next phase, interrupt active provider
work, and inspect the durable lineage after restart. You cannot use consensus
to publish a manuscript or core document. Successful finalization is a
read-only handoff to the separately confirmed artifact Apply workflow; a later
Git Commit is another independent user confirmation.

## Roles and authority

| Participant | Responsibility | Authority it does not have |
| --- | --- | --- |
| User | Decides whether each disclosed phase may run; later decides Apply and Git Commit separately. | A stale decision cannot start work. |
| Lead Orchestrator | Synthesizes the three current audits, chooses repair or finalization, and validates the reviewed handoff. | It does not vote, write artifacts, or commit. |
| Execution Specialist | Authors `r0` from a prompt or transforms a supplied draft; authors repairs. | It cannot approve or publish its candidate. |
| Three specialists | Plot Architect, Character Custodian, and Execution Specialist each audit the same current candidate. | An earlier vote cannot approve a later revision. |
| Runtime | Owns IDs, request digests, durable events, one-use confirmation authority, routing, provider binding, and material/reference authorization. | Provider prose cannot create authority or select another agent. |
| Provider backend | Executes the exact runtime-selected `AgentRequest`. | It cannot advance phases, apply artifacts, or commit. |

## Complete flow

```text
submit prompt or supplied draft
  -> DRAFT awaiting_confirmation
  -> Confirm -> execution_specialist -> DRAFT completed (r0)
  -> request next -> AUDIT awaiting_confirmation
  -> Confirm -> three auditors -> AUDIT completed
  -> request next -> SYNTHESIS awaiting_confirmation
  -> Confirm -> lead_orchestrator
       conflicts -> REPAIR -> REAUDIT -> SYNTHESIS (repeat)
       approvals -> FINALIZATION
       irreconcilable conflict -> deadlocked
  -> Confirm FINALIZATION -> completed read-only handoff
  -> separately propose and Confirm artifact Apply
  -> separately propose and Confirm Git Commit
```

Both input forms begin with a DRAFT confirmation. A supplied draft is not
silently accepted as `r0`: the Execution Specialist transforms it before any
audit. Audits always call all three voters against the same candidate digest.
Synthesis is a nonvoting lead judgment. Repair creates a new unpublished
candidate; its revision increments when the re-audit request is created.

## Visible states

Use both `inspection.projection.state` and `next_action`; a phase name alone
does not grant authority.

| State | User meaning and next step | Internal meaning |
| --- | --- | --- |
| `awaiting_confirmation` | Review the exact request and choose Confirm, Reject, or Revise. | The request is durable; same-process one-use authority may be available. No provider ran. |
| `confirmed` | Wait for the result; do not decide again. | The decision is durable; execution is starting or awaiting projection. |
| `running` | Wait, or interrupt from another task. | The running checkpoint was saved before the provider call. |
| `completed` | If `next_action=request_next_phase`, explicitly request it. After finalization, continue to Apply separately. | The phase result is durable; publication remains forbidden. |
| `rejected` | This phase is closed. | No provider ran and no authority remains. |
| `superseded` | Request a replacement for the same phase. | Revise closed the old request and invalidated its authority. |
| `interrupted` | Inspect the failure; begin a new workflow if appropriate. | The exact running task was cancelled and closed durably. |
| `failed` | Inspect diagnostics; retry only with a new workflow after correction. | Execution definitely failed; nothing was published. |
| `outcome_unknown` | Stop automation and perform operator recovery. | Provider outcome cannot be proved; replay may duplicate work. |
| `recovery_required` | Reconcile durable storage before proceeding. | Runtime cannot prove a safe append/interruption boundary. |
| `deadlocked` | Review unresolved conflicts; start a later workflow if requirements change. | Lead selected no safe successor; nothing was published. |

`capability_available=True` means this process holds private one-use authority
for the exact pending request. After restart, `awaiting_confirmation` may be
visible with `capability_available=False`; this prevents replay. Do not
manufacture a decision—submit a fresh phase or workflow.

## Prompt-input example

Consensus values are exported from `story_writing_agents.consensus`.

```python
from uuid import uuid4

from story_writing_agents import StoryRuntime, Workspace
from story_writing_agents.consensus import ConsensusDecision, ConsensusRuntimeDecision

runtime = StoryRuntime(Workspace("/path/to/story"), backend=backend)
session = runtime.session("novel")
view = session.submit_consensus_prompt(
    conversation_id,
    "Draft an opening that follows the canonical outline.",
)
print(view.projection.phase.value, view.projection.state.value)
# draft awaiting_confirmation

event = runtime.load_workflow(
    "novel", conversation_id, view.submission.workflow_id
).events[-1]
request = event.payload
decision = ConsensusRuntimeDecision(
    execution_identity=request.execution_identity,
    workflow_id=request.workflow_id,
    phase_request_id=request.phase_request_id,
    proposal_id=request.proposal_id,
    revision_id=request.revision_id,
    request_digest=request.request_digest,
    request_event_id=event.event_id,
    request_event_sequence=event.sequence,
    current_tail_sequence=view.projection.last_sequence,
    confirmation_id=f"confirm:{uuid4().hex}",
    decision=ConsensusDecision.CONFIRM,
)
result = await session.confirm_consensus(decision)
print(result.inspection.projection.state.value)
# completed

next_view = session.request_next_consensus_phase(
    conversation_id, view.submission.workflow_id
)
print(next_view.projection.phase.value, next_view.projection.state.value)
# audit awaiting_confirmation
```

The application should present the request facts to the user and construct a
decision only after an explicit choice.

## Supplied draft, Reject, and Revise

```python
view = session.submit_consensus_draft(conversation_id, complete_draft_text)
print(view.submission.input_kind.value, view.projection.phase.value)
# supplied_draft draft
```

Confirming runs DRAFT and produces newly authored `r0` bytes before audit. A
pending request can instead use `ConsensusDecision.REJECT` (no provider call)
or `ConsensusDecision.REVISE` with a nonempty `revision_request`. Revise returns
`superseded`; `request_next_consensus_phase()` then creates a new request for
the same phase with new one-use authority. `decide_consensus()` accepts all
three decisions; `confirm_consensus()` accepts Confirm only.

## Provider-call input

After Confirm is durable, runtime selects the agent and builds data equivalent
to:

```python
AgentRequest(
    session_id="novel",
    debate_id="consensus-workflow:...",
    agent_id="execution_specialist",
    phase=Phase.DRAFT,
    round_number=0,
    revision_id="r0",
    task="Execute confirmed consensus draft phase.",
    user_payload="Draft an opening ...",
    candidate_payload=None,
)
```

Audit/re-audit maps to `Phase.AUDIT` and calls the three specialists in roster
order. Repair maps to `PROPOSE_REVISION`, synthesis to `SYNTHESIZE`, and
finalization to `FINALIZE`. Post-draft calls also receive the exact current
candidate text. Strict reply shape, revision, assignment, and digest checks
run before a result is accepted.

Canonical artifact input uses the normal reference operation: runtime
revalidates the path and digest and gives the provider an authorized read
coordinator. Canonical bytes are not embedded in durable consensus events and
the agent cannot bypass the reference.

## Interruption and recovery

- Stale request/tail/digest values and reused confirmations fail before work.
- `resume_consensus()` only reloads and inspects; it never recreates authority
  or replays provider work.
- `interrupt_consensus()` targets a currently running phase. If cancellation
  cannot be proved, the result requires recovery rather than claiming success.
- A nonfinal phase advances only through an explicit next-phase request and a
  new confirmation.
- Rejection, interruption, failure, unknown outcome, recovery requirement, and
  deadlock never publish an artifact.

See [Runtime API](../reference/runtime-api.md) for exact signatures and
[Consensus workflow](../design/consensus-workflow.md) for the mechanism.
