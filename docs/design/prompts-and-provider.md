# Prompts and provider boundary

> **Status:** this page documents prompt/provider/context components and
> historical execution accounting. `StoryRuntime.lead_turn()` and confirmed
> specialist consultations use the current Codex/Antigravity binding, context,
> reference, and exchange-capture path. The interactive shell now calls that
> same Lead path from ordinary focused input and supports canonical references;
> management commands remain provider-free.

Key terms: **definition**—stable context for one role; **fingerprint**—its
hash-derived identity; **binding**—the provider object for a definition;
**visible context**—events explicitly sent in a request; **provider-private
history**—state behind a provider thread ID.

<a id="exact-provider-context-accounting"></a>

## Exact provider-context accounting

This section records the immutable historical #381 accounting baseline. Later
children implemented selection, omission, a typed compact action wire, a
content-free ledger, and live progress without changing the encoder or baseline
workload. Current wire-v7 behavior is described in
[Agent-context design](agent-context.md#typed-provider-wire-v7).

### Model-input boundary and canonical encoding

For each physical provider call, the provider-neutral model-input boundary is
the exact bytes of three inputs:

1. the staged binding instruction, measured from the same snapshot written for
   the provider (`AGENTS.md` for Codex, or the selected custom-agent definition
   for Antigravity);
2. the canonical output schema actually prepared for that provider; and
3. the canonical inner turn-prompt JSON.

The exact formulas are:

```text
gross model input = binding instruction + output schema + inner turn prompt
non-reference model input = gross model input
                            - exact raw mandatory-reference source bytes delivered
```

Only the raw strict-UTF-8 bytes of mandatory referenced source text actually
delivered to an agent are subtracted. Reference paths and ranges, descriptors,
JSON keys and envelopes, punctuation, escaping, instructions, schema bytes,
repeated material, and retrieved non-reference text all remain counted.

`canonical_provider_json()` is the sole encoder for accounted schemas and inner
prompts. It emits strict UTF-8 JSON in insertion order with no insignificant
whitespace: `ensure_ascii=False`, `allow_nan=False`, and
`separators=(",", ":")`. It does not sort keys. Rejected values produce a
fixed content-free diagnostic that does not echo the rejected input.

For Antigravity, vendor-specific preparation calls the adapter-owned schema
renderer before constructing `ProviderInvocation`. It preserves domain
constraints while rendering singleton null/integer enums for native function
conversion. The invocation, request capture, output-schema accounting, argv,
and schema echo all use those rendered bytes. The original domain/Codex schema
is unchanged; its size is not a conservative transport bound (the captured
canary grew from 1,314 to 1,326 bytes). This changes neither the shared accounting
definitions nor payload encoding. See the exact
[rendering rules](../guides/antigravity.md#shared-structured-output-contract).

Codex sends the inner prompt itself on standard input, so its diagnostic
transport byte count is the inner-prompt count. Antigravity passes that exact
inner JSON string as one `--print` argument with empty stdin.
`actual_transport_utf8_bytes` counts those payload argument bytes, without an
outer event wrapper or added newline. This diagnostic count is not added again
to provider-neutral gross model input. Process-start failure, including an OS
argument-size rejection, occurs before submission and has no actual transport
count.

Every serialized byte is attributed once. Binding attribution separates
binding structure, role system prompt, persona, response language, each core
document under its canonical identity, and provider constraints. Schema bytes
belong to output schema. Turn-prompt attribution separates control and envelope
structure, current input, candidate, debate messages, conversation context,
agent state, retrieved observation, RAG context, mandatory-reference
descriptors, and mandatory-reference bodies.

### Call state, observation, and provider usage

`prepared` means the exact accountable input was constructed but provider
submission was not established. `submitted` means the provider process was
started; actual transport bytes and provider usage can exist only in that
state. Every canonically accountable physical call is offered exactly once on
success, failure, or cancellation to an optional internal observation sink. An
absent sink or any sink failure cannot change the domain result. An input that
cannot be canonically encoded has no accounting record. Current contextual
execution separately persists a content-free ledger v2; that ledger does not
persist prompt, source, candidate, reference, or response prose. The independent
agent-exchange diagnostic store does persist bounded provider-neutral request
and response projections as described below; it is not part of accounting or
checkpoint authority.

Provider token usage is evidence reported by the provider, not an estimate.
Only recognized counters whose values are exact nonnegative integers (and not
booleans) are accepted. A missing, malformed, negative, or unrecognized value
remains `None`; the runtime never derives a replacement. Provider values must
be treated as reported snapshots. Antigravity reads only the terminal JSON
object's `usage`: `input_tokens`, `output_tokens`, and `cache_read_tokens`
(the last maps to `cached_input_tokens`). Missing or invalid counters stay
unavailable; values are not inferred from response text or intermediate events.
Historical streaming usage for a provider conversation may be cumulative and
must not be summed as if each snapshot were an independent call delta.

<a id="provider-context-baseline-v1"></a>

### Fixed provider-free baseline v1

The version-1 baseline runs real `StoryRuntime`, `AgentBackend`, binding, and
payload construction with deterministic provider responses. It blocks network
and subprocess provider execution, disables semantic RAG, and fixes identities,
timestamps, paths, revisions, schemas, and response decisions. Its controlled
timing is fixture data for repeatability, not a measurement of provider or
runtime performance.

The 14 scenarios produce 56 physical provider calls:

| Scenario | Calls |
| --- | ---: |
| Fresh ordinary conversation | 1 |
| Continued ordinary conversation | 1 |
| Retrieval with a direct answer | 1 |
| Retrieval with search, transcript read, and answer | 3 |
| Whole-file reference | 2 |
| Ranged-file reference | 2 |
| Multiple ordered references | 2 |
| Straight consensus debate | 5 |
| Conflict, revision, and consensus | 10 |
| Terminal deadlock | 9 |
| Outline modification | 5 |
| Character-sheet modification | 5 |
| Narrative-rule modification | 5 |
| Create-only manuscript revision | 5 |

Its frozen cumulative evidence is:

| Measurement | Exact value |
| --- | ---: |
| Staged binding instructions | 54,563 UTF-8 bytes |
| Output schemas | 21,497 UTF-8 bytes |
| Inner prompts | 104,677 UTF-8 bytes |
| Fixture transport | 104,677 UTF-8 bytes |
| Gross model input | 180,737 UTF-8 bytes |
| Raw mandatory references | 162 UTF-8 bytes |
| Non-reference model input | 180,575 UTF-8 bytes |
| Controlled provider elapsed value | 257,000,000 ns |
| Exact later 50% ceiling | 90,287 UTF-8 bytes |

The ceiling is half of the cumulative non-reference baseline, rounded down.
The current optimized golden is 84,053 non-reference bytes (84,215 gross),
below both the 90,287 hard ceiling and 84,104 engineering target. Provider
usage is unavailable in both fixed fixtures and remains `None`.

The canonical verification is the
[baseline test](../../tests/test_agent_context_accounting.py), and the
versioned evidence is the
[v1 golden report](../../tests/fixtures/agent_context/provider_context_baseline_v1.json).
The report is content-free: it stores counts, call categories, scenario IDs,
and stable source identities, never prompt, source, reference, or reply bodies.

In the historical baseline fixture, stable prompt context and dynamic call data
used different channels: role prompts, personas, and role-allowlisted current
core documents became immutable content-backed references, and the backend
staged those bytes at binding while request JSON entered per call. The fixture
retains that exact behavior for comparison; it is not the built-in runtime path
described next.

## Current contextual binding

The preceding byte table is the immutable historical #381 baseline. The
built-in runtime now constructs definitions with `provider_managed_context=True`
and no mutable `context_document_files`. Role prompt, persona, language,
provider restrictions, and typed wire-v7 protocol remain stable binding material.
Current core documents are P2 authoritative sources selected by the context
planner; manuscripts and other additive sources are SEARCH-first.

A direct custom `AgentDefinition` can still bind explicit
`context_document_files`. That eager path preserves its exact bytes,
fingerprint, staging, and provider behavior for compatibility, but it is not how
`StoryRuntime` constructs the four built-in roles. Core publication therefore
changes contextual source revision/delta facts instead of permanently restaging
mutable story bytes as a new built-in definition.

## Binding resource lifecycle

The runtime still owns desired/current/retiring bindings and leases one exact
binding through each operation. Collaboration proposals bind both the stable
definition fingerprint and the backend's content-free execution-profile
fingerprint; confirmation rejects either kind of drift. A contextual bootstrap
always starts a distinct provider-thread epoch; a compatible READY checkpoint
and provider thread permit a delta. Missing, corrupt, mismatched, stale,
tainted, or excessive-depth state cannot authorize omission and forces
bootstrap. Provider-native cache/history may accelerate a call but is not
authoritative context.

<a id="conversation-turn-flow"></a>

## Provider-context component

A Python lead turn or confirmed specialist/synthesis invocation passes focused
identity, route, input, and current storage authorities into the
provider-neutral context service. It does not consume the optional
conversation-RAG collaborator. The service captures current material, builds a
content-free manifest, and plans bootstrap/delta. If selected material is
insufficient, typed wire-v7 SEARCH/READ actions can add exact authorized evidence
from route-permitted sources. Focused Lead input and confirmed collaboration in
the current shell enter this same provider-context flow.

The dynamic workflow material is role-specific. A specialist assignment
receives one disclosed instruction, exact ordered input IDs, typed assessment
contract, and only its assignment-local story references. `lead_synthesis`
receives every typed reply in canonical order, direct story-artifact inputs,
mandatory unresolved IDs, and at most one same-lineage continuation contract.
Provider output never supplies confirmation, runtime IDs/budgets, write/Git
authority, or a call to another agent.

`lead_front_channel` receives the current command, identity, content-free input
aliases, a strict seven-field semantic answer contract, and four canonical-core
status rows. Inventory alone supplies no story body. The lead must classify the
turn as `story_specific` or `workflow_meta`; runtime performs no NLP relabeling.
For story-specific results the provider selects the minimum ordered current
evidence IDs supporting `verified`, `missing`, `empty`, `incomplete`, or
`conflicting`. For workflow/meta it must use `not_applicable`, no IDs, and no
proposal. Ambiguous scope is handled by the role prompt—choose story-specific
and ask a grounded question—not by runtime inference.

The lead role prompt never grants tool or agent-dispatch authority. Provider
SEARCH/READ/ANSWER values are requests to the context service. A proposal in
the final answer is semantic intent; runtime validates evidence, creates the
authoritative proposal, and waits for the user's exact structured decision.
Only then does runtime call selected specialists. Synthesis receives the
proposal's scope/status/evidence IDs as an unchanged answer contract and keeps
them separate from specialist reply-event attribution.

Established conversation retrieval remains responsible for its cited
conversation observations. Optional `rag_context` is independent semantic RAG
input. Contextual continuation frames carry only the newly authorized action
observation; they do not resend the initial payload.

The action response itself is typed rather than prompt-decoded shorthand. Its
closed root has required `correlation` and `action`; the latter is a closed
SEARCH, READ, or ANSWER variant selected from the per-call schema. ANSWER puts
the complete semantic object directly in `action.result` and supporting
successful READ handles in `action.used_handles`. Runtime validates the exact
correlation, issued handles, evidence IDs, Lead evidence matrix, input aliases,
artifact authority, and later confirmation boundaries. Provider schema
acceptance is therefore necessary but never sufficient for a valid Lead turn.

<a id="story-session-continuity-envelope"></a>

## Story-session capture and compact wire

`StorySessionContext` remains a package-internal capture and direct-request
compatibility value. It can represent focused included/fresh/omitted mode,
bounded complete conversation pairs, invoking-agent owner outcomes, and current
core identities. It is not the current built-in provider payload.

The contextual service translates authoritative capture into
`agent-context-wire-v7`: compact required input/candidate and selected P2
material, content-free catalog/record changes, and bounded observations. Optional
P3 history and invoking-agent state remain SEARCH-first. Separately, ordinary
Lead turns and consultation/artifact-review synthesis carry required `h`:
exact retained active/archived session history plus current directives and
complete core bytes, even for `workflow_meta` or a zero P3 budget. Compatible
calls share the session's Lead thread and send unseen events; a fresh thread
receives all retained history. Neither form supplies current receipts or
confirmation authority. Other source deltas use revisions,
record changes, tombstones, and an exact candidate pointer or beneficial
lossless single-splice. Invocation and evidence scope stays conversation-bound;
specialists and consensus retain their separate scopes. See
[Agent-context design](agent-context.md#typed-provider-wire-v7).

<a id="explicit-reference-provider-flow"></a>

## Explicit-reference provider flow

A referenced invocation adds a separate prelude under the same logical
operation. The provider receives only the exact ordered path/range indicators
and correlation, must return the matching read action, and receives current
selected strict-UTF-8 text only after runtime authorization. The substantive
contextual payload then carries those values as
`untrusted_writing_artifact_context`.

Reference bodies remain outside ambient selection, candidate delta, checkpoint
suppression, and SEARCH/READ authority. Every addressed debate, specialist, and
synthesis invocation performs its own prelude/read/receipt/revalidation. A
checkpoint, previous provider copy, or another agent's receipt cannot satisfy
the reference. See
[Mandatory-reference separation](agent-context.md#mandatory-reference-separation).

An explicit missing `${path}` fails the reference operation before the
substantive lead call; it is not converted into provider-declared `missing`.
Exploratory absence instead requires an absent canonical-core status or a
complete non-truncated zero-hit SEARCH over a scope that includes writing
artifacts.

## Debate context

Each draft, audit, proposal, synthesis, and finalization call has its own
session/debate/agent/phase/round/operation identity. Required current work and
the complete logical candidate are always reconstructable. Compatible later
calls can use an exact unchanged pointer or deterministic single-splice; unsafe
or non-beneficial cases send the full candidate. New public debate records and
the receiving agent's own state follow compact record rules, while older
authorized facts remain searchable. Included conversation capture itself
performs no search, but the subsequent debate invocation may search its
authorized focused conversation, writing artifacts, active debate, or own
state.

<a id="agent-exchange-capture-boundary"></a>

## Agent-exchange capture boundary

`AgentBackend` owns two automatic best-effort capture seams: the established
structured-call path and the provider-managed contextual physical-call path.
Each seam records the request immediately before `adapter.invoke()`. When the
adapter returns an exact `ProviderResult`, it records that completed result
immediately, before accounting observation, structured/domain decoding, the
separate rejected-response artifact, or provider-thread persistence. One
closed terminal attempt then classifies the physical call as `accepted`,
`rejected`, `provider_failure`, `cancelled`, or
`post_response_runtime_failure` without changing the provider result or
exception.

The request diagnostic is canonical JSON containing only `call_kind`,
`payload`, `prior_thread_id`, and `schema`. A completed result contains
`actual_transport_utf8_bytes`, `response_text`, `thread_id`, and nullable
validated `usage`. A recovered Antigravity native `ERROR` also carries
`response_diagnostic`: integer `exit_code` and `stdout`/`stderr` streams, each encoded as
`{"encoding":"utf-8","data":"…"}` or, for invalid UTF-8 bytes,
`{"encoding":"base64","data":"…"}`.

A typed completed Antigravity invocation failure can attempt a response with
only `provider_failure: true` and that same `response_diagnostic` before its
fixed `provider_failure` outcome. This does not construct a valid semantic
`ProviderResult` or authorize retry. Failures before process completion,
timeouts, and cancellations supply no partial bytes through the shared runner;
capture invents no response. The terminal diagnostic still contains only
`outcome` and one fixed code. Normal Codex streams and the native proof
transcript are not copied by this capture seam.

Storage may retain only the longest complete UTF-8 prefix at or below 262,144
bytes; its full byte/code-point counts and SHA-256 describe the complete capture
input. Base64 is reversible encoding, not redaction. Native bytes remain private
within the existing exchange rotation and retention limits. The independent
`provider_error_responses` store still accepts only returned response bodies
rejected by shared decoding; pre-result native failures do not enter that store.

All records for a captured call share a fresh `exchange_<32 lowercase hex>`
identity plus the exact supplied session, conversation/debate scope, agent, and
provider. A mandatory-reference prelude is a separate physical call but
inherits the same outer logical scope as its substantive call. Capture has no
retry or replay path, and any capture/storage failure is suppressed after its
state transition. Missing records or sequence gaps therefore do not prove that
no provider call occurred. See the exact
[storage envelope and bounds](../reference/configuration/session-storage.md#agent-exchange-storage)
and [privacy boundary](../operations/privacy-and-provider.md#agent-exchange-privacy).

## Provider process boundaries

The backend launches a constrained Codex subprocess from isolated staging.
Verified stable context reaches its temporary top-level `AGENTS.md`; request
JSON reaches standard input. The exact per-call schema is passed by
`--output-schema <schema-file>` and the structured last message becomes the
provider-neutral result. OpenAI Structured Outputs requires a supported root
object schema, required fields, closed objects, and explicitly typed array
items; Story generates those constraints for every nested action/result node.
See the official
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).
Replies still pass Story's correlation, handle, evidence, semantic, and phase
checks.
The exact sandbox, approval, tool, configuration, and instruction controls live
in [Privacy and provider exposure](../operations/privacy-and-provider.md).

The Antigravity adapter binds the same verified definition and request models
to an isolated session/agent/fingerprint provider root. It installs the bound
definition as `.agents/agents/<agent-id>/agent.md`, selects that custom main
agent with `--agent`, and launches `agy` without a shell from the nested
adapter-owned workspace. A fresh thread uses `--new-project`; exact resume uses
`--conversation <id>`, whose associated project Antigravity reopens. Story
does not register the canonical story workspace root or pass `--project`.
Story passes the exact payload as one `--print` argument with empty stdin,
selecting `--input-format text --output-format json`. The adapter-rendered
canonical per-call schema is passed inline with `--json-schema`. Exactly one
UTF-8 JSON object must contain a safe conversation ID matching the retained ID
on resume, object `structured_output`, and an object `json_schema` whose
canonical bytes exactly echo the rendered request schema. Duplicate fields,
non-finite values, and trailing data are rejected; there is no string-response
fallback. Native `SUCCESS` is the normal path. The known malformed-function-call
`ERROR` is recoverable only after exit zero and matching current-call native
request/finish/completion proof through the adapter's bounded, no-follow reader.
Missing, unsafe, stale, or unknown evidence leaves the call failed. This
format-dependent proof ignores private thinking and is not tool/security
attestation. Recovery adds no model call; every returned result still passes
shared correlation, domain, evidence, and authority validation. See the
[recovery gates](../guides/antigravity.md#recover-a-completed-native-error).

This output has no `init` or step metadata to attest agent, cwd, permission
mode, or tool behavior. The selected custom agent, isolated state/cwd,
sandbox, plan-mode, and disabled-slash flags remain configured, without an
approval bypass. Their presence does not prove provider capability removal;
CLI 1.1.27 warns that plan mode has no effect with slash commands disabled.
The full payload, schema, state path, optional model/effort, and conversation
ID are process-visible arguments. Exact setup and privacy limits are in the
[Antigravity guide](../guides/antigravity.md).

Antigravity's staged `ROLE_SYSTEM_PROMPT` rendering changes the exact
common-policy clause `invoke another agent, provider subagent, tool, or model.`
to `invoke another agent, provider subagent, or model.` before byte accounting.
It also directs structured delivery through `finish` with the complete supplied
outer schema. Contextual specialist instructions identify their assessment as
`action.result`, inside the root `correlation` and `action` fields; they do not
ask `finish` to receive the inner assessment alone. Its compact provider-managed
constraints carry the same rule and omit the tool ban. Shared policy/prompt
sources, workspace initialization, Codex rendering and stdin/JSONL transport,
and provider-neutral domain semantics remain unchanged. Existing workspace
prompt files are not migrated or rewritten; these transformations apply to the
Antigravity staged definition.

Runtime-built contextual debate calls use typed wire v7 for task/input,
candidate pointer/splice/full fallback, selected sources, and record changes.
They do not repeat stable role prompts, persona, language directives, or
unchanged core-document text. `story_session_context` is an internal capture
and direct-compatibility value, not the current provider payload.
A referenced backend call adds only the separate
`untrusted_writing_artifact_context` field described above to its substantive
payload; the content-free prelude uses its own payload and schema.

Neither boundary makes a provider or executable trusted. Hashes detect change
but do not encrypt or authorize content. Provider transcripts may be retained,
and ending a story session keeps local thread references. Antigravity-native
state is retained on backend close; a tainted thread or changed definition
starts a fresh provider conversation without deleting prior provider data.

Exact models are in [Agent contracts](../reference/agent-contracts.md); privacy
implications are in [Privacy and provider exposure](../operations/privacy-and-provider.md),
and provenance states are in
[Canonical writing artifacts](../reference/writing-artifacts.md).
