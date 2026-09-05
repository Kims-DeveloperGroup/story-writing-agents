# Agent-context design

This document is the maintainer model for the bounded agent-context component.
`StoryRuntime.lead_turn()` and confirmed consultation execution invoke it for
the current Python lead workflow. Ordinary focused input in the installed
shell enters that same Lead path. References below distinguish that integration
from direct low-level component use.
See [Agent-context progress](../guides/agent-context-progress.md).

<a id="package-boundary-and-owners"></a>
## Package boundary and owners

The accepted package has eight importable units with inward dependencies and no
cycle:

| Unit | Responsibility |
| --- | --- |
| `story_writing_agents.agent_context` | Accounting-only compatibility facade. It exports 11 names and deliberately does not export service, manifest, planner, source, action, or ledger contracts. |
| `agent_context.accounting` | Canonical JSON encoding, byte attribution, physical-call accounting, and exact/unavailable provider usage. |
| `agent_context.manifest` | Immutable scope, authority, provenance, source identity/kind/priority/channel, and deterministic manifest ordering. |
| `agent_context.planning` | Pure bootstrap/delta selection, budgets, deduplication, tombstones, compact catalog/record aliases, reference reservation, and checkpoint proposal. |
| `agent_context.ledger` | Internal v2 READY/incomplete/tainted/stale state, locks, atomic publication, restart/corruption handling, and content-free metrics. |
| `agent_context.actions` | SEARCH/READ/ANSWER values, policies, correlations, opaque handles, evidence authorization, and provider observations. |
| `agent_context.sources` | Bounded current capture of canonical artifacts, focused conversation, active debate, and invoking-agent state; lexical search/read and drift checks. |
| `agent_context.service` | Provider-neutral typed wire-v7 call loop, reference prelude, lifecycle authority, source/action orchestration, checkpoint commit, usage aggregation, and optional progress observer. |

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
  -> optional reference prelude reads every explicit reference exactly
  -> runtime builds one per-call typed-v7 schema plus compact input payload
  -> selected adapter invokes Codex or Antigravity
  -> provider returns exactly one typed SEARCH, READ, or ANSWER action
     SEARCH -> runtime searches only authorized sources and returns fresh handles
     READ   -> runtime recaptures issued handles and returns bounded current text
     ANSWER -> runtime authorizes used handles, recaptures sources, and validates
               the invocation-specific semantic result
  -> COMMIT: atomically publish READY checkpoint and close accepted provider call
  -> Lead orchestration persists the typed judgment/proposal or fixed rejection
  -> interactive presenter renders Lead outcome/message/next action
     or FAIL: fixed facts, taint when required, checkpoint_not_advanced;
              uncertain submitted work is preserved without replay
