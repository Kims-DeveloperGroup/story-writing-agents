# Structured collaboration history

> **Availability:** schema-v2 collaboration history is the sole active
> storage/inspection contract. The selected filesystem or SQLite bundle owns
> one `CollaborationEventStore`; `/history`, `/search`, `/transcript`, and
> `/status` use its provider-free readable projections. Concrete store adapters
> are direct-module-only, while contracts and pure context/event projections
> are available from their documented facades. Python lead turns, structured
> decisions, confirmed specialist execution, lead synthesis, and reviewed
> artifact proposals write the same contract.

Key terms: **proposal**—the complete disclosure for one possible step;
**decision**—one structured `confirm`, `reject`, or `revise` response to the
exact current request; **physical call**—one submitted provider invocation,
including a retry; **batch**—one all-or-nothing ordered append candidate;
**projection**—a validated summary that is evidence, never execution authority;
**artifact candidate**—one immutable complete-state-bound proposed change;
**reviewed artifact proposal**—that exact candidate plus complete attributed
review, with no Apply/write/Git authority.

## What changes for a user

The contract represents the active Python lead conversation without losing
which actor said or did what. It records typed user, lead, specialist, runtime,
artifact, and Git actors; disclose the exact proposed work before it starts;
bind a structured decision to that proposal; retain successful sibling results
when another assignment fails; and expose a deterministic recovery state after
a restart. A story-specific lead judgment is stored with its exact content-free
runtime evidence. An invalid submitted judgment is closed by a typed failure,
so the user can retry without replaying that provider result.

The same exact v2 history is readable without weakening it. A Python caller or
the current shell can project one complete typed record per event, search only the
closed public field set, resolve a hit to the exact original Unicode substring,
page one unchanged snapshot, derive a stable lookup-document ID, and explicitly
select bounded records for possible later provider context.

The contract and its pure projections run nothing. `StoryRuntime.lead_turn()`
uses it while binding the lead. `decide_lead_recommendation()` records a
preparation decision and runs its bounded Lead invocation; `decide_lead_step()`
records exact decisions and executes a confirmed consultation through runtime-owned
capabilities. This path can call specialists and the lead for read-only
synthesis, construct and review an exact artifact candidate, and persist the
review result; it cannot change a story artifact or mutate Git. The active
filesystem and SQLite compositions persist exact batches and
read them after restart. They do not reinterpret free text as permission or
turn a stored confirmation or projected `next_safe_action` into executable
authority. Archive and backend transfer carry the same canonical events.
Unsupported owning-boundary history is rejected before mutation.

Before this addition, the pure v2 projector answered operational questions such
as “what state is this step in?” and “what is the next safe action?” It did not
provide a supported display/search record for each event. Now the state
projection remains unchanged and a separate readable projection answers “what
public event and fields can I show, search, page, or select?” Neither projection
performs I/O or grants authority.

The authoritative history is an ordered sequence of typed exchanges among the
user, lead, specialists, runtime, artifact, and Git actors. Facts that must
never be separated are appended as one atomic batch.

## Explicit readable-history scenario

Suppose a Python application has obtained a complete canonical v2
history and the user asks, “Where did we discuss the outline range?” The
application can, without invoking a provider:

1. call `project_collaboration_readable_history()` to obtain one record for
   every event, including the complete typed payload;
2. call `search_collaboration_history()` with `OUTLINE RANGE` and receive an
   exact citation into `proposal.purpose` or an assignment instruction;
3. call `resolve_collaboration_citation()` against the current complete history
   and display the original substring `outline range`, not normalized text;
4. page the same validated snapshot, continuing only while both its last
   sequence and tail batch digest still match;
5. derive a content-source ID for one readable field if a later integration
   needs a lookup/RAG document identity; and
6. pass the cited event ID explicitly to
   `select_bounded_collaboration_context()` if that later integration chooses
   to include the record.

The default context selection includes no readable record text. Current user
input is not accepted by this selector and remains a separate invocation
input. These pure readable operations do not persist history, authorize
retrieval, or start a provider call. The selected filesystem or SQLite bundle
supplies the canonical history; archive/transfer uses the same event contract.

## Current Python lead interaction

The following is a human-readable projection of a current Python lead result.
It is not an installed shell UI or set of buttons.

```text
Lead proposal
Purpose: Ask the plot architect to review outline lines 3-12.
Assignment: plot_architect, round 1, at most 2 physical calls
Input: ${core_documents/global_outline.md}, lines 3-12,
       revision sha256:1111...1111 (content is not embedded)
Impact: Read-only provider work; no artifact or Git mutation.
Budget: round 1 of 2; at most 2 additional calls; 12-call lineage ceiling

[Confirm]  [Reject]  [Revise]
```

A Python application must submit an exact `LeadStepDecision`, not the visible
label or conversational text. For example:

