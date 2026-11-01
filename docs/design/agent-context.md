# Agent-context design

This document is the maintainer model for the bounded agent-context component.
`StoryRuntime.lead_turn()` and confirmed consultation execution invoke it for
the current Python lead workflow. Ordinary focused input in the installed
shell enters that same Lead path. References below distinguish that integration
from direct low-level component use.
See [Agent-context progress](../guides/agent-context-progress.md).

<a id="package-boundary-and-owners"></a>
## Package boundary and owners

The accepted package has nine importable units with inward dependencies and no
cycle:

| Unit | Responsibility |
| --- | --- |
| `story_writing_agents.agent_context` | Accounting-only compatibility facade. It exports 11 names and deliberately does not export service, manifest, planner, source, action, or ledger contracts. |
| `agent_context.accounting` | Canonical JSON encoding, byte attribution, physical-call accounting, and exact/unavailable provider usage. |
| `agent_context.manifest` | Immutable scope, authority, provenance, source identity/kind/priority/channel, and deterministic manifest ordering. |
| `agent_context.planning` | Pure bootstrap/delta selection, budgets, deduplication, tombstones, compact catalog/record aliases, reference reservation, and checkpoint proposal. |
| `agent_context.ledger` | Internal v2 READY/incomplete/tainted/stale state, locks, atomic publication, restart/corruption handling, and content-free metrics. |
| `agent_context.continuation` | Shared process-local Lead session transaction; defer coverage commitment until authoritative result publication. |
| `agent_context.actions` | SEARCH/READ/ANSWER values, policies, correlations, opaque handles, evidence authorization, and provider observations. |
| `agent_context.sources` | Bounded searchable source capture and drift checks; separate exact retained-session-history capture for Lead continuation. |
| `agent_context.service` | Provider-neutral agent-provider-wire-v10 call loop, local reference loading, lifecycle authority, source/action orchestration, checkpoint commit, usage aggregation, and optional progress observer. |

The retained execution entry points are backend/provider component interfaces,
plus the runtime's lead-turn and confirmed-consultation composition.
`StoryRuntime.lead_turn()`, `propose_specialist_advisory()`, and their bound
session forms accept optional context progress sinks.
Progress observer types are
direct-module-only imports from `agent_context.service`; the root and
`agent_context` facades do not export them.

<a id="end-to-end-invocation-flow"></a>
## End-to-end invocation flow

```text
focused user input + zero or more ${path} / ${path}#Lx-Ly references
  -> shell/Python Lead API records the turn and validates reference indicators
  -> PREPARE: validate observer, scope, request, references, and source authorities
  -> PLAN: inventory current sources -> manifest -> bootstrap/delta projection
  -> local authorization reads every explicit reference exactly
  -> runtime builds one per-call descriptive schema plus guided input payload
  -> selected adapter invokes Codex or Antigravity
  -> provider returns exactly one typed SEARCH, READ, or ANSWER action
     SEARCH -> runtime searches only authorized sources and returns fresh handles
     READ   -> runtime recaptures issued handles and returns bounded current text
     ANSWER -> runtime authorizes used handles, recaptures sources, and validates
               the invocation-specific semantic result
  -> Lead orchestration persists the typed judgment/proposal or fixed rejection
  -> COMMIT: after successful publication, atomically publish READY coverage
             and close the accepted provider call
  -> interactive presenter renders Lead outcome/message/next action
     or FAIL: fixed facts, taint when required, checkpoint_not_advanced;
              uncertain submitted work is preserved without replay
```

SEARCH and READ are action requests inside repeated calls to the same provider
adapter; they are not separate provider APIs. Each continuation contains only
the new runtime observation plus compact control data. The next physical call
gets a fresh schema whose allowed action variants, source
kinds, result schema, and array bounds match that exact point in the loop.

For confirmed collaboration, the runtime first commits `running` and all
ordered assignment dispatches outside the context component. It then creates
one context invocation per specialist with kind `specialist_assignment` or
`specialist_advisory`. Normal assignments run concurrently. Only when all
assignments return valid typed replies does a separate `lead_synthesis`
invocation run. The runtime persists the result batch and performs terminal or
round-two finalization locally; context checkpoints are not execution-result
authority.

