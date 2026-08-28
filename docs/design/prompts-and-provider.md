# Prompts and provider boundary

> **Availability:** file-referenced definitions, provider-specific prompt
> binding, advisory turns with optional bounded retrieval, context-aware
> debates, and compatible threads are implemented for Codex and Antigravity.
> `StoryRuntime` turns inline canonical reference indicators into one
> repository operation and supplies a fresh authorized coordinator to every
> addressed provider invocation. In provider-bound shell prompt spans,
> `$`/`$$` discovery and viewer-driven attachment construct those same
> indicators before normal submission.

Key terms: **definition**—stable context for one role; **fingerprint**—its
hash-derived identity; **binding**—the provider object for a definition;
**visible context**—events explicitly sent in a request; **provider-private
history**—state behind a provider thread ID.

<a id="exact-provider-context-accounting"></a>

## Exact provider-context accounting

This accounting slice does not change what users can ask agents to do, which
material agents search or read, prompt selection, provider-call order or count,
provider-thread scope, or agent-visible story output. It adds content-free
internal measurement and a fixed comparison baseline. Context selection,
omission, compaction, and durable accounting history are not implemented here.

### Model-input boundary and canonical encoding

For each physical provider call, the provider-neutral model-input boundary is
the exact bytes of three inputs:

1. the staged `AGENTS.md` binding instruction, measured from the same snapshot
   that is written for the provider;
2. the canonical output schema; and
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

Codex sends the inner prompt itself on standard input, so its diagnostic
transport byte count is the inner-prompt count. Antigravity wraps that same
inner JSON string in its provider-owned outer NDJSON input event. The outer
NDJSON byte count is retained only as provider-specific transport evidence; it
is not added to provider-neutral gross model input.

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
cannot be canonically encoded has no accounting record. There is no durable
ledger or accounting persistence yet.

Provider token usage is evidence reported by the provider, not an estimate.
Only recognized counters whose values are exact nonnegative integers (and not
booleans) are accepted. A missing, malformed, negative, or unrecognized value
remains `None`; the runtime never derives a replacement. Provider values must
be treated as reported snapshots. In particular, Antigravity streaming usage
for a provider conversation may be cumulative and must not be summed as if
each snapshot were an independent call delta.

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

The ceiling is half of the cumulative non-reference baseline, rounded down; it
is a comparison target for later work, not a reduction delivered by this
slice. Provider usage is unavailable in the fixture and remains `None`.

The canonical verification is the
[baseline test](../../tests/test_context_accounting_baseline.py), and the
versioned evidence is the
[v1 golden report](../../tests/fixtures/agent_context/provider_context_baseline_v1.json).
The report is content-free: it stores counts, call categories, scenario IDs,
and stable source identities, never prompt, source, reference, or reply bodies.

Stable prompt context and dynamic call data use different channels. During
definition construction, role prompts and personas are read once, while the
artifact repository selectively reads only present safe current core
documents allowlisted for that role. Every result becomes an immutable
content-backed reference containing a lexical absolute path, SHA-256, and the
exact captured bytes. The backend stages those bytes at binding and never
rereads the canonical artifact paths. Request JSON enters separately for each
call.

## Bound role context

When present and safe, the plot architect receives global and part outlines; the
character custodian gets character sheets; the execution specialist gets
narrative rules; and the lead gets all four core documents. `read_current()`
touches only those requested core paths, so accumulating manuscripts are not
read while definitions are assembled. Absent or unsafe core files are omitted;
manual, pre-provenance, published, and hash-divergent current bytes are bound
without fabricating publication history. Definitions include no unrelated
workspace paths.

The fingerprint covers agent, provider, language, component identities, and
hashes. A nonzero provider-route generation is also included, while generation
zero preserves the legacy hash exactly. Model and effort are deliberately not
fingerprint inputs. Editing a role prompt or persona changes the next
definition. An external manual core edit made while workspace processes are
stopped changes the next filesystem snapshot and definition fingerprint;
generic `Workspace` writes reject canonical artifact paths. Later agent
republication can replace the current bytes and record provenance, but
provenance is not required for binding. Either effective-context change starts
a fresh provider thread for that agent.
Provider conversation state remains partitioned by story session and agent.