```json
{"confirmation_request_id":"request_a","session_id":"novel","conversation_id":"conversation_a","step_id":"step_a","proposal_id":"proposal_a","proposal_revision":1,"proposal_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"confirm","revision_request":null}
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
Before execution, runtime revalidates current route, definition/execution
profiles, policy, input, and resource facts, then creates and consumes one
process-local start capability. History decoding or projection never recreates
a provider, filesystem lease, Git capability, credential, or other authority.

### Artifact-candidate review in history

When a valid lead artifact intent is accepted, runtime atomically records the
typed lead judgment, `artifact_review_candidate`, ordinary consultation
proposal, `artifact_proposal_state=awaiting_consultation_confirmation`, and
`step_state=awaiting_confirmation`. The candidate payload contains the exact
content-bearing candidate plus its lead-judgment and consultation identity,
mandatory reviewers, and disclosed reviewers.

Exact Confirm first revalidates the candidate. If current evidence/base state
drifted, history records `artifact_proposal_state=stale` and supersedes the
step with no reviewer call. Otherwise confirmation and durable start record
`artifact_proposal_state=reviewing`, and every disclosed reviewer receives the
same candidate. When all succeed, `artifact_review_synthesis` stores the lead's
`propose`, `revise`, or `reject` decision, message, rationale, user impact,
limitations, every reply event ID, and every unresolved input ID.

Only `propose` records `artifact_reviewed_proposal` and artifact state
`reviewed`. The immutable reviewed proposal closes over candidate,
consultation, evidence, review, synthesis, and history-tail identity and fixes
`artifact_write_occurred`, `apply_authority_granted`, and
`git_action_occurred` to false. `revise` records `revision_required`; `reject`
records `rejected`; incomplete review records `failed`; invalid non-null intent
records `artifact_review_failure=intent_rejected`. None is filesystem or Git
authority.

### Contextual artifact objectives

An `artifact_objective` event carries strict `artifact_objective_v1` or
`artifact_objective_v2` payloads inside the existing schema-v2 event envelope.
This is additive: older event payload bytes remain unchanged, and the outer history version stays v2.
Filesystem/SQLite history, archive, and transfer retain the same objective
records; projection itself appends nothing.

`ArtifactObjectiveFacts` freezes the runtime objective ID, origin conversation
and user event, visible request, exact canonical target paths, and the offered
draft-source snapshot. `ArtifactObjectivePayload` records `start`, `continue`,
or `cancel` beside its accepted Lead judgment in the same atomic batch. A
continuation names the exact previous objective event and preserves the origin
facts. A PREPARE transition also binds its co-batched candidate by event ID,
candidate ID, revision, and digest; its operations must cover the frozen target
scope exactly.

A new objective from structured recommendation Confirm instead uses
`ArtifactObjectiveFactsV2` and `ArtifactObjectivePayloadV2`. Its immutable origin
names `origin_recommendation_request_event_id` and
`origin_recommendation_decision_event_id`, replacing the v1 user-event origin.
Replay requires the exact real Confirm and its correlated preparation result;
runtime never inserts a fake user message. Confirm continuing a v1 objective
preserves its v1 origin facts and codec; later transitions of a v2 objective
preserve v2. Execution origin is validated independently of objective origin.

`CollaborationHistoryProjection.artifact_objectives` exposes the retained
objectives; `offered_draft_sources` exposes exact owner-conversation visible
messages available as drafting context. Sources include story-specific direct
Lead judgments (including questions) and ordinary Lead synthesis messages.
Each `ArtifactDraftSource` binds the whole original strict-UTF-8 message digest,
including whitespace and line endings. It does not extract a draft fragment
or grant artifact evidence. An empty continuation selection retains the prior
selected sources. The source message is preserved unchanged. A separate
runtime notice shown after drafting is not included in that source digest.
The independently prepared artifact candidate may omit conversational framing;
there is no requirement to copy the whole source message into story content.

| Objective state | Meaning |
| --- | --- |
| `open` | Authoring remains outstanding through clarification, advice, consultation, review, rejection, staleness, and failure. |
| `satisfied` | A validated `APPLIED` or `UNCHANGED` result matches the exact bound candidate and reviewed-proposal lineage. |
| `cancelled` | An explicit cancellation closed this owner-conversation objective without proposing work. |

Null objective intent, prose completion, and a step rejection do not close an
objective. Commit does not satisfy it. Satisfaction remains recorded through
later Apply/Commit cycles. Complete same-session Lead context includes
objective facts across conversation switches and restart, while continuation
and cancellation remain restricted to the originating conversation.

Readable projection exposes the closed fields `artifact_objective.request`,
`artifact_objective.action`, and, when present,
`artifact_objective.next_step`. Full facts and bindings remain in the typed
payload and required session context. No projection reconstructs confirmation
authority from them. See the
[front-channel contract](runtime-api.md#contextual-authoring-objectives).

### Recommendation preparation events

The existing schema-v2 history envelope adds four typed event kinds and strict
versioned payloads:

| Event kind | Payload type | Meaning |
| --- | --- | --- |
| `lead_recommendation_request` | `lead_recommendation_request_v1` | Exact immutable preparation disclosure and final-tail request. |
| `lead_recommendation_decision` | `lead_recommendation_decision_v1` | Real structured Confirm, Reject, or Revise bound to that request. |
| `lead_recommendation_started` | `lead_recommendation_started_v1` | Correlated single preparation attempt origin; never a reusable capability. |
| `lead_recommendation_result` | `lead_recommendation_result_v1` | Known closure, distinct from artifact completion. |

`LeadRecommendationRequestPayload` contains `request_event_id`, `request_id`,
`action_id`, `action_revision`, `kind`, `purpose`, `instruction`,
`expected_result`, `target_paths`, `specialist_ids`, `objective_id`,
`source_recommendation_event_id`, `source_recommendation_digest`,
`draft_sources`, `basis`, `source_fingerprint`, `physical_calls_spent`,
`max_preparation_physical_calls`, `tail_sequence`, and `user_impact`.
The computed `action_digest` binds every field using canonical JSON. Optional
`authoring_scope` is keyword-only in the Python constructor. When present, its
canonical scope is bound into the digest. Legacy omitted scope remains omitted
in serialized payloads, digests, and session details; a pending unscoped request
is stale and needs fresh scoped planning before preparation can execute.

Four trailing optional fields bind upstream proposal provenance:
`source_proposal_id`, `source_proposal_revision`, `source_proposal_digest`,
and `source_execution_attempt_id`. The proposal triple is all-or-none; an
attempt requires the triple. Ordinary front-channel recommendations use all
nulls. Ordinary synthesis binds its exact proposal triple and execution
attempt. A recommendation produced by consultation Revise binds the old
proposal and actual Revise chain, with a null attempt. Mixed, foreign, or stale
correlation fails replay and the existing `LeadDecisionResult` validation.

`LeadRecommendedAction` is untrusted provider intent, not a separate persisted
authority payload. Runtime resolves `current_response` to the source
recommendation's exact visible message event and whole-message UTF-8 digest;
no fragment extraction, normalization, or alias is persisted as authority.
Other sources resolve only from offered inputs and draft events. The request
retains exact ordered source revisions, evidence, profiles, policies, target
fingerprint, and remaining preparation reservation. Historical `lead_judgment`
records are unchanged; a historical recommendation without its own request is
not promoted to an actionable recommendation.

The decision repeats the session/conversation, request event and tail sequence,
request ID, action ID/revision/digest, kind, and optional revision text. Confirm
or nonblank Revise is co-batched with its started event before provider work;
Reject or detected staleness is co-batched with terminal closure. The started
event also binds decision and execution-attempt IDs. Result states are
`completed`, `rejected`, `superseded`, `stale`, `failed`, or `interrupted`, with
applicable decision/start/Lead-result IDs or `superseding_user_event_id`.
`completed` means preparation ended, not that the artifact was applied.

`CollaborationHistoryProjection` exposes `current_recommendation_request`,
`current_recommendation_decision`, `current_recommendation_start`,
`current_recommendation_result`, and `current_recommendation_state`. Pending
requests select `submit_recommendation_decision`; known local closure work
selects `finalize_recommendation`. Ordinary new input atomically supersedes a
pending request. Repeated/concurrent decisions cannot consume it twice.
Uncertain submitted work remains `inspect_and_resolve_outcome`; corrupt or
unprovable closure requires recovery. Inspection creates no capability or
provider replay. See [recovery](../operations/recovery-and-limitations.md#recommendation-preparation-recovery).

Ordinary provider synthesis uses `lead-synthesis-v3` with nullable
`recommended_action`; it is mutually exclusive with `next_intent`. Runtime
persists the unchanged historical `CollaborationLeadSynthesisPayload` and a
separate recommendation request when an action is selected. Artifact-review
synthesis keeps its independent contract. Filesystem/SQLite storage, archive,
transfer, and pure replay use these exact records without rewriting historical
judgment, synthesis, or v1 objective payloads.

Readable history adds `lead_recommendation.purpose`, `.instruction`, `.result`,
`.impact`, `.decision`, `.revision`, `.state`, `.sources`, `.targets`, and
`.cost` fields. Required session context includes the exact recommendation
request, decision, start, and closure, including source and objective origins;
remembering those records grants no execution authority.

## Proposal closure and limits

`CollaborationProposal` discloses every one of these fields:

| Field | Meaning |
| --- | --- |
| `step_id`, `proposal_id`, `proposal_revision` | Lineage, proposal identity, and increasing revision |
| `step_kind`, `purpose` | Exact typed step and visible reason |
| `authoring_scope` | Versioned activity, targets, domains, interpretation, and task/revision; omitted in historical unscoped records |
| `assignments` | Ordered, unique specialist assignments with explicit live mode and review basis |
| `user_impact`, `side_effects` | Visible consequence and closed side-effect set |
| `budget` | Whole-lineage and whole-next-step physical-call reservation |
| `basis` | Route and policy fingerprints, participant profiles, closed input revisions, and exact lead evidence when applicable |
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
mode = "review"
review_basis = {"schema_version": 1, "candidate_input_ids": ["input_outline"], "criteria": "Check the supplied outline range."}
```

