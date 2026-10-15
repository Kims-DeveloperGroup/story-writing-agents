# Conversation transcripts and history

The active transcript is the readable projection of one authoritative
schema-v2 collaboration-event stream. Filesystem and SQLite storage implement
the same `CollaborationEventStore` port:

```python
append_batch(batch: CollaborationEventBatch) -> CollaborationAppendReceipt
read(session_id: str, conversation_id: str) -> tuple[CollaborationEvent, ...]
read_context(session_id: str,
             conversation_id: str) -> CollaborationHistoryProjection
```

`append_batch()` is atomic and validates a complete contiguous canonical batch
before mutation. `read()` returns the complete canonical stream.
`read_context()` returns the validated state projection. The separate pure
`select_bounded_collaboration_context()` function requires explicit event IDs
for content-bearing readable context; its default contains no record text.

## What users see

`/history [COUNT]` reads the focused active conversation.
`/transcript [ID [COUNT] [--from SEQUENCE]]` opens an isolated, transient viewer
for an active or archived conversation without changing focus. Bare `/transcript`
opens the attached session's conversation picker; choices show
`Subject (conversation_id)` with full IDs in catalog order. Selection resolves
the ID again before reading. An explicit ID skips the picker. An empty catalog,
picker cancellation, Ctrl-C, or EOF reads no transcript and preserves attachment,
focus, and conversation history.

Without `COUNT`, the viewer shows the complete conversation from sequence 1,
or the complete remaining history from `--from`. Supplying `COUNT` retains the
existing bounded page and its continuation hint. Each invocation reads one
validated snapshot; browsing never rereads storage or includes later appends.

Use arrows or `j`/`k` to scroll, Page Up/Down to move a screen, and Home/End to
reach the beginning/end. Close with `q`, Escape, Ctrl-C, or EOF. Like `/exchanges`,
closing clears the viewer and restores the previous shell screen. Request
headings, message frames, results, and hints stay inside the viewer. Terminals
without transient display support receive only a content-free notice.

Each row exposes semantic identity before readable fields:

```text
History (1/2)
  [event proposal_event]
    Sequence: 1
    Turn: turn_a
    Step: step_a
    Kind: message
    Direction: lead_to_user
    Actors: lead_orchestrator → user
    proposal.purpose: Consult specialists before recommending the next action.
```

Exact decoration varies by terminal. Private event payload fields, provider
instructions, route fingerprints, credentials, and arbitrary workspace bytes
are not part of the readable record.

Artifact-review events add only their closed public display fields to the
readable rows:

```text
artifact_review.decision: propose
artifact_review.message: The exact candidate review is complete.
artifact_proposal.state: reviewed
```

Candidate and reviewed-proposal payloads intentionally contain the exact
proposed change, but their content is not a readable/search field and is not
printed merely because history is projected. Python callers inspect the typed
`LeadTurnResult.artifact_proposal` or canonical event payload when they need the
candidate itself. `reviewed` still means no Apply, write, commit, or Git
authority.

After the rows, `/history` explains durable workflow state:

```text
[context]
  History displayed: 2/2 records
  Last sequence: 2
  Current state: awaiting_confirmation
  User meaning: A proposal is waiting for your confirmation.
  Next safe action: submit_confirmation
  Next action meaning: Review the proposal and submit a confirmation decision.
```

For a new conversation the exact semantic values are:

```text
History displayed: 0/0 records
Last sequence: 0
Current state: empty
User meaning: No collaboration event has been recorded yet.
Next safe action: none
Next action meaning: No history-derived action is pending.
```

`/status` reports the same current state and next-safe-action meanings for the
focus. These are observations, not executable buttons or proof that the
shell invoked a lead turn. The Python lead workflow writes these facts, but
presentation never creates execution authority.

## State and next-action meanings

