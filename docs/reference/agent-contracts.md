# Agent contracts

> **Availability:** these models are Python APIs. Debate requests can carry an
> optional conversation snapshot; one-step and retrieval turns use separate,
> compatible request families. Content-free explicit-reference read contracts
> remain available at the direct repository/backend boundary. Python
> `StoryRuntime.lead_turn()` constructs them for lead references; confirmed
> consultations construct a fresh read contract for each specialist and lead
> synthesis invocation. A lead artifact intent is resolved by runtime into one
> exact immutable candidate; confirmed reviewers receive that same candidate,
> and the resulting reviewed proposal grants no write or Git authority.

Key terms: **definition**—stable context bound to one agent; **request**—dynamic
data for one provider call; **blackboard**—public debate messages;
**conversation context**—a transcript snapshot separate from debate input.

## Bound definition

```python
PromptFileReference(path: pathlib.Path, sha256: str, content: bytes)
PromptFileReference.from_path(path: pathlib.Path) -> PromptFileReference
PromptFileReference.from_bytes(path: pathlib.Path, content: bytes) -> PromptFileReference
PromptFileReference.read_text() -> str
AgentDefinition(agent_id: str, backend: str, fingerprint: str,
    system_prompt_file: PromptFileReference,
    persona_file: PromptFileReference, language: str,
    context_document_files: tuple[PromptFileReference, ...],
    provider_managed_context: bool = False,
    behavior_policy_components: tuple[PromptFileReference, ...] = (),
    runtime_behavior_policy: bytes = b"")
AgentDefinition.file_references() -> tuple[PromptFileReference, ...]
```

References are frozen content snapshots. Construction validates strict UTF-8
and requires the SHA-256 to match the captured bytes; `content` is omitted from
their representation. `path` is a lexical source identity, not continuing read
authority: `read_text()` decodes the captured bytes, and provider binding does
not reread the path. A built-in definition's fingerprint covers the agent,
backend, language, ordered file-component identities/hashes, and runtime policy
bytes. Runtime bindings are cached by
`(agent_id, fingerprint)`.

Built-in `system_prompt_file` contains effective composed bytes: common policy,
specialist-family policy where applicable, role policy with the Lead contract
overlay for the Lead, then runtime routing policy v1. Its path retains the
role-policy source identity; its content/hash
is not the on-disk role file alone. `behavior_policy_components` captures the
ordered captured policies, with the Lead component including its in-memory
contract overlay; `runtime_behavior_policy` is a separate trailing UTF-8 byte
value, not a file component. The runtime preserves workspace files.
Custom definitions may omit both trailing fields for compatibility; a nonempty
runtime policy requires components and an exactly matching composed prompt.

Collaboration proposals persist both a definition fingerprint and a separate
content-free execution-profile fingerprint. The latter binds the selected
backend/provider execution profile without embedding credentials, prompt
bytes, model output, or provider-native state. Confirm rederives both; drift
rejects execution instead of silently changing the disclosed participant.

| Agent | Contextual P2 core sources in the built-in runtime |
| --- | --- |
| `lead_orchestrator` | all four |
| `plot_architect` | global and part outlines |
| `character_custodian` | character sheets |
| `execution_specialist` | narrative rules |

Built-in definitions set `provider_managed_context=True` and
`context_document_files=()`. The table describes contextual source eligibility,
not permanent binding bytes. A direct custom definition may leave the flag
false and eagerly bind explicit content-backed references; that compatibility
path preserves its prior fingerprint and adapter behavior. Exact source and
publication rules are in
[Canonical writing artifacts](writing-artifacts.md).

For current built-in core context, source capture and the manifest/planner—not
prompt assembly—read the role-authorized current snapshot. A manual core change
therefore changes the source revision/delta and can force bootstrap; it does not
change the stable definition fingerprint. Role-prompt and persona references
remain content-backed at definition construction.