Every proposal input is content-free identity and revision evidence:

```text
input_id = "input_outline"
kind = "core_document"
locator = "${core_documents/global_outline.md}"
state = "present"
revision_digest = "sha256:1111...1111"
start_line = 3
end_line = 12
```

Input kinds are `user_input`, `conversation_event`, `core_document`,
`manuscript`, `agent_state`, `writing_artifact`, and `artifact_candidate`;
state is `present` or `absent`. A locator, optional inclusive line range, and
digest can identify the source revision. Ordinary inputs never carry file
bytes; an `artifact_candidate` assignment input deliberately adds the exact
content-bearing canonical candidate. Assignments' `input_ids` must
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

The event kinds are `message`, `lead_outcome_failure`,
`assignment_dispatch`, `specialist_reply`, `lead_synthesis`, `step_state`,
`confirmation`, `execution_result`, `interruption`, `terminal_outcome`, and
`provider_call_accounting`, plus `artifact_review_candidate`,
`artifact_review_synthesis`, `artifact_reviewed_proposal`,
`artifact_proposal_state`, and `artifact_review_failure`; the separate effect
events are `artifact_application_request`, `artifact_application_decision`,
`artifact_application_result`, `story_commit_request`, `story_commit_decision`,
and `story_commit_result`. `artifact_objective` records contextual authoring
transitions. Each stored
event carries schema,
event/session/conversation/turn/step identity, contiguous sequence, canonical
UTC timestamp, atomic-batch metadata, typed actors, and its kind-specific
payload. Visible message and reply content is capped at 32,768 strict-UTF-8
bytes and cannot contain surrogate code points. A lead-authored message uses a
typed visible kind/message plus exact `CollaborationLeadEvidence`. That
evidence holds only ordered receipts for attached references, canonical-core
statuses, complete zero-hit searches, or final-answer-used READs. A
`lead_outcome_failure` instead closes one read-only Lead attempt without a
usable result. Its closed codes are `evidence_validation_rejected`,
`artifact_intent_rejected`, `provider_result_rejected`, `request_not_submitted`,
`provider_failed`, `provider_timed_out`, `cancelled`, and `interrupted`.
`invocation_id` and `call_id` must both be null only for
`request_not_submitted`, which requires one exact user request with no provider
accounting. Every other code requires both exact non-null call identities;
`provider_timed_out` requires submitted accounting. A closure preserves the
original user event and never creates a successful judgment or execution
result. Payloads contain visible facts, not hidden reasoning.