```

SEARCH and READ are action requests inside repeated calls to the same provider
adapter; they are not separate provider APIs. Each continuation contains only
the new runtime observation plus compact control data. The next physical call
gets a fresh schema whose correlation enum, allowed action variants, source
kinds, result schema, and array bounds match that exact point in the loop.

For confirmed collaboration, the runtime first commits `running` and all
ordered assignment dispatches outside the context component. It then creates
one context invocation per specialist with kind `specialist_assignment` or
`specialist_advisory`. Normal assignments run concurrently. Only when all
assignments return valid typed replies does a separate `lead_synthesis`
invocation run. The runtime persists the result batch and performs terminal or
round-two finalization locally; context checkpoints are not execution-result
authority.

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
each contextual physical provider invocation, including a separate
mandatory-reference prelude. The request, any valid raw `ProviderResult`, and
one closed terminal attempt share the exact `ContextScope` session,
conversation/debate kind and ID, agent, provider, and generated exchange ID.
The prelude therefore inherits the outer conversation/debate scope; storage
does not invent a separate reference scope or resolve a subject label.

Response capture happens before accounting observation and before the context
service receives the result. Terminal finalization stays with the component
that can prove the contextual outcome:

- a SEARCH or READ call becomes `accepted` only after its observation has been
  produced and the completion observer has received it;
- an ANSWER call becomes `accepted` immediately after the READY checkpoint
  commit and before the later `checkpoint_advanced` observer delivery;
- a successful mandatory-reference prelude becomes `accepted` in the backend
  before its result is returned to the contextual service; and
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
32,768; P3 0; mandatory references 262,144; maximum delta depth 16. Exact-byte
groups deduplicate only within an eligible semantic channel and only after
byte equality; digest equality alone is insufficient. Every semantic pointer
and provenance remains represented.

<a id="bootstrap-delta-and-checkpoint-authority"></a>
## Bootstrap, delta, and checkpoint authority

Scope binds workspace, story session, subject kind/ID, agent, provider,
provider thread, definition fingerprint, planning policy, and wire schema.
Bootstrap sends current required/P2 material and starts a distinct provider
thread epoch. A compatible READY checkpoint allows unchanged sources to be
suppressed and changed records/source bodies or tombstones to be delivered.

Catalog aliases are durable within a resumable epoch, never reused after
removal, and distinct from record aliases. Record cursors retain ordering and
deletion facts without prose. Missing, corrupt, incompatible, stale, tainted,
provider-mismatched, or excessive-depth state selects bootstrap. Ambiguous
provider failure taints the continuation; it never authorizes later omission.

Ledger v2 lives below the internal agent-state area and stores only identities,
digests, revisions, boundaries, sizes, aliases, numeric usage/accounting, and
fixed state. V1 remains untouched and is not read or migrated. The authoritative
sources remain the workspace repositories, not the ledger or provider history.

<a id="authorized-sources-search-and-read"></a>
## Authorized sources, SEARCH, and READ

The registry has exactly four scoped adapters: canonical writing artifacts,
focused same-session conversation, active debate, and the invoking agent's own
state. Operation routing further restricts which kinds may be searched.
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

`FocusedConversationSourceAdapter` reads canonical v2 events from the active
store and applies `select_bounded_collaboration_context()`. Its default mode is
a content-free bootstrap with no provider-readable record body. The lead
runtime explicitly enables bounded searchable history across active
same-session conversations: it still sends zero eager bodies, then SEARCH and
READ add only authorized selected records under the 24-record/32,768-byte
bound. Private payload fields and automatic full-history injection are
excluded.

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

<!-- Historical audit-link alias only; the visible normative contract is v7. -->
<a id="compact-provider-wire-v5"></a>
<a id="typed-provider-wire-v7"></a>
## Typed provider wire v7

Contextual bindings define `agent-context-wire-v7`. The initial compact
payload uses `c` (correlation), `q` (phase/result contract), and `i` (required
current input), with applicable compact candidate `n`, selected documents
`docs`, projection `p`, and tombstone/record `t` data. Mandatory reference
bodies appear only after the separate prelude as
`untrusted_writing_artifact_context`. Continuations contain only correlation,
contract, and the new observation (`c`, `q`, `o`).

Provider results are exact closed objects with required `correlation` and
`action` fields. `correlation` must echo the current input `c` token. `action`
is one schema-selected closed variant:

```json
{"correlation":"<exact-token>","action":{"kind":"search","path_prefix":null,"queries":[{"text":"opening reveal","source_kinds":["writing_artifact"]}]}}
```

```json
{"correlation":"<exact-token>","action":{"kind":"read","handles":["<issued-handle>"]}}
```

```json
{"correlation":"<exact-token>","action":{"kind":"answer","result":{"kind":"answer","message":"Visible result.","proposal":null},"used_handles":["<issued-handle>"]}}
```

The third example uses a shortened generic result object; the live ANSWER
schema embeds the complete invocation-specific result schema at
`action.result`. There is no JSON-in-string layer and no compatibility decoder
for an older action envelope. `used_handles` contains only successful READ
handles supporting that answer and is empty when no READ supports it.
SEARCH/READ observations retain the full authoritative identity, revision,
boundary, digest, size, provenance, handle, and untrusted content required for
provider reasoning, even though the input payload remains compact.

For a lead initial call, `core_statuses` is an additional top-level list beside
`c`, `q`, and `i`. The final `action.result` contains all seven Lead fields:
`scope`, `evidence_status`, `evidence_ids`, `kind`, `message`,
`artifact_intent`, and `proposal`. Those semantic fields are decoded by
orchestration only after context ANSWER authorization. Invalid, invented,
reordered, stale, or matrix-incompatible evidence rejects that physical
response, which is never replayed. With fresh authority and remaining budget,
runtime may request one bounded same-turn correction. A final deterministic
rejection closes with `retry_lead_turn`, which starts a fresh user turn and
provider call. An uncertain submitted outcome instead remains
`outcome_unknown` with `inspect_and_resolve_outcome` and is never replayed.

The built-in runtime uses provider-managed context and empty mutable-core
bindings. Package-internal `story_session_context` remains an internal capture
and direct-compatibility input; it is not the current provider wire. Direct
noncontextual definitions still preserve their exact eager bytes,
fingerprints, and adapter behavior.

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
schema, payload, and raw reference bytes without double counting. Provider
input/output/cache token values are exact only when supplied as validated
nonnegative integers. A terminal progress event aggregates input tokens only
when every submitted call—including a reference prelude—reported an exact
value; otherwise the field is `None`. No estimate is labeled exact.

Provider-native cache, conversation history, and compaction may improve
performance but are never correctness authority. Thread loss forces a
bootstrap from local authoritative data. The finite planner, action, source,
reference, and ledger limits are enforced independently; exhaustion is an
explicit content-free failure rather than silent omission of required data.

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