The Lead session transaction covers history capture, provider work, public
terminal-event append, and READY coverage commit. Its process-local lock is
shared across runtime/service instances; existing backend access claims remain
the cross-process exclusion authority. Failed publication aborts coverage.
Non-session context invocations retain their service-owned commit boundary.

PREPARE has no distinct public progress event. A normal PLAN starts with
`bootstrap` or `delta`; a duplicate `omission` fact is callback-only in the
shell. SEARCH and READ have paired increasing action indexes. `answer_authorized`
means authorization and final revalidation passed, not that the checkpoint or
artifact committed. Exactly one terminal checkpoint event closes an operation;
there is no post-terminal event. Debate streams may interleave, so validation is
per scope and operation ID, with required phase/round identity.

Domain publication remains separate. Consensus first verifies the context
answer and references, then the writing-artifact repository performs one
recoverable catalog-last transaction. Context READY authority advances only
after its own verified service commit; it is not an artifact publication log.

<a id="physical-call-exchange-finalization"></a>

## Physical-call exchange finalization

The backend begins one best-effort agent-exchange lifecycle immediately before
each contextual physical provider invocation. Mandatory references are loaded
locally before it and create no provider exchange. The request, any valid raw `ProviderResult`, and
one closed terminal attempt share the exact `ContextScope` session,
conversation/debate kind and ID, agent, provider, and generated exchange ID.

Response capture happens before accounting observation and before the context
service receives the result. Terminal finalization stays with the component
that can prove the contextual outcome:

- a SEARCH or READ call becomes `accepted` only after its observation has been
  produced and the completion observer has received it;
- an ANSWER call becomes `accepted` immediately after the READY checkpoint
  commit and before the later `checkpoint_advanced` observer delivery;
- semantic/decode rejection, cancellation, or a post-response runtime failure
  closes the still-open physical call with a fixed terminal code.

Finalization is append-only. A later observer, artifact-publication, or outer
workflow failure does not retroactively replace an already recorded
`accepted` outcome. Conversely, a storage failure is swallowed after the
capture state advances, so a response or outcome record can be missing even
though the contextual call proceeded. There is no diagnostic retry, replay,
or inference from sequence gaps. The ledger and progress channels remain
content-free and authoritative for their own responsibilities; exchange JSONL
is bounded plaintext diagnostic evidence only.

<a id="manifest-priorities-and-planning"></a>
## Manifest, priorities, and planning

Source adapters capture immutable current snapshots. A manifest retains exact
scope, source ID/kind, revision, provenance, priority, semantic channel, bytes,
and record information without loading bodies into a public catalog. Planning
is deterministic for the same manifest, policy, material, and checkpoint.

| Priority | Current treatment |
| --- | --- |
| P0 required | Current input and complete logical candidate; exact inclusion or fixed failure. |
| P1 stable | Stable behavior identity; suppressed only on a compatible delta. |
| P2 working | Current role-relevant core documents and focused evidence; eager within its budget, with changes delivered as deltas. |
| P3 optional | Additive history and invoking-agent state; SEARCH-first by default. |

Default planning bytes are total 131,072; P0 73,728; P1 16,384; P2
32,768; P3 0; mandatory references 262,144; maximum delta depth 16 for
non-session checkpoints. Required Lead session memory is separate from these
source budgets, and compatible Lead continuation has no 16-turn rotation.
Exact-byte groups deduplicate only within an eligible semantic channel and only after
byte equality; digest equality alone is insufficient. Every semantic pointer
and provenance remains represented.

<a id="bootstrap-delta-and-checkpoint-authority"></a>
## Bootstrap, delta, and checkpoint authority

Invocation scope binds workspace, story session, conversation/debate kind/ID,
and agent. Compatibility also checks provider thread, definition fingerprint,
planning policy, and wire schema.
Bootstrap sends current required/P2 material and starts a distinct provider
thread epoch. A compatible READY checkpoint allows unchanged sources to be
suppressed and changed records/source bodies or tombstones to be delivered.

Catalog aliases are durable within a resumable epoch, never reused after
removal, and distinct from record aliases. Record cursors retain ordering and
deletion facts without prose. Missing, corrupt, incompatible, stale, tainted,
provider-mismatched, or excessive-depth non-session state selects bootstrap.
Ambiguous provider failure taints the continuation; it never authorizes later
omission or replay of unresolved submitted work.