Agent-authored visible messages, Lead judgments, specialist replies, and Lead
syntheses persist an optional `trace` object with exactly `role`, `action`,
`context_sources`, `recovered_fields`, and `origin`. These are bounded labels
derived by runtime after provider validation; no prompt, retrieved content,
reply prose, hidden reasoning, or authority is copied into the trace. A newly
opened event store decodes the same trace, allowing terminal history to show
how the reply was constructed. `runtime_fallback` always reports
`action=none`.

`provider_call_accounting` contains no request or response content. It records
one invocation/call identity, contiguous ordinal, canonical agent role,
invocation kind (`lead`, `specialist_assignment`, `lead_synthesis`, or
`specialist_advisory`), physical-call position
(`reference_prelude`, `initial`, or `continuation`), and state (`prepared` or
`submitted`). PREPARED records intent before a provider boundary; only the
matching unique SUBMITTED record spends one of the twelve lineage calls.
Submitted failures, timeouts, and cancellations still count.

A running attempt dispatches assignments in proposal order. Every reply binds
the exact proposal ID/revision/digest, execution attempt, assignment, and
dispatch event. Each provider-facing invocation records a terminal outcome with
its scope, submitted-call count, and assignment ID where applicable.

A live specialist reply also carries its exact assignment mode. Draft mode
returns `drafted` or `insufficient_evidence`; review mode returns `supports`,
`concern`, `conflict`, or `insufficient_evidence`, plus a visible message and
the exact ordered assignment input IDs. Drafted material is neither support
nor final candidate review. A `lead_synthesis` payload binds every successful
reply event in order, preserves required unresolved input IDs from every
concern/conflict/insufficient-evidence reply, and optionally carries one strict
same-lineage round-two intent. It separately preserves the proposal's exact
`authoring_scope` and story-specific evidence;
specialist replies remain attribution rather than replacement story evidence.
Synthesis is absent after any assignment failure and for direct
advisories.

The execution result then correlates all proposal assignments in their
original order and contains:

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

## Readable record and field projection

`project_collaboration_readable_history(events)` first performs the same full
canonical-history validation as `project_collaboration_history()`. Schema-v1
events, partial batches, sequence gaps, duplicate identities, digest drift,
illegal transitions, and any other corrupt v2 history fail before it returns a
record. The function is pure and returns an exact tuple in durable sequence
order.

There is exactly one `CollaborationReadableRecord` per event. It repeats every
canonical event fact—schema, session/conversation/turn/step/event identity,
sequence and timestamp, batch identity/ordinal/size/digest, typed sender and
recipient, derived direction, event kind, and the complete typed payload—then
adds a deterministic tuple of searchable public fields. The record is a view;
it does not replace or rewrite the event.

`CollaborationReadableField` contains an exact `key` and original `text`, plus
a zero-based `assignment_ordinal` or `input_ordinal` only when the field is
nested at that coordinate. The closed field keys, in deterministic field
order, are:

```text
message.content
lead_outcome.failure
specialist_reply.content
specialist_reply.assessment
lead_synthesis.message
proposal.purpose
proposal.user_impact
proposal.assignment.instruction
proposal.input.locator
proposal.input.range
proposal.call_usage
proposal.assignment.call_limit
proposal.synthesis.call_limit
dispatch.instruction
dispatch.call_limit
interruption.content
step.state
confirmation.decision
execution.outcome
execution.assignment.outcome
execution.assignment.call_usage
execution.synthesis.outcome
execution.synthesis.call_usage
terminal.scope
terminal.outcome
terminal.call_usage
artifact_review.decision
artifact_review.message
artifact_proposal.state
artifact_review.failure
```

Proposal ranges render as `whole` or `lines:START-END`; call fields use fixed
`name=value` text. The field set exposes visible content and public facts only.
It adds no authority, capability, provider-session, hidden diagnostic,
profile/input revision digest, or private reasoning field. The complete typed
payload remains available on the record for faithful inspection, while search
is limited to this closed field set.

## Search, citations, pages, and derived identity

The pure direct module `story_writing_agents.conversation_search` owns the v2
read APIs. Search validates and projects the complete history, then applies the
existing literal normalization: strict UTF-8 NFKC, case folding, category-C
replacement, and whitespace collapse. Query input itself must be nonblank, no
more than 200 characters, and contain no category-C character. Search is not
regex, fuzzy, or semantic.

Every returned `CollaborationHistoryCitation` binds session, conversation,
event ID, one-based sequence, field key, optional zero-based nested ordinal,
and a zero-based start-inclusive/end-exclusive Unicode codepoint range in the
original field. Case-fold expansions, compatibility characters, combining
sequences, control replacement, and collapsed whitespace are mapped back to
the exact original range. `resolve_collaboration_citation(events, citation)`
revalidates the current complete history and returns that original substring;
stale event, identity, field, ordinal, or range facts fail closed.

One field contributes at most its first normalized-literal match. Hits sort by
event sequence, closed field-key order, assignment ordinal, then input ordinal.
Search returns at most 10 hits by default and 50 when explicitly requested,
scans at most 10,000 records, and bounds each normalized snippet to 240
characters and 1,024 UTF-8 bytes. `scan_truncated` distinguishes scan
exhaustion; `truncated` also covers the hit limit.

`page_collaboration_history()` returns whole readable records from an inclusive
sequence range. It defaults to 24 records; the maximum is 100 records and
32,768 aggregate readable-field UTF-8 bytes. A continuation cursor retains the
range and page limits and binds both `snapshot_last_sequence` and
`snapshot_tail_batch_digest`. `continue_collaboration_history()` rejects an
append, rewritten tail digest, changed identity, or other drift rather than
mixing snapshots. An oversized first record fails instead of being split.