## Binding resource lifecycle

The runtime bounds binding and prompt-staging ownership per canonical agent.
These internal states do not add a command, status field, or durable record:

| Internal state | Transition and user-visible meaning |
| --- | --- |
| Desired/current | The newest requested fingerprint becomes the definition for new work. At quiescence, the runtime retains at most one current binding for that agent. |
| Single-flight bind | Concurrent requests for the same fingerprint await one bind and share its immutable staged definition. |
| Operation lease | Debate invocation, advisory conversation, and retrieval each retain the exact selected binding until that operation exits on success, failure, or cancellation. |
| Superseded-active / retiring | A different fingerprint may become current while old work is still running. New work uses the newest desired definition; existing work keeps its original definition and staging until its last lease exits. More than one staging directory may therefore exist during overlap. |
| Retired | An idle superseded or abandoned binding is retired exactly once. Its owned temporary staging is then eligible for best-effort removal and cannot be selected for later work. |

If fingerprints change from A to B and back to A, the newest desired definition
still wins; a compatible binding that remains safely retained may become current
again, while abandoned out-of-order bind results are retired. Repeated
agent-authored core publication therefore does not accumulate idle bindings or
prompt-staging directories without bound. Provider calls, replies, conversation
threads, and durable session state retain their existing contracts.

Snapshot timing matters. Once a definition exists, changing, deleting, or
replacing a source path does not change that definition or the bytes staged for
its binding. A later definition construction performs a fresh verified read and
may produce a different fingerprint. This prevents a path swap between
repository verification and provider staging from changing what the agent
receives.

<a id="conversation-turn-flow"></a>

## Advisory context

The shell passes its focused identity, route, input, and current-session
memory service to `StoryRuntime.converse_with_retrieval()`. Runtime owns context,
memory authorization, and transcript order. Backend holds one provider-thread
lock across the action loop and calls only the synchronous runtime handler; it
never receives a search, catalog, transcript, or filesystem collaborator.

On the established `StoryRuntime` path, every substantive provider turn receives
the bounded belonging-session envelope described below. The first retrieval
call also receives current input. An attempted conversation-RAG preflight adds
its separate bounded `rag_context` beside `story_session_context` on that first
call only. Later calls receive only runtime-authorized observations framed as
untrusted story data and do not repeat input or either context envelope.
Exact payloads and action order live in
[Bounded retrieval](../reference/conversation-retrieval.md). Direct Python
`converse()` with no reference coordinator and its `ConversationRequest`
payload remain one-step; callers that leave the new optional envelope as `None`
retain the legacy payload.

Conversation IDs scope visible events, not provider-private history. One
session-and-agent thread can span subjects and debates. Direct routing chooses
a responder; it is not a private provider channel.

<a id="story-session-continuity-envelope"></a>

## Story-session continuity envelope

`StoryRuntime` builds one package-internal schema-v1 `StorySessionContext` for
every substantive advisory, retrieval, and consensus phase. The provider
payload contains it under the top-level key `story_session_context`. It carries:

- `schema_version: 1`, `scope: "story_session"`, and the exact `session_id` and
  receiving `agent_id`;
- `owner_outcomes`, the newest complete suffix belonging only to that agent and
  session, capped at 24 entries and 32,768 compact UTF-8 bytes, with exact
  `total_entries`, `retained_entries`, `omitted_entries`,
  `retained_utf8_bytes`, `truncated`, and `entries` fields;
- `focused_context` in `included`, `fresh`, or `omitted` mode; and
- `core_documents`, the bound canonical `core_documents/...` paths and
  lowercase SHA-256 identities, never their bytes.