The runtime appends shared agent JSON guidance to every provider-bound role.
The current built-in wire is `agent-provider-wire-v10`; the separate result
contracts are `lead-response-v7` (fresh scope3), `lead-response-v6` (explicit
legacy scope1/2), `specialist-assignment-response-v3` (draft/review),
`specialist-assignment-response-v4` (constraints), `consultation-summary-v4`,
and `artifact-review-summary-v4`. Per-call guidance
and schema descriptions explain the actual fields and allowed values. See the
[field guide](provider-wire.md).

<a id="explicit-reference-read-contracts"></a>

## Explicit-reference read contracts

Import the public values from `story_writing_agents.writing_artifacts`:

```python
ReferenceInvocationCorrelation(
    operation_id: str, invocation_id: str, invocation_ordinal: int,
    agent_id: str, phase: str, round_number: int | None, attempt: int,
)
ReferenceReadRequest(
    correlation: ReferenceInvocationCorrelation,
    references: tuple[ArtifactReference, ...],
)
ReferenceReadAction(
    kind: ReferenceReadActionKind,
    correlation: ReferenceInvocationCorrelation,
    references: tuple[ArtifactReference, ...],
)
ReferenceReadActionKind.READ_WRITING_ARTIFACTS
```

The request and action are frozen, slotted, nonempty, ordered, and
content-free. They contain only complete invocation correlation and canonical
path/range indicators. The action kind is fixed, and an acceptable action must
equal its request's complete correlation and reference tuple exactly.