`collaboration_readable_document_id()` returns a 71-character
`sha256:`-prefixed derived identity for one record field. SHA-256 input
length-frames the complete canonical encoded event, followed by the fixed
readable-source schema ID, collaboration schema version, workspace identity,
session/conversation identity, positive metadata revision, event ID/sequence,
batch digest, field key, optional ordinals, and exact readable text bytes. The
fixed source schema ID is
`story-collaboration-readable-event-field-v2`. Changing any bound fact changes
the ID. This is only a derived lookup/context-source identity: it is never an
event ID, append receipt, confirmation capability, state/recovery fact, or
authorization to reread or execute anything.

See [Conversation search](conversation-search.md#schema-v2-readable-collaboration-history)
for signatures, page/search limits, and focused examples.

## State projection versus bounded provider context

These similarly named values have different jobs:

| Contract | Answers | Contains by default | Authority |
| --- | --- | --- | --- |
| `CollaborationHistoryProjection` from `project_collaboration_history()` or a direct `CollaborationEventStore.read_context()` | What proposal/state/decision/attempt/result is current, and what action is safe to consider? | State and recovery summary | Evidence only |
| `tuple[CollaborationReadableRecord, ...]` from `project_collaboration_readable_history()` | What complete typed public events and closed searchable fields exist? | Every validated event record | Evidence only |
| `CollaborationReadableContext` from `select_bounded_collaboration_context()` | Which explicitly named records fit a bounded provider-context budget? | Content-free `CollaborationContextBootstrap`; zero records | Evidence only |

The bootstrap contains schema version, optional history identity, record count,
last sequence, and tail batch digest. Only unique, known `event_ids` explicitly
supplied by the caller can add records. Selection restores authoritative
sequence order regardless of request order, retains each record atomically,
and caps the result at 24 records and 32,768 readable-field UTF-8 bytes.
Requested/retained/omitted counts and `truncated` report the result.

The selector has no `current_input` parameter. The current user input remains
separate, and the selected value still does not invoke a provider or establish
retrieval authorization. The lead runtime explicitly opts its focused source
adapter into a bounded searchable-history snapshot over active same-session
conversations; it still sends zero eager history records and adds bodies only
after SEARCH and READ. Confirmed specialist and synthesis invocations use the
same bounded component with assignment-local source restrictions. #454 owns
whole-workspace unsupported-history rejection.

Required Lead session memory is a separate complete retained-history
projection. It includes objective facts and current directives/core bytes
across active and archived conversations even when the bounded selector sends
zero records. It supplies context without current artifact receipts or
confirmation authority; see [Lead session memory](../guides/agent-context.md#lead-session-memory).

## Atomic batch flow

The storage-neutral history is an ordered sequence of nonempty batches. One
batch is restricted to one session conversation and turn, contains unique event
IDs, and compares `expected_last_sequence` with the authoritative tail.
Storage assigns contiguous sequences, canonical UTC timestamps, ordinal/size,
and the digest of the exact caller-owned batch.

```text
user message
    -> provider_call_accounting PREPARED/SUBMITTED pairs
    -> evidence-bound lead message
       + optional proposal + awaiting_confirmation (request is last)
       OR lead_outcome_failure -> retry_lead_turn (no proposal state)
    -> structured decision + confirmed/rejected/superseded
    -> running + every assignment_dispatch in proposal order
    -> replies + optional lead_synthesis + every terminal_outcome
       + execution_result (last)
    -> visible terminal notice + completed/failed/interrupted
       OR completed round-one notice + revision-2 proposal
          + awaiting_confirmation

running + interruption + outcome_unknown

pending proposal + ordinary user message + superseded (one batch)
    -> next lead provider call on a new step
```

Evidence IDs are valid only for their deterministic lead operation/result
correlation. Projection rejects a successful outcome from another invocation
even when its receipts are internally consistent. The failure event closes the
exact submitted call; later recovery starts a fresh turn rather than reusing
its provider output.

The paired facts above must be co-batched. An interrupted finalization also
co-batches its interruption record. A store must expose only the entire batch
or none of it; a partial stored batch makes the history corrupt.

`CollaborationEventStore` is the sole store port:

```python
append_batch(batch: CollaborationEventBatch) -> CollaborationAppendReceipt
read(session_id: str, conversation_id: str) -> tuple[CollaborationEvent, ...]
read_context(session_id: str, conversation_id: str) -> CollaborationHistoryProjection
```

The protocol remains storage-neutral. Direct Python callers can implement it
themselves or use
`story_writing_agents.conversation_storage.filesystem.FilesystemCollaborationEventStore`
or
`story_writing_agents.conversation_storage.sqlite.SQLiteCollaborationEventStore`.
Both classes are direct-module APIs, not `conversation_storage`, `workspace`,
or project-root exports. The selected composition places one of them behind
the storage-neutral service bundle. A v2 store decides
whether an atomically committed candidate is `APPENDED` or an exact retry is
`ALREADY_COMMITTED`; exact retry returns the original sequence range and
timestamps before current-tail CAS is evaluated.

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

### Direct filesystem storage and retry

The adapter owns two v2-only siblings below the existing catalog conversation:

```text
discussion_logs/SESSION/interactive/conversations/CONVERSATION/
├── metadata.json
├── transcript.jsonl                 # unsupported sentinel; active owner rejects it
├── collaboration-history.jsonl      # strict canonical schema-v2 JSONL
└── .collaboration-history.lock      # persistent advisory guard
```

Construction retains the workspace and catalog without filesystem I/O. After
catalog identity succeeds, an absent v2 history reads as empty and creates
neither v2 file. A present history is decoded one canonical event per line with
exactly one final LF, correlated to its path, and fully projected before it is
returned. Empty-present, malformed, noncanonical, schema-v1, incomplete-batch,
identity-mismatched, or invalid-transition bytes raise `WorkspaceFormatError`.
An append against that corruption instead raises
`CollaborationAppendError(CORRUPT_HISTORY)` and does not rewrite the file.

Given a caller-constructed canonical `CollaborationEventBatch` named `batch`,
the direct call and externally visible results are:

```python
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)

store = FilesystemCollaborationEventStore(workspace, catalog=catalog)

first = store.append_batch(batch)
events = store.read(batch.events[0].session_id, batch.events[0].conversation_id)
state = store.read_context(
    batch.events[0].session_id,
    batch.events[0].conversation_id,
)
retry = store.append_batch(batch)

output = {
    "first": first.status.value,
    "range": (first.first_sequence, first.last_sequence),
    "stored_event_ids": tuple(event.event_id for event in events),
    "state": state.current_state.value,
    "retry": retry.status.value,
    "same_timestamps": retry.timestamps == first.timestamps,
}
```

For a two-event proposal/awaiting-confirmation batch at an empty tail, the
observable shape is:

```python
{
    "first": "APPENDED",
    "range": (1, 2),
    "stored_event_ids": ("proposal_event", "request_event"),
    "state": "awaiting_confirmation",
    "retry": "ALREADY_COMMITTED",
    "same_timestamps": True,
}
```

The adapter holds a per-history process lock and blocking POSIX exclusive
advisory lock across validation and publication. It serializes the complete
canonical history to a same-directory mode-`0600` temporary file, flushes and
file-fsyncs it, atomically replaces the history, then mandatorily fsyncs the
parent directory. Only then does it return `APPENDED`. A failure before replace
leaves the old history. A failure after replace reports operational uncertainty
even though complete new bytes may be visible; retry the exact same batch ID
and content so a visible commit resolves to its original
`ALREADY_COMMITTED` receipt without duplication.

The history contains plaintext user-visible collaboration facts. A newly
published history and newly created guard request owner-only `0600` modes, but
the adapter is not encryption, workspace access control, backup, retention, or
permission repair. Do not hand-edit, reformat, or copy schema-v1 transcript
bytes into the v2 file. After an operationally uncertain append, preserve the
workspace and retry only the exact batch. After `corrupt_history`, stop normal
appends and preserve the bytes for diagnosis; this child supplies no repair,
conversion or repair workflow.

### Direct SQLite storage and retry

`SQLiteCollaborationEventStore` uses the existing fixed main-database path:

```text
discussion_logs/conversation_storage.sqlite3
```

A newly provisioned database is exact SQLite schema version `2`: its 14
application objects retain the v1 catalog/transcript tables and add the
`collaboration_events` table, two uniqueness indexes, and one contiguous-insert
trigger. Existing schema-v1 databases are rejected unchanged. There is no
in-place `ALTER`, converter, compatibility mode, or automatic upgrade.

Construction performs no I/O and has the same coordinator options as the
other SQLite adapters:

```python
SQLiteCollaborationEventStore(
    workspace: Workspace | str | os.PathLike[str],
    *,
    mutation_coordinator: ConversationStorageMutationCoordinator | None = None,
    mutation_timeout_seconds: float = 5.0,
)
```

Assuming the schema-v2 database is provisioned, its v1 catalog contains the
addressed conversation, and `batch` is the two-event value from the example
below, a direct call has this observable result:

```python
from story_writing_agents.conversation_storage.sqlite import (
    SQLiteCollaborationEventStore,
)

store = SQLiteCollaborationEventStore(workspace)
first = store.append_batch(batch)
retry = store.append_batch(batch)
events = store.read("session_a", conversation_id)
state = store.read_context("session_a", conversation_id)

output = {
    "first": first.status.value,
    "range": (first.first_sequence, first.last_sequence),
    "retry": retry.status.value,
    "same_receipt": retry.timestamps == first.timestamps,
    "event_ids": tuple(event.event_id for event in events),
    "state": state.current_state.value,
}
```

```python
{
    "first": "APPENDED",
    "range": (1, 2),
    "retry": "ALREADY_COMMITTED",
    "same_receipt": True,
    "event_ids": ("proposal_event", "request_event"),
    "state": "awaiting_confirmation",
}
```

Append borrows the workspace mutation coordinator and uses one
`BEGIN IMMEDIATE` transaction. It validates the complete addressed v2 history,
applies exact-retry and conflict precedence, inserts every event or none, and
returns `APPENDED` only after commit and final database/path checks. A failure
before commit rolls back. A commit can become durable before a later check
reports an operational failure; after that uncertainty, preserve the database
and retry the exact same batch ID and content. A visible commit then resolves
to its original `ALREADY_COMMITTED` receipt instead of duplicating events.
Independent threads and processes serialize through the coordinator and
SQLite transaction: identical same-tail candidates produce one `APPENDED` and
one `ALREADY_COMMITTED`, while distinct candidates produce one `APPENDED` and
one `stale_sequence`.

Each row stores the canonical public collaboration event as a plaintext BLOB
and repeats only the facts needed for validation and uniqueness. It stores no
hidden reasoning, provider diagnostics, credentials, or referenced file bytes.
The main database uses UTF-8, DELETE journal mode, `synchronous=FULL`, foreign
keys, a five-second busy timeout, contained path checks, and owner-only mode on
fresh provisioning. This is not encryption, backup, permission repair,
retention, or secure erasure.

Malformed collaboration rows make direct `read()`/`read_context()` fail with
`WorkspaceFormatError`; append maps addressed-history corruption to
`CollaborationAppendError(CORRUPT_HISTORY)`. Operational availability,
coordination, path-security, and unsupported-version failures remain separate
public categories. Preserve the database and stop normal work after
corruption; this adapter repairs nothing. In the active SQLite bundle it is the
authoritative store behind history, event-field search, transcript paging, and
state inspection. FTS remains only a non-authoritative candidate accelerator.

## State and user-action contract

These are the complete and exact states. “Next action” names the
`CollaborationHistoryProjection.next_safe_action` value; it is evidence for an
application, never permission to perform the action.

| State | Internal meaning | External user meaning | Allowed next action |
| --- | --- | --- | --- |
| `awaiting_confirmation` | A complete proposal and fresh request are the durable tail | A proposal is waiting for your confirmation. | `submit_confirmation`: Review the proposal and submit a confirmation decision. |
| `reconfirmation_required` | Previously confirmed proposal is retained, but fresh authority is required | The earlier confirmation is no longer sufficient; review and confirm the updated proposal. | `submit_confirmation`: Review the proposal and submit a confirmation decision. |
| `confirmed` | One exact structured confirm decision is durable; the live runtime normally crosses immediately into `running` | You confirmed the proposal; the runtime is starting the disclosed work. | `start_or_reconfirm`: Start with the live single-use authority, or reconfirm after that authority is lost. |
| `running` | One execution attempt and every ordered dispatch are durable | The confirmed work is currently being executed. | `none` while no result exists; with a result, `finalize_result`: Record the terminal result for the running work. |
| `completed` | Known successful result and visible terminal notice are durable | The confirmed work completed and its result is recorded. | `none`: No history-derived action is pending. |
| `rejected` | Structured reject decision is durable; no work started | You rejected the proposal; that work will not execute. | `continue_with_lead`: Continue the conversation with the lead agent. |
| `superseded` | Structured revise decision retired this proposal | A newer proposal replaced this proposal. | `continue_with_lead`: Continue the conversation with the lead agent. |
| `interrupted` | Known cancelled execution result and notice are durable | Execution stopped before a complete outcome was recorded. | `continue_with_lead`: Continue the conversation with the lead agent. |
| `failed` | Known failed execution result and notice are durable | Execution failed and the failure outcome is recorded. | `continue_with_lead`: Continue the conversation with the lead agent. |
| `outcome_unknown` | Restart/interruption left no authoritative result for a submitted attempt | Execution may have occurred, but its outcome cannot be proven. | `inspect_and_resolve_outcome`: Inspect durable evidence and resolve the uncertain outcome. |
| `recovery_required` | Authoritative history needs external repair | History requires operator inspection before any safe continuation. | `repair_authoritative_history`: Repair authoritative history before continuing. |

Legal state transitions are deliberately narrower than conversational intent.
In particular, only `confirmed` can enter `running`; `outcome_unknown` can only
enter `recovery_required`; and `recovery_required` is terminal in this contract.

Artifact proposal state is projected separately and correlated to the current
candidate and consultation:

| Artifact state | Durable meaning |
| --- | --- |
| `awaiting_consultation_confirmation` | Exact candidate/reviewers/request are disclosed; review has not started. |
| `reviewing` | One correlated confirmed review attempt is running or awaiting finalization. |
| `reviewed` | Complete review plus immutable zero-authority reviewed proposal is durable. |
| `revision_required` | Complete synthesis selected `revise`; no reviewed proposal exists. |
| `rejected` | User or lead rejected this candidate; no reviewed proposal exists. |
| `superseded` | A replacement turn/candidate permanently retired this candidate. |
| `stale` | Evidence or complete base state drift prevented safe review/finalization. |
| `failed` | Review or synthesis did not close successfully. |
| `recovery_required` | Artifact-review history requires external repair. |

The readable projection intentionally exposes only artifact review message,
decision, state, and closed failure code. Candidate bytes and complete reviewed
proposal are present in the typed event payload/history codec, but they are not
search fields and are not automatically selected into bounded provider
context.

`retry_lead_turn` is a next action, not a new durable step state. It covers
eligible read-only Lead failures and rejected results closed by
`lead_outcome_failure`, and exact legacy unstarted, prepared-only, or unmatched
submitted attempts awaiting closure. Projection and reads remain pure. Only
explicit new user input lets runtime close a pending attempt under the session
and conversation leases, re-read its closure, then append a fresh turn in the
same conversation. Restart does not change this behavior.

The failure result is a recommendation without new proposal, confirmation
request, or evidence. If a Revise attempt fails, its old proposal remains
superseded and no replacement authority is created. Successful results,
uncertain specialist/synthesis work, and pending or uncertain Apply/Commit
outcomes are excluded from read-only abandonment. A closure that cannot be
appended safely returns `recovery_required`.

Known read-only failures after references may expose retry once the exact
attempt and its reference accounting are durably closed. An open authoring
objective remains unchanged. Uncertain reference cleanup or failed closure
keeps the recovery gate; new input acquires fresh evidence and never replays
the failed call automatically.

Apply and Commit requests/results project independently of consultation state.
While either exact decision is pending, ordinary Lead/advisory input rediscloses
the same request without appending history or calling a provider. Known
terminal Apply/Commit outcomes permit later cycles with fresh requests and
preserved earlier correlations. `outcome_unknown` and `recovery_required`
continue to block new work.

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

## Routing history compatibility

AuthoringScope and ReviewBasis each use schema 1. Collaboration event schema 2
and existing historical bytes/digests remain compatible: absent optional scope,
mode, and review-basis fields are omitted when re-encoded. Strict current
provider contracts require the routing fields; old pending unscoped proposals
must be regenerated with fresh scope and confirmation before execution.

Current tasks derive from independently validated events and the active
same-session conversation catalog. An explicit selected task retains cumulative
domains/targets, drafting obligations, and its current revision across
conversations. Archived history remains readable context, without routing or
confirmation authority. Applied revisions are excluded from offered tasks.
This is a projection of existing durable facts, not a new task store.

`completed` describes a consultation round. It does not mean the authoring
objective is complete, a drafted message was reviewed, or Apply is authorized.
All required current-task drafts precede exact final-candidate review by every
required specialist, followed by a separate Apply decision. A fresh finished
user-supplied candidate can enter review without a fabricated draft round;
an existing task with DRAFT/REVISE history still requires its draft provenance,
even when the current request says REVIEW. Selecting a prior Lead message on a
fresh REVIEW task does not by itself create a drafting obligation. Task/revision coverage
tracks current specialist work across active conversations; an objective
remains owned by its originating conversation and stays open until its exact
successful Apply or explicit cancellation.

## Python value, wire, and projection example

This example uses only the implemented `story_writing_agents.conversations`
facade. It performs no I/O or provider call. The manual storage-metadata step is
only an in-memory illustration; either direct persistence adapter owns that
step when a caller chooses to store the batch. It deliberately illustrates
a historical unscoped proposal and its unchanged codec. It is not a live
execution proposal; new callers must supply the scope/mode/basis described
above and use runtime confirmation.

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

## Python readable/search/page/context example

Continuing with the validated two-event `history` above, this uses every #474
operation. The structured dictionaries show the significant output facts; the
functions themselves return the immutable typed values documented here.

```python
from story_writing_agents.conversation_search import (
    CollaborationHistorySearchQuery,
    collaboration_readable_document_id,
    continue_collaboration_history,
    page_collaboration_history,
    resolve_collaboration_citation,
    search_collaboration_history,
)
from story_writing_agents.conversations import (
    CollaborationReadableContextPolicy,
    project_collaboration_readable_history,
    select_bounded_collaboration_context,
)

records = project_collaboration_readable_history(history)
project_output = {
    "record_count": len(records),
    "first_event": records[0].event_id,
    "first_kind": records[0].kind.value,
    "payload_type": type(records[0].payload).__name__,
    "first_field": records[0].fields[0].key.value,
}
assert project_output == {
    "record_count": 2,
    "first_event": "proposal_event",
    "first_kind": "message",
    "payload_type": "CollaborationProposal",
    "first_field": "proposal.purpose",
}

result = search_collaboration_history(
    history,
    CollaborationHistorySearchQuery(text="OUTLINE RANGE", limit=1),
)
hit = result.hits[0]
resolved = resolve_collaboration_citation(history, hit.citation)
search_output = {
    "normalized_query": result.normalized_query,
    "event_id": hit.citation.event_id,
    "field": hit.citation.field_key.value,
    "codepoints": (
        hit.citation.start_codepoint,
        hit.citation.end_codepoint,
    ),
    "resolved_original": resolved,
}
assert search_output == {
    "normalized_query": "outline range",
    "event_id": "proposal_event",
    "field": "proposal.purpose",
    "codepoints": (37, 50),
    "resolved_original": "outline range",
}

first_page = page_collaboration_history(history, max_records=1)
assert [record.event_id for record in first_page.records] == ["proposal_event"]
assert first_page.snapshot_last_sequence == 2
assert first_page.next_cursor is not None
second_page = continue_collaboration_history(history, first_page.next_cursor)
assert [record.event_id for record in second_page.records] == ["request_event"]

default_context = select_bounded_collaboration_context(history)
assert default_context.records == ()
assert default_context.bootstrap.total_records == 2
assert default_context.bootstrap.last_sequence == 2

selected_context = select_bounded_collaboration_context(
    history,
    event_ids=(hit.citation.event_id,),
    policy=CollaborationReadableContextPolicy(
        max_records=1,
        max_utf8_bytes=32_768,
    ),
)
context_output = {
    "requested": selected_context.requested_records,
    "retained": selected_context.retained_records,
    "omitted": selected_context.omitted_records,
    "event_ids": tuple(record.event_id for record in selected_context.records),
    "truncated": selected_context.truncated,
}
assert context_output == {
    "requested": 1,
    "retained": 1,
    "omitted": 0,
    "event_ids": ("proposal_event",),
    "truncated": False,
}

document_id = collaboration_readable_document_id(
    workspace_id="workspace_a",
    metadata_revision=1,
    record=records[0],
    field=records[0].fields[0],
)
document_output = {
    "schema_id": "story-collaboration-readable-event-field-v2",
    "prefix": document_id[:7],
    "length": len(document_id),
}
assert document_output == {
    "schema_id": "story-collaboration-readable-event-field-v2",
    "prefix": "sha256:",
    "length": 71,
}
```

Appending to or changing `history` before the continuation makes the cursor
stale. Changing an event/field coordinate makes the citation or document input
different or invalid. These examples call no store and no provider.

## Privacy, authority, and non-goals

Canonical v2 history may contain user-visible proposal text, instructions,
messages, specialist replies, notices, and interruption text. Applications must
treat those values as sensitive conversation data and apply their own storage,
access, retention, and deletion policy. File inputs contribute locator, range,
state, and digest only. Artifact candidate and reviewed-proposal payloads are a
deliberate exception: they contain the exact proposed artifact text/change set
needed to prove that every reviewer saw the same candidate. Provider
credentials, capabilities, raw provider protocol bodies, hidden reasoning, and
reconstructed authorization do not belong in any payload.

The direct filesystem and SQLite adapters define their v2 persistence and
durability boundaries above. Archive schema 2 and backend transfer consume
their canonical histories. Runtime composes provider calls from these values,
but this contract still defines no artifact/Git executor, credential model, complete
workspace backup, or recovery tool. The installed shell wires only
provider-free readable records, search, pages, and state inspection. The Python
runtime owns lead proposal/confirmation and confirmed specialist/synthesis
behavior; #454 owns whole-workspace rejection. See the dedicated
[lead-agent guide](../guides/lead-agent.md),
[conversation transcripts](conversation-transcripts.md) for the active readable
path, [persistence](../design/persistence.md) for authority ownership,
[filesystem safety](../operations/filesystem-safety.md#schema-v2-collaboration-history-safety)
for failure handling, and
[package API](package-api.md#structured-collaboration-history-boundary) for the
supported import boundary.