| State | User meaning | Next safe action |
| --- | --- | --- |
| `empty` | No collaboration event has been recorded yet. | `none` |
| `awaiting_confirmation` | A proposal is waiting for your confirmation. | `submit_confirmation` |
| `reconfirmation_required` | The earlier confirmation is no longer sufficient; review and confirm the updated proposal. | `submit_confirmation` |
| `confirmed` | You confirmed the proposal; execution has not started yet. | `start_or_reconfirm` |
| `running` | The confirmed work is currently being executed. | `finalize_result` when a result awaits finalization; otherwise `none` |
| `completed` | The confirmed work completed and its result is recorded. | `none` |
| `rejected` | You rejected the proposal; that work will not execute. | `continue_with_lead` |
| `superseded` | A newer proposal replaced this proposal. | `continue_with_lead` |
| `interrupted` | Execution stopped before a complete outcome was recorded. | `continue_with_lead` |
| `failed` | Execution failed and the failure outcome is recorded. | `continue_with_lead` |
| `outcome_unknown` | Execution may have occurred, but its outcome cannot be proven. | `inspect_and_resolve_outcome` |
| `recovery_required` | History requires operator inspection before any safe continuation. | `repair_authoritative_history` |

When a lead turn carries an artifact candidate, its independent artifact state
is one of:

| Artifact state | User meaning |
| --- | --- |
| `awaiting_consultation_confirmation` | The exact candidate and reviewers are disclosed; review has not started. |
| `reviewing` | Confirmed review of that exact candidate is in progress. |
| `reviewed` | Complete attributed review is durable; no file changed and no Apply/Git authority exists. |
| `revision_required` | The lead requires a replacement candidate. |
| `rejected` | The candidate was rejected by the user or lead. |
| `superseded` | A newer turn/candidate retired it. |
| `stale` | Evidence or base state changed; rebuild from current files. |
| `failed` | Review or synthesis failed or was incomplete. |
| `recovery_required` | Repair authoritative review history before continuing. |

Next-action labels mean:

| Action | User meaning |
| --- | --- |
| `none` | No history-derived action is pending. |
| `submit_confirmation` | Review the proposal and submit a confirmation decision. |
| `start_or_reconfirm` | Start confirmed work, or reconfirm if its basis changed. |
| `finalize_result` | Record the terminal result for the running work. |
| `continue_with_lead` | Continue the conversation with the lead agent. |
| `inspect_and_resolve_outcome` | Inspect durable evidence and resolve the uncertain outcome. |
| `repair_authoritative_history` | Repair authoritative history before continuing. |

## Transition flow

The projector validates the whole stream before exposing a state:

```mermaid
flowchart LR
    E[empty] --> A[awaiting_confirmation]
    A --> C[confirmed]
    A --> J[rejected]
    A --> S[superseded]
    C --> R[running]
    C --> RC[reconfirmation_required]
    RC --> C
    RC --> J
    RC --> S
    RC --> X[recovery_required]
    R --> D[completed]
    R --> I[interrupted]
    R --> F[failed]
    R --> U[outcome_unknown]
    D --> A
    J --> A
    S --> A
    A --> X
    C --> X
    R --> X
    D --> X
    J --> X
    S --> X
    I --> X
    F --> X
    U --> X
```

The artifact candidate has a correlated but separate flow:

```mermaid
flowchart LR
    A[awaiting_consultation_confirmation] -->|Confirm, still current| V[reviewing]
    A -->|Reject| J[rejected]
    A -->|Revise or replacement text| S[superseded]
    A -->|Confirm after drift| T[stale]
    V -->|lead: propose| D[reviewed]
    V -->|lead: revise| Q[revision_required]
    V -->|lead: reject| J
    V -->|incomplete review or invalid synthesis| F[failed]
    V -->|base drift before finalization| T
    V --> X[recovery_required]
```

Only `reviewed` carries an `ArtifactReviewedProposal`, and even that value is
non-executable review evidence. A new candidate starts a new
`awaiting_consultation_confirmation` flow with fresh identity and digest.

`recovery_required` is terminal. An illegal transition, identity mismatch,
noncanonical field, partial batch, corrupt byte stream, or inconsistent
sequence fails closed rather than producing a plausible state.

## Unsupported owning-boundary history

The active filesystem owner rejects a conversation directory containing
`transcript.jsonl`. The SQLite owner rejects any row in the unsupported
`conversation_events` table (an empty table is only a recognized sentinel).
The check happens before append/read mutation. The runtime neither selects that
data nor combines it with v2 history. #454 separately owns rejection at the
whole-workspace boundary.

See [Conversation search](conversation-search.md) for exact citations and
snapshot-safe pages, [Collaboration history](collaboration-history.md) for the
complete event contract, and [Session storage](configuration/session-storage.md)
for physical paths.