Ordinary Lead turns and consultation/artifact-review synthesis use a separate `SESSION`
continuation/checkpoint identity for one workspace/session/agent. Invocation
correlation, reference/evidence authority, exchanges, and progress remain `CONVERSATION`
scoped. Specialists and consensus keep their existing exact scopes. The new session
namespace bootstraps without reading, migrating, or rewriting old conversation-scoped
namespace bytes.

Lead bootstrap sends all retained active and archived history. Compatible deltas deliver
missing turn content and new host/specialist semantic material regardless of originating
turn. Provider-origin Lead text demonstrably known to a verified thread and text present
in current input are suppressed without dropping new workflow facts.
`conversations.session_context` owns the provider-visible projection. Canonical records,
original timestamps and the coverage vector of conversation ID, sequence and prefix
digest remain host-owned. No global session sequence is invented. Changed valid prefixes
invalidate continuation and bootstrap current authority; corrupt canonical history fails
closed.

Required history also carries exact recommendation requests, decisions, starts,
and results. A source alias such as `current_response` is resolved to the whole
visible event and exact UTF-8 digest before persistence. Those remembered facts
do not reconstruct a live preparation capability. Recommendation Confirm is
its own execution origin; objective v1 user-event or v2 Confirm provenance stays
immutable when the objective continues.

Required history includes runtime-owned artifact objectives, their origin
conversations, selected whole-message draft sources, and exact candidate
bindings. This lets the Lead interpret abbreviated approval after a draft or
retain outstanding authoring work after a conversation switch. Only the owner
conversation can continue or cancel its objective; only a matching successful
Apply result satisfies it. Consultation and synthesis leave it open.

Ledger v2 lives below the internal agent-state area and stores only identities,
digests, revisions, boundaries, sizes, aliases, numeric usage/accounting, and
fixed state. V1 remains untouched and is not read or migrated. The authoritative
sources remain the workspace repositories, not the ledger or provider history.

<a id="authorized-sources-search-and-read"></a>
## Authorized sources, SEARCH, and READ

The SEARCH/READ registry has exactly four scoped adapters: canonical writing
artifacts, focused same-session conversation, active debate, and the invoking
agent's own state. Operation routing further restricts which kinds may be searched.
Canonical artifact inventory covers every eligible canonical directory
descendant and excludes transcripts, discussion logs, agent states, ledgers,
symlink escapes, and other internal files.

Confirmed collaboration narrows the writing-artifact adapter again for each
assignment. Runtime builds a fresh canonical reference operation from only that
assignment's ordered artifact inputs and supplies an immutable allowed-path
set. Inventory, SEARCH, and READ cannot expose a sibling assignment's
artifact-only paths. Lead synthesis is a separate invocation over all direct
story-artifact inputs and typed specialist replies; it does not inherit a
specialist's read authority or receipt.

These adapters do not share one filesystem authority. Focused conversation
uses the configured conversation store owned by the control plane. For each
artifact-sensitive invocation, runtime derives a package-internal
`SessionSourceWorkspace` from the same freshly validated exact-session binding
as the artifact repository and lends it only to the active-debate and invoking-
agent-state adapters. Its reads are descriptor-relative and pinned to the
session worktree's physical `(st_dev, st_ino)` identity. The binding also pins
the exact semantic READY branch/ref/worktree inspection. Every outer repository
or session-source transaction enters a short synchronous cooperating-runtime
guard, checks READY and the named physical root once before and once after its
body, and then releases the Git lease and descriptors. Nested binding and
repository operations reuse that transaction with thread/lease liveness and
named-root checks rather than repeating the complete READY scan. The guard
never spans the provider `await`; a later SEARCH, READ, ANSWER revalidation,
reference-ledger action, or closure step reacquires it. A replaced or
persistently non-ready root is therefore rejected before content returns or at
the next checkpoint; neither
adapter can fall back to the control root, another session, or a previous
binding. The repository is private to the binding and is available only
through the live access yielded by that guard; the access expires when the
guard closes. Closing revalidates after a successful action, raised error, or
cancellation. If closing detects binding loss, that content-free failure
dominates an otherwise concurrent body failure; with a valid binding, the
original error or cancellation is preserved. The lease does not continuously
exclude arbitrary external Git that does not cooperate with the runtime. The
view supplies no generic path, write, provider-filesystem, or Git authority.