The owner byte count covers compact serialized entries and separators but not
the surrounding `[]`; entries are never split. Every owner entry has exactly
`agent_id`, `session_id`, `debate_id`, `phase`, `round`, `revision`, `kind`,
`content`, and `timestamp`. `included` uses the established
[conversation-context policy](../reference/conversation-transcripts.md#context-window-derivation-and-failure-evidence):
the newest at most 24 complete turns and 32,768 combined content bytes, with no
split turn. It adds `conversation_id`, `total_complete_turns`, `retained_turns`,
`omitted_turns`, `retained_utf8_bytes`, `truncated`, and `events`; every event
has `turn_id`, `sequence`, `sender`, `recipient`, `kind`, and `content`. `fresh`
has only `mode` and `conversation_id`. `omitted` has only `mode`. Every
core-document entry has exactly `path` and `sha256`.

The first advisory or retrieval turn in an empty/new focused conversation uses
`fresh`; later normal focused turns use `included`. Normal `/run` is included;
`/run --fresh` is fresh, preserving the focused identity without its transcript;
a context-omitted noninteractive run is omitted. A fresh provider-native thread
therefore remains part of the same story session and can rehydrate owner,
core-identity, and focus facts. It cannot reconstruct another agent's outcomes,
events outside the bounded focused window, provider-native hidden history,
artifact bytes, or core-document bytes.

When the envelope is present, provider payloads omit the duplicate legacy
`agent_state`, `conversation_context`, or `context` field. The request values
retain those established fields for validation and result APIs. A content-free
reference prelude does not carry the envelope; its following substantive call
does. Retrieval call one carries it, and continuation calls do not repeat it.

<a id="explicit-reference-provider-flow"></a>

## Explicit-reference provider flow

An optional referenced invocation adds a two-stage exchange under the same
provider-thread lock. The prelude receives only fixed instructions, one exact
operation/invocation correlation, and ordered canonical path/range indicators.
The repository capability and selected text are absent. The provider must
return the complete exact `read_writing_artifacts` action before the repository
coordinator can deliver anything.

The backend then adds the selected strict-UTF-8 text, in request order, only to
the immediately following substantive payload:

```json
{
  "untrusted_writing_artifact_context": [{
    "reference": {"path": "...", "line_range": null},
    "content": "..."
  }]
}
```

This field is separate from task, user input, candidate, blackboard, agent
state, conversation context, retrieval observation, and bound definition. It
has no instruction authority. Retrieval includes it only on retrieval call one;
later observation calls do not repeat it. A structurally valid substantive
result is recorded and revalidated before return. The exact sequence, failure
rules, and call counts are in the
[backend reference](../reference/backend-and-sessions.md#referenced-invocation-boundary).

The same `BoundAgent` mechanism applies to the lead orchestrator, plot
architect, character custodian, and execution specialist. Role instructions
can explain how to use writing material but are not what enforces the read.
Provider-private or remote history may retain content after it is received;
closing local transient carriers is not remote erasure. See
[Privacy and provider exposure](../operations/privacy-and-provider.md#explicit-reference-data-boundary).

## Debate context field

An included debate retains the same `conversation_context` request value for
every agent phase. Drafting, audits, proposals, synthesis, and finalization all
receive the same captured event snapshot. The runtime does not reread or search
the transcript between phases. Fresh and context-omitted requests omit that
legacy request field, while all runtime-built substantive requests carry the
corresponding `story_session_context` mode.

This context is not concatenated into the bound prompt, phase `task`, original
`user_payload`, current candidate, public blackboard, or owner state. There is
no debate-time retrieval, search result, or hidden memory injection. Exact request
fields are in [Agent contracts](../reference/agent-contracts.md#debate-request).

## Provider process boundaries

The backend launches a constrained Codex subprocess from isolated staging.
Verified stable context reaches its temporary top-level `AGENTS.md`; request
JSON reaches standard input. Replies pass schema, correlation, and phase checks.
The exact sandbox, approval, tool, configuration, and instruction controls live
in [Privacy and provider exposure](../operations/privacy-and-provider.md).

The Antigravity adapter binds the same verified definition and request models
to an isolated provider-state root. It launches `agy` without a shell, sends
story payloads through standard input using stream JSON, and requests streamed
JSON output. Runtime-owned JSON schema, provider-state path, optional
model/effort, and provider conversation ID remain process arguments. Slash
commands are disabled, plan mode is selected, and sandbox controls are applied. Exact
setup, precedence, preflight, and lifecycle contracts are in the
[Antigravity guide](../guides/antigravity.md).

Runtime-built debate JSON contains task, input, candidate, blackboard, and the
single bounded `story_session_context` envelope. It does not duplicate owner
state or focused context into legacy payload fields and does not repeat role
prompts, persona, language directives, or core-document text.
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