The module-supported synchronous `ReferenceReadInvocation` protocol and its
closeable, content-bearing `ReferenceReadResult` are backend/repository
collaborators rather than subpackage exports. A repository-created coordinator
owns the private one-use capability and exposes only its content-free request
to the backend. References remain a separate keyword-only backend argument;
they are not fields of `AgentRequest`, `ConversationRequest`, or
`ConversationRetrievalRequest`, and are not concatenated into task, user,
candidate, messages, agent state, conversation history, or retrieval
observations. See the
[backend boundary](backend-and-sessions.md#referenced-invocation-boundary).

For confirmed collaboration, an assignment receives exactly its ordered input
IDs and a fresh reference operation for the artifact subset among them. Its
writing-artifact source adapter has an immutable allowed-path set, so inventory,
SEARCH, and READ cannot borrow sibling-only evidence. Synthesis is a separate
lead invocation with the direct story-artifact inputs and all typed replies.

## Typed context action contract

The provider wire is `agent-provider-wire-v10`; its complete
[field guide](provider-wire.md) covers contextual and direct calls. A contextual
request supplies named controls in `response_contract`. The closed response root
is `agent_action`, with one schema-permitted `action_type`:

- `search_context`: `queries` contain `search_text` and `source_types`; the
  artifact-only branch may carry `artifact_directory_prefix`.
- `read_context`: `read_handles` select capabilities issued by SEARCH.
- `submit_result`: `task_result` is the invocation's semantic result object.

The runtime rebuilds action branches and limits for each call and derives
correlation, action index, attribution, and read receipts itself. No JSON-encoded
result string or alternate-name decoder is accepted. All built-in result schemas
use explicit `response_type`; custom result contracts retain their declared schema.

Response alternatives use the identifiers offered by the current contract. A
unique `schema_id`, or `schema_id` with a declared `variant_id` for a shared
family, selects one alternative; optional-field presence does not select it.
The runtime prepares these lookups per immutable schema owner and validates the
selected alternative without trying unrelated alternatives after a failure.

Provider-facing schemas remain closed. Production consumption ignores unknown
response-record properties and excludes them from normalized values passed to
typed decoding, including nested records. Required fields and declared values
remain validated. Semantic maps retain their declared identity rules:
an unoffered source or specialist ID is not an ignorable record extension.
Strict JSON parsing, whole-response limits, schema pairing, current-call binding,
domain checks, and authorization still apply.

Each provider-bound role receives common protocol instructions. Each call supplies
`contract_guidance` for its actual inputs and response, and built-in output schemas
carry field and enum descriptions. Guidance and schema bytes are accounted as part
of the request. Runtime-owned contracts fail construction if required guidance is
missing or inconsistent.

## Lead judgment and evidence contracts

Import the public values from `story_writing_agents.conversations`:

```python
LeadOutcomeScope                 # story_specific | workflow_meta
LeadEvidenceStatus               # not_applicable | verified | missing |
                                 # empty | incomplete | conflicting
CollaborationEvidenceAcquisition # attached_reference | core_status |
                                 # zero_hit_search | read
CollaborationEvidenceReceipt(...)
CollaborationLeadEvidence(
    scope: LeadOutcomeScope,
    status: LeadEvidenceStatus,
    receipts: tuple[CollaborationEvidenceReceipt, ...],
)
CollaborationLeadJudgmentPayload(
    kind: LeadOutcomeKind,
    message: str,
    evidence: CollaborationLeadEvidence,
    authoring_scope: AuthoringScope | None = None,
)
CollaborationLeadOutcomeFailurePayload(
    code: CollaborationLeadOutcomeFailureCode,
    invocation_id: str,
    call_id: str,
)
```

Receipts are content-free, invocation-local facts. An attached-reference or
READ receipt retains source identity, locator, revision/content digest, size,
and exact boundary. A core-status receipt records present, empty, or absent
state for one canonical core path. A complete zero-hit SEARCH receipt records
the query digest/count, authorized source identities/kinds, optional canonical
path prefix, and an authorized-source fingerprint; it does not store query or
source text. All receipts in one `CollaborationLeadEvidence` share the same
operation, result correlation, and manifest.

`CollaborationLeadJudgmentPayload` enforces the closed evidence matrix described
in the [lead-agent guide](../guides/lead-agent.md#how-a-lead-judgment-is-grounded).
Runtime does not perform semantic classification. It maps provider-selected
IDs only when they are current, canonical, unique, ordered, and authorized.
An evidence-validation rejection persists the content-free failure payload and
returns `retry_lead_turn`; the rejected provider result is not a judgment and
is not replay authority.

## Contextual objective contracts

`story_writing_agents.conversations` also exports `LeadObjectiveAction`,
`LeadObjectiveIntent`, `ArtifactObjectiveNextStep`, `ArtifactObjectiveState`,
`ArtifactDraftSource`, `ArtifactObjectiveFacts`, `ArtifactObjectiveFactsV2`,
`ArtifactObjectiveCandidateBinding`, `ArtifactObjectivePayload`,
`ArtifactObjectivePayloadV2`, `ArtifactObjective`, and `artifact_objective_context()`.

The provider omits `document_change_request_update` or supplies an `update_type`
of `start_request`, `continue_request`, or `cancel_request`. Start selects exact
target paths; the runtime freezes the originating user-event content as the
visible request. Continue/cancel names an offered pending request owned by the
current conversation. `requested_workflow_step` selects `ask_clarification`
(a clarification question), `consult_specialists` (a consultation proposal), or
`draft_document_change_proposal` (an exact artifact change proposal). The final
step cannot become advice during bounded result correction. These wire fields
normalize into the Python domain types listed above; their storage names remain
unchanged. See the [wire field guide](provider-wire.md) and
[runtime API](runtime-api.md#contextual-authoring-objectives).

Runtime co-batches an accepted transition with its Lead result. Draft sources
bind the complete original visible message bytes by event ID and digest;
candidate bindings name the exact candidate event, ID, revision, and digest.
These are persisted context and correlation, never read/write capabilities.
Only the bound candidate's matching validated `APPLIED` or `UNCHANGED` result
satisfies the objective. Synthesis and review cannot do so. Persistence and
origin-conversation ownership are described in
[collaboration history](collaboration-history.md#contextual-artifact-objectives).

## Recommendation preparation contracts

The conversations facade exports `LeadRecommendationActionKind`,
`LeadRecommendedAction`, `LeadRecommendationState`,
`LeadRecommendationRequestPayload`, `LeadRecommendationDecision`,
`LeadRecommendationDecisionPayload`, `LeadRecommendationStartedPayload`, and
`LeadRecommendationResultPayload`. These values do not add a runtime result
facade: `decide_lead_recommendation()` returns `LeadTurnResult` with a trailing
optional `recommendation_request` for a pending recommendation.

A fresh provider recommendation uses `proposal_drafting_request` with
`action_type` set to `draft_document_change_proposal` or
`draft_specialist_consultation_proposal`; unrelated intent fields are omitted.
The runtime normalizes it into the Python values above, resolves source aliases,
and discloses the action, basis, impact, and call reservation. The request requires substantive scope; consultation preparation includes every
affected specialist. Its optional Python `authoring_scope` is keyword-only and
binds the canonical scope into `action_digest` when present. Legacy omission
remains omitted in codecs and digests; an old pending unscoped request is stale
and requires fresh planning. Confirm retains the exact current task/revision
and scope. Revise offers the same task's next revision with existing obligations
preserved and a fresh decision required.
Confirm authorizes one preparation only. Review/execution, Apply, and Commit
retain separate decisions. Ordinary contextual approval is a new instruction
and can supersede the unconfirmed recommendation without a fabricated Confirm.

A new Confirm-origin objective uses v2 request/decision provenance. An ordinary
v1 objective preserves the exact originating user-event payload as its request,
not a provider-authored paraphrase. Continuing an existing objective preserves
its immutable v1 or v2 facts independently of the preparation execution origin. See the
[exact API](runtime-api.md#leadrecommendationdecision) and
[versioned history](collaboration-history.md#recommendation-preparation-events).

## Authoring scope and assignments

Pure ownership values are direct-module APIs from
`story_writing_agents.specialist_routing`:

```python
AuthoringScope(
    activity: AuthoringActivity,  # workflow | draft | revise | review
    target_paths: tuple[str, ...],
    domains: tuple[ArtifactReviewDomain, ...],
    interpretation: str,
    task_id: str,
    task_revision: int,
    schema_version: int = 2,  # fresh Lead proposals explicitly use 3
    *,
    intent: AuthoringIntent = AuthoringIntent.AUTHORING,
    contributions: tuple[AuthoringContribution, ...] = (),
    dependency_slots: tuple[AuthoringDependencySlot, ...] = (),
)
AuthoringContribution(domain: ArtifactReviewDomain,
                      role: AuthoringContributionRole)  # draft_owner | constraint_assessor
AuthoringDependencySlot(slot_id: str,
                        dependent_domains: tuple[ArtifactReviewDomain, ...])
ReviewBasis(candidate_input_ids: tuple[str, ...], criteria: str,
            schema_version: int = 1)
SpecialistAssignmentMode         # draft | constraint_assessment | review
```

Domains have canonical order `plot_structure`, `character_world`,
`tone_execution`. Runtime unions semantic domains, affected canonical paths,
and an explicitly selected current task; empty/missing targets still count.
Workflow scopes have no domains or targets. Interpretation remains model-owned;
these values confer no reads, execution, or write authority.

Live `LeadAssignmentIntent` and `CollaborationAssignment` carry explicit `mode`
and `review_basis`: draft requires a null basis, while review requires concrete
nonempty candidate inputs and criteria. The conversations facade owns the exact
`authoring_scope_to_dict`/`authoring_scope_from_dict` and
`review_basis_to_dict`/`review_basis_from_dict` durable codecs.

Fresh scope-v3 Lead invocations use `lead-response-v7`; explicit legacy
scope1/2 bindings retain `lead-response-v6`. Ordinary draft/review uses
`specialist-assignment-response-v3`; constraint assessment uses v4. Synthesis
retains `consultation-summary-v4` and `artifact-review-summary-v4`.
These names are invocation contracts, with no global wire-version change.
Provider scopes identify the current shape through required contribution and
dependency arrays; durable scope codecs explicitly record version 3. JSON names
are independent of Python values: provider `writing_task_scope` includes
`activity_type`, `target_artifact_paths`, `specialist_domains`, and the exact
`task_reference` object. The runtime reconstructs attribution and routing facts
and never infers missing authority selectors.

Provider schemas are closed. Missing Lead scope receives one bounded repair;
a second rejection becomes a runtime-owned visible no-action fallback. That
fallback is not a provider-schema variant.

Scope version 3 requires explicit contribution and dependency arrays. Each
required domain declares one role; domains mapping to one specialist must agree.
Draft/revise requires at least one draft owner. Constraint assessment has no
`ReviewBasis` and cannot supply draft proof or review support. Final review keeps
the complete affected specialist union and concrete identical candidate.

The proposal intent carries `LeadSharedPremiseIntent(slot_id, selected_premise,
basis_input_ids)`. The host resolves those offered aliases into immutable
`CollaborationSharedPremise(slot_id, selected_premise, sources)` values, where
`CollaborationPremiseSource` contains the exact `input_revision` and
`source_conversation_id`. Basis version 2 explicitly serializes `schema_version`
and `shared_premises`, including an empty array. Basis version 1 omits both and
retains its historical bytes. Scope versions 1/2 reject new duty/slot fields,
even empty arrays; their intents cannot acquire captures or constraint duties.

A preparation recommendation may bind a destination separately from a
confirmed targetless source scope. The source capture stays exact, and the
destination's artifact domains must already belong to its full domain union.
The exact request and immutable candidate retain destination authority, while
all required calls inherit the source's round and physical-call counters.

A completed local confirmed scope-v3 task may explicitly offer its next revision.
That revision requires a fresh proposal, Confirm, and substantive owner drafts;
previous drafts and same-revision restarts grant no new execution authority.
When the old continuation floor is exhausted, the bounded ordinary invocation
excludes the old revision pair and preparation authority. Its successor is
restricted to DRAFT or REVISE; REVIEW cannot bypass fresh owner drafts.

## Specialist reply and lead synthesis

Import the public semantic values from `story_writing_agents.conversations`:

```python
CollaborationSpecialistAssessment
CollaborationSpecialistReplyPayload(
    proposal_id: str,
    proposal_revision: int,
    proposal_digest: str,
    execution_attempt_id: str,
    assignment_id: str,
    dispatch_event_id: str,
    assessment: CollaborationSpecialistAssessment,
    message: str,
    input_ids: tuple[str, ...],
    mode: SpecialistAssignmentMode | None = None,
)
CollaborationLeadSynthesisPayload(
    proposal_id: str,
    proposal_revision: int,
    proposal_digest: str,
    execution_attempt_id: str,
    message: str,
    reply_event_ids: tuple[str, ...],
    unresolved_input_ids: tuple[str, ...],
    evidence: CollaborationLeadEvidence,
    next_intent: LeadProposalIntent | None = None,
    authoring_scope: AuthoringScope | None = None,
)
```

The constructor above describes the durable synthesis payload. The ordinary
provider result uses `consultation-summary-v4`, with explicit
`response_type: "consultation_summary"`, `response_text`, and schema-permitted
`proposal_drafting_request` or `follow_up_consultation`. The two requests are
mutually exclusive. Provider specialist status values are `draft_completed`,
`supports_proposal`, `constraints_assessed`, `concerns_found`, `conflicts_found`,
and `insufficient_evidence`. The following rules describe the Python domain values
normalized from that wire contract.

Constraint-mode assessment is `constraints_assessed`, `concern`, `conflict`,
or `insufficient_evidence`, with wire mode `assess_constraints`.
Draft-mode assessment is `drafted` or `insufficient_evidence`; review-mode
assessment is `supports`, `concern`, `conflict`, or `insufficient_evidence`.
The runtime binds the reply to its assignment mode, assignment ID, and exact
ordered input IDs. Durable synthesis records cite every successful reply event in order
and cannot omit an input from a concern, conflict, or insufficient-evidence
reply. `drafted` contributes material without asserting support or final review.
A non-null `next_intent`
must be `continue_lineage`, cover the original and reply-event inputs exactly,
and is accepted only after round one. Round two cannot continue again.
Synthesis also preserves the proposal's exact story-specific evidence; reply
events are attribution and cannot replace story evidence.

## Reviewed artifact proposal contracts

Provider-neutral construction values are a direct-module API:

```python
from story_writing_agents.artifact_proposals import (
    ArtifactCreateIntent,
    ArtifactDeleteIntent,
    ArtifactIntentEvidence,
    ArtifactPatchIntent,
    ArtifactProposalIntent,
    ArtifactRenameIntent,
    ArtifactReplaceIntent,
    ArtifactReviewCandidate,
    artifact_review_specialists,
    build_artifact_review_candidate,
    deserialize_artifact_review_candidate,
    serialize_artifact_review_candidate,
)
```

An intent requests one or more `create`, evidence-bound `patch`, whole-source
`replace`, whole-source `rename`, or whole-source `delete` operations plus an
optional canonical tuple of added review domains. Runtime-issued
`ArtifactIntentEvidence` binds an evidence ID to the current canonical path,
full-source digest, selected-boundary digest, and optional line range. These
values are semantic input only; they contain no capability or authority.

An exact selected zero-byte status receipt for an existing canonical core file
can supply replacement evidence after runtime recaptures and revalidates that
status. Nonempty targets still require attached-reference or READ evidence.
For READ evidence, the source revision identifies the captured metadata while
the recaptured snapshot supplies the whole-file content digest; the receipt's
content digest identifies the selected boundary. Runtime keeps those identities
distinct when constructing `ArtifactIntentEvidence`. A whole empty core file
uses the empty-content digest for both source and boundary.

`build_artifact_review_candidate()` resolves that intent against one complete
canonical `base_state`, builds the exact change set, validates projected
artifact relationships, derives mandatory review domains, and unions added
domains in canonical order. The frozen result binds candidate identity and
revision, the content-bearing change set, relationship facts, grounding
evidence IDs, mandatory/disclosed domains, and digest. Its canonical codec is
strict and intentionally content-bearing. Every disclosed reviewer receives
the same serialized candidate.

The public durable/review values are exported by
`story_writing_agents.conversations`:

```python
ArtifactReviewCandidatePayload
ArtifactProposalState
ArtifactProposalStatePayload
ArtifactReviewDecision          # propose | revise | reject
ArtifactReviewSynthesisPayload
ArtifactReviewReply
ArtifactReviewedProposal
ArtifactReviewFailureCode
ArtifactReviewFailurePayload
ArtifactProposalResult
```

The artifact synthesis repeats exact candidate/consultation/attempt identity
and preserves every reply event and unresolved non-supporting input. The
provider supplies only `decision` and `message`; runtime uses that message as
the visible rationale, adds the no-change user impact, and reports either no
additional limitations or the exact unresolved reviewer input IDs. Only `decision=propose`
creates `ArtifactReviewedProposal`. That immutable value closes over the exact
candidate, every disclosed review, evidence and synthesis identities, and the
review tail, while requiring `artifact_write_occurred=false`,
`apply_authority_granted=false`, and `git_action_occurred=false`. It is review
evidence, not a filesystem or Git capability.

## Debate request

```python
AgentRequest(
    session_id: str, debate_id: str, agent_id: str, phase: Phase,
    round_number: int, revision_id: str, task: str, user_payload: str,
    candidate_payload: str | None = None,
    messages: tuple[DebateMessage, ...] = (),
    agent_state: tuple[AgentStateEntry, ...] = (),
    conversation_context: ConversationContextWindow | None = None,
    story_session_context: StorySessionContext | None = None,
)
```

Requests are frozen and prompt-free. `task` carries phase instructions;
`user_payload` is the original input; `candidate_payload` is the current
candidate where applicable. `messages` is the public blackboard snapshot.
`agent_state` contains only the receiving agent's session-wide stored outcomes;
parallel peers receive the same pre-phase state snapshot.

`conversation_context` is `None` for context-omitted and fresh runs. Included runs put
the same validated immutable window in every draft, audit, proposal, synthesis,
and finalization request. It stays a separate provider field: the runtime does
not concatenate it into the task, input, candidate, blackboard, agent state, or
bound prompt.

`story_session_context` is a trailing package-internal capture and direct-call
compatibility value. It must match the request's session and agent. The current
built-in runtime translates authoritative capture into typed contextual wire
v7; it does not send this object as the provider envelope. Direct noncontextual
callers may still use the established request field. The context types are not
package-root exports; see [Agent-context design](../design/agent-context.md#agent-provider-wire-v10).

```python
DebateMessage(sender: str, phase: str, round: int, revision: str,
              kind: str, content: str, timestamp: str,
              trace: ReplyTrace | None = None)
AgentStateEntry(agent_id: str, session_id: str, debate_id: str, phase: str,
                round: int, revision: str, kind: str, content: str,
                timestamp: str)
```

Both are frozen and provide `to_dict()`; agent-state content is capped at 4096
characters per entry. An untraced debate message omits `trace` from its encoded
object. A traced message encodes the exact trace object, and `from_dict()`
restores it as a typed `ReplyTrace`.

## One advisory conversation turn

```python
ConversationRequest(session_id: str, conversation_id: str, turn_id: str,
                    agent_id: str, content: str,
                    context: ConversationContextWindow,
                    story_session_context: StorySessionContext | None = None)
ConversationReply(session_id: str, conversation_id: str,
                  turn_id: str, content: str)
ConversationRetrievalRequest(*, session_id: str, conversation_id: str,
    turn_id: str, agent_id: str, content: str, context: ConversationContextWindow,
    story_session_context: StorySessionContext | None = None,
    rag_context: ConversationRagContext | None = None)
ConversationRetrievalReply(*, session_id: str, conversation_id: str,
    turn_id: str, content: str, sources: tuple[ConversationRetrievalSource, ...])
```

The one-step pair is frozen and slotted; its request's trailing continuity
envelope defaults to `None`. IDs are safe, agents are canonical, and content is
nonempty exact strict UTF-8. `context` must be a
bounded, increasing window of complete turns from the same session and
conversation and exclude the current turn. Retrieval values are keyword-only;
their two trailing context fields default to `None`, and their reply adds
ordered validated sources without changing the one-step pair.
When present, story-session capture must match the request session, agent, and
any focused conversation identity. Current built-in execution converts it to
agent-provider-wire-v10; omitting it retains direct-request compatibility. An
omitted `rag_context` means there was no attempted RAG envelope.
See [Conversation transcripts](conversation-transcripts.md) for windows and
[Bounded retrieval](conversation-retrieval.md) for actions and persistence.

## Debate reply

```python
AgentReply(revision_id: str, content: str = "",
           verdict: Verdict | None = None,
           core_updates: Mapping[str, str] = {})
AgentReply.candidate(revision_id: str, content: str) -> AgentReply
AgentReply.approval(revision_id: str, content: str = "") -> AgentReply
AgentReply.conflict(revision_id: str, critique: str) -> AgentReply
AgentReply.finalization(revision_id: str,
                        core_updates: Mapping[str, str] | None = None) -> AgentReply
```

| Phase | Valid reply |
| --- | --- |
| `draft` | Nonempty candidate; no verdict or updates. |
| `audit` | Approval with optional note, or conflict with nonempty critique. |
| `propose_revision` | Nonempty proposal; no verdict or updates. |
| `synthesize` | Nonempty complete replacement; no verdict or updates. |
| `finalize` | Empty or approved candidate content; optional allowlisted full-text updates. |

Every reply targets the requested revision. Specialists cannot update core
documents, and finalization cannot alter the approved manuscript. Public models
have no hidden-reasoning field; ordinary content is visible and may persist.