Direct Lead and Lead-synthesis invocation owners additionally acquire an
opaque six-descriptor physical-generation pin before session-derived
definitions, sources, or provider work. It retains the exact control-root,
common-Git, worktree-administration, session-worktree, `.git`, and `commondir`
relationship through durable result publication, then rechecks and closes.
This pin can span the provider await because it retains no Git lease and grants
no traversal, content, mutation, or semantic READY authority. The pin itself
does not set a process working directory. For each Codex or Antigravity story
call, the same session binding separately mints an opaque call-local capability
that selects the retained session-root descriptor as OS working directory.
Codex also selects native project root `--cd .`; Antigravity uses that exact CWD
as its project root. See the
[Provider session execution boundary](../reference/backend-and-sessions.md#codex-session-execution-boundary).
Antigravity's selected role projection is protected control data rather than
story content; SEARCH/READ remains the story-evidence path.

The session-source reader accepts only canonical relative POSIX paths. It
traverses without following symlinks, retains the complete root-to-parent
descriptor chain, and accepts only a stable, singly linked regular-file leaf
whose named and opened identity, mode, size, and timestamps agree before and
after reading. After the final leaf, inventory, or absence observation, every
original parent-child edge is reopened and revalidated. Agent-state JSONL is
limited to 8 MiB and 10,000 records across the selected files; debate JSONL is
limited to 10,000 records. Both iterate lazily and reject record 10,001 before
slicing, parsing, or retaining it. FIFOs, devices, hard links, replacements,
ancestor drift, and foreign embedded session/debate/agent ownership fail as
source unavailability before evidence is accepted.

The guarded reference-operation variant deliberately preserves a root-security
failure for orchestration instead of converting it to a generic reference
workflow failure. Orchestration can therefore close exact binding loss as
typed recovery without provider replay or repository fallback.

`FocusedConversationSourceAdapter` reads canonical v2 events from the active
store and applies `select_bounded_collaboration_context()`. Its default mode is
a content-free bootstrap with no provider-readable record body. The lead
runtime explicitly enables bounded searchable history across active
same-session conversations: it still sends zero eager bodies, then SEARCH and
READ add only authorized selected records under the 24-record/32,768-byte
bound. Private payload fields are excluded from this search adapter.
`SessionHistorySourceAdapter` separately captures complete retained active and
archived history for required Lead memory; the search bound does not limit it.

The default action policy permits four nonterminal actions and five provider
calls: at most two SEARCH, two READ, four queries per SEARCH/eight cumulative,
eight handles per READ/16 cumulative, ten hits per query, 20 per SEARCH and
cumulative, and three per source/query. Queries are at most 200 characters;
snippets 240 characters/1,024 UTF-8 bytes; an excerpt/source-read is at most
16,384 bytes, a READ 24,576 bytes, and an observation 32,768 bytes.

The source policy caps inventory at 4,096 sources/8,192 traversal entries,
10,000 records per source, 8 MiB per source/scan, 64 MiB captured bodies, and
32 MiB cumulative scan. SEARCH normalizes queries, scans each authorized source
once for a shared prefix, ranks deterministically, and mints fresh opaque
handles. READ recaptures requested sources, verifies revision/boundary/digest,
and returns requested order. Handles are invocation-bound, single-lifecycle
authorities and cannot be guessed, reused, or transferred.

Each SEARCH query's `source_kinds` is a nonempty, duplicate-free collection
drawn from the kinds allowed for that request. Provider array order has no
semantic meaning: runtime canonicalizes the collection to its internal source-
kind order before search authorization. The common provider-schema subset
types and enums members but does not claim to enforce set uniqueness or
canonical order. Runtime rejects empty, duplicate, unknown, and request-
disallowed values. A non-null `path_prefix` is valid only when the canonical
collection is exactly `writing_artifact`.

### Invocation-local lead evidence

A lead-front-channel request opts into a content-free status inventory of the
four canonical core paths. Each row is exactly `present`, `empty`, or `absent`
and receives a deterministic `status_<ordinal>` evidence ID plus the current
manifest and, when present, revision/digest/size facts. This inventory does not
send a file body. With no explicit reference and no provider SEARCH/READ, a
workflow/meta result therefore has no story receipt.

SEARCH and READ add evidence through the same invocation lifecycle:

- a complete, non-truncated zero-hit SEARCH receives `search_<action-index>`
  plus query digest/count, authorized source kinds/IDs, optional artifact
  prefix, and a deterministic authorized-source fingerprint;
- a completed READ receives `read_<action-index>_<ordinal>` plus exact source,
  boundary, revision, digest, and size facts; and
- final ANSWER authorization includes only READ receipts whose handles the
  provider names as used, while retaining all completed reads for lifecycle
  validation.

The evidence IDs and receipts are content-free and invocation-local. Runtime
maps only a provider-selected canonical ordered subset into durable lead
evidence, then applies the domain's closed status/kind matrix. The context
component does not interpret story semantics. Search queries and source bodies
are absent from durable collaboration evidence.

For artifact preparation, a selected exact status receipt for an existing
zero-byte canonical core document can support `replace` after runtime
recaptures and revalidates that status. It proves empty content only. Nonempty
targets need attached-reference or READ evidence; a metadata revision is kept
distinct from the recaptured whole-file digest and selected-boundary digest.

<!-- Historical audit-link alias only; the visible normative contract is v8. -->
<a id="agent-provider-wire-v10"></a>

## Descriptive agent provider wire

Contextual and direct calls use `agent-provider-wire-v10`. The
[agent JSON field guide](../reference/provider-wire.md) specifies the request
properties, named context records, action variants, result schemas, and field
meanings. Built-in result objects include explicit `response_type` values.

Contextual initial requests contain `request_correlation_token`,
`response_contract`, `task_input`, and applicable `session_history`, `current_draft`,
`context_documents`, `context_record_updates`, and `context_removals`. Continuations
carry current controls and `context_observation`. Every physical request includes
`protocol_version` and guidance for its actual input and response schema.

The projection preserves exact readable content, selectable identities, source
ranges, provenance, digests, and byte counts. Source and history records use named
objects. `core_document_statuses` distinguishes `has_content`, `empty_file`, and
`missing_file`. Remembered history does not satisfy a current READ or explicit
reference obligation. Untrusted source text remains opaque and cannot redefine
runtime instructions.

Repeated runtime-owned dictionaries can share a request-local object table when
both total encoded bytes and recursive property count become smaller.
`context_reuse` declares reconstruction instructions; `shared_context_objects`
contains literal values. An exact `{"shared_context_object_id":"o1"}` at an
eligible slot refers to that request's table. Each request carries its complete
table. The runtime does not recursively interpret arbitrary story JSON as references.

Eligible repeated slots include history record details, `document_change_requests`,
`confirmed_proposal_drafting_context` records, and selected conversation events.
Sharing uses strict canonical JSON equality and measures the actual descriptive
wire representation. It does not omit mandatory reference bodies. Component paths
follow projected and shared fields so request, schema, guide, and bound-instruction
bytes remain included in context accounting.

The single current wire version participates in provider continuation fingerprints.
No migration, old-format response decoder, or alternate field spelling is provided.
Public Python domain values and history storage have independent interfaces.

<a id="candidate-pointer-single-splice-and-fallback"></a>
## Candidate pointer, single splice, and fallback

The ledger retains a content-free logical-candidate identity. If a compatible
call's candidate bytes are unchanged, the provider receives a pointer to the
known exact logical candidate. If changed, planning finds the maximal common
Unicode-code-point prefix and suffix and encodes one insertion/deletion splice.
The provider must reconstruct the exact target identity/digest/size, and the
splice is used only when physically smaller than the full candidate.

Full exact candidate fallback is mandatory on bootstrap, thread loss, restart,
taint, stale target, identity/digest/size mismatch, unsafe reconstruction, or
non-beneficial encoding. The interpreter applies candidate operations in
payload order so repeated equal text with distinct identities and successive
splices remain unambiguous. This replaces the historical illustrative
`changed_candidate_sections` sketch; there is no LLM sectioning.

<a id="part-prefix-filtering"></a>
## Part-prefix filtering

One SEARCH may carry one canonical writing-artifact directory prefix such as
`manuscript/part_01/`. It is authorized and applied before source capture,
lexical scanning, ranking, global deduplication, and hit truncation. All queries
share it, so mixed prefixed/unprefixed scopes are unrepresentable and each
eligible source is scanned once. Nested canonical Markdown files are included;
out-of-prefix, internal, absolute, traversal, backslash, file-path, and
incompatible-kind prefixes fail. A prefix grants no path-only or arbitrary
filesystem discovery.

<a id="mandatory-reference-separation"></a>
## Mandatory-reference separation

References are parsed from ordered `${path}` or `${path}#Lx-Ly` indicators.
For every addressed invocation the provider must first echo the exact ordered
reference action. The runtime securely captures current whole/ranged bytes,
then attaches them as untrusted mandatory context to the substantive call and
revalidates completion before acceptance.

Reference accounting and authorization remain independent of the ambient
manifest, SEARCH/READ handles, P budgets, checkpoint suppression, candidate
delta, and provider cache. A repeated reference is not satisfied because an
earlier agent or invocation read it.

<a id="observer-lifecycle-and-shell-projection"></a>
## Observer lifecycle and shell projection

`ContextExecutionEvent`, `ContextExecutionEventKind`, and
`ContextExecutionEventSink` are available only from
`story_writing_agents.agent_context.service`. A low-level contextual executor
and `lead_turn()` can accept the synchronous callback; it must return exactly
`None`.

Noncallable, declared async/async-generator, asynchronously callable, or
noncontextual observers fail before contextual effects. A callback exception,
awaitable result, or non-`None` result aborts before authority and is re-raised.
If delivery of `checkpoint_advanced` fails, READY is already committed and
cannot be rolled back, though the outer operation reports observer failure. If
an independent runtime failure is primary, terminal-observer failure is
suppressed. No observer preserves calls, results, schemas, and persistence.

A Python application can validate each correlated event stream and render
compact content-free lines. Interactive Lead turns emit these through the
advisory progress presenter. The
component has no lifecycle replay or durable observer store.

<a id="provider-usage-cache-and-limits"></a>
## Provider usage, cache, and limits

Physical-call accounting uses canonical UTF-8 JSON and attributes binding,
schema, and payload without double counting locally loaded reference bytes. Provider
input/output/cache token values are exact only when supplied as validated
nonnegative integers. A terminal progress event aggregates input tokens only
when every submitted provider call reported an exact
value; otherwise the field is `None`. No estimate is labeled exact.

Provider의 native cache, 대화 이력과 compaction은 성능에 도움이 될 수 있지만 정확성이나
권한의 근거는 아니다. Thread가 사라지면 다음의 안전하고 승인된 작업에서 로컬의 권위
있는 데이터를 사용해 bootstrap한다.
Lead 세션의 canonical payload는 524,288 UTF-8 bytes 이하, 이력 목록은 4,096개 대화
이하로 제한된다. 알려진 입력·이력·현재 상태·framing을 provider 실행 전에 확인하며,
생성된 reference observation은 실질적인 제출 전에 다시 검사한다. 초과하면
`exact session context exceeds the delivery capacity`를 발생시키고 필요한 내용을 요약하거나
조용히 생략하지 않는다. 실제 payload 회계에는 `h`가 포함되며 source 선택의
내용 비노출 진행 수치와는 구분한다. 이 byte 한도는 provider의 token window와 독립적이다.
Planner, action, source, reference, ledger의 각 유한 한도도 독립적으로 검사한다.
한도를 소진하면 필요한 내용을 생략하는 대신 내용을 노출하지 않는 명시적 실패를 반환한다.

<a id="privacy-recovery-and-compatibility"></a>
## Privacy, recovery, and compatibility

Public progress, metrics, and the ledger contain no prompt/query, candidate,
source or reference body, excerpt, provider response, absolute path, private
state, exception payload, or provider-thread detail. Debug logging records only
fixed `agent_context` outcome categories and safe scope IDs, not observer
history. The separate agent-exchange store does contain bounded canonical
request and completed-response projections for these same physical calls; it
is not a progress or ledger field. Even content-free IDs, counts, timing, and
relative topology are sensitive plaintext and require normal workspace
protection.

There is no semantic RAG, new dependency, free-form durable memory, provider
opaque correctness source, arbitrary filesystem discovery, or cross-agent
state sharing. Wire v7 has no migration or legacy decoder. Exact references
and the existing publication contracts remain compatible. "Atomic"
publication means one recoverable catalog-last artifact
transaction; it does not mean an unrestricted multi-manuscript batch API.
Terminal-deadlock resume/extension remains outside this initiative until issue
#140 is integrated.

See [Recovery and limitations](../operations/recovery-and-limitations.md) and
[Privacy and provider exposure](../operations/privacy-and-provider.md) for the
operational boundaries.
