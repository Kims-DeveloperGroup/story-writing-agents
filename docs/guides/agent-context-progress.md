# Agent-context progress

> **Availability:** the installed shell automatically shows live context
> progress for contextual ordinary, routed, retrieval, prompt-debate, and
> draft-debate work. There is no progress command or setting. Python callers
> can observe the same typed lifecycle by passing the optional synchronous
> `context_event_sink` callback documented in the
> [runtime API](../reference/runtime-api.md#context-lifecycle-observer).

For the selection, search/read, candidate-delta, modification, and reference
scenarios surrounding these events, start with
[Bounded agent context](agent-context.md).

Key terms: **operation**—one logical invocation by one agent; **selected**—a
logical source accepted by the current bootstrap or delta plan;
**searchable**—an authorized source not projected by that plan but still
available through bounded search/read; **checkpoint**—content-free state that
allows a later compatible invocation to send a delta instead of another
bootstrap.

## What users can and cannot see

Users can see whether an invocation bootstraps or uses a delta, how many logical
sources and bytes it selected, how many sources remain searchable, when an
agent searches or reads, whether its answer passed authorization, and whether
the context checkpoint advanced. Debate progress also names the canonical
agent, round, and phase, so interleaved invocations remain distinguishable.

Users cannot use progress to read a prompt, query, candidate, excerpt,
mandatory-reference body, provider response, source or reference path, source
digest, private agent state, exception detail, or provider-thread identity.
Progress does not prove that a provider understood a source, and `answer
accepted` does not mean the checkpoint or a story artifact has committed.

Progress is observation, not another context mechanism. Enabling it adds no
provider call, result field, story content, conversation event, debate record,
or second ledger. The shell does not provide post-hoc lifecycle inspection.
Terminal software may retain visible lines in scrollback, and a Python caller
may deliberately retain callback values in its own storage.

## Read the live shell output

Counts vary with the workspace and operation. A compatible ordinary follow-up
that needs no search can look like this:

```text
[context] delta: 4 selected / 21,192 bytes; 11 searchable
[context] answer accepted
[context] checkpoint advanced
```

`delta` means the provider binding and prior READY checkpoint were compatible.
The selected count is the current plan's logical-source count, not a claim that
every byte was newly transmitted. `answer accepted` means authorization,
source revalidation, and domain validation succeeded. Only `checkpoint
advanced` reports the context commit.

The focused shell uses its established retrieval presentation for SEARCH and
READ. Generic context action lines are validated but suppressed, so each action
appears once:

```text
[context] bootstrap: 2 selected / 1,024 bytes; 1 searchable
[context] Retrieval search action 1 started.
[context]
  Retrieval search action 1 completed: 1 hits; 40 observation bytes; truncated: no.
[context] Retrieval read action 2 started.
[context]
  Retrieval read action 2 completed: 1 turns; 2 events; 80 observation bytes; truncated: no.
[context] answer accepted
[context] checkpoint advanced
```

Debate context actions use the compact generic wording `search action N
started`, `search action N completed: H hits; truncated: yes|no`, `read action
N started`, and `read S sources / B bytes`.

Every logical debate-agent invocation has its own identity. Output may
interleave while each operation keeps its own order:

```text
[context] lead_orchestrator · round 0 · draft: bootstrap: 0 selected / 0 bytes; 0 searchable
[context] lead_orchestrator · round 0 · draft: search action 1 started
[context] plot_architect · round 0 · audit: bootstrap: 1 selected / 1 bytes; 0 searchable
[context] plot_architect · round 0 · audit: answer accepted
[context] lead_orchestrator · round 0 · draft: search action 1 completed: 2 hits; truncated: no
[context] plot_architect · round 0 · audit: checkpoint advanced
```

Failure markers use fixed content-free wording. Applicable markers precede one
terminal `checkpoint not advanced` line:

```text
[context] source invalidated
[context] provider context tainted
[context] checkpoint not advanced
```

Other fixed markers are `context budget exhausted` and `context actions
exhausted`. A failure before a normal plan can make one of these failure facts,
or `checkpoint not advanced`, the first observed event. No success line follows
a terminal failure.

## External and internal state map

The internal lifecycle is `PREPARE -> PLAN -> INVOKE/ACTION -> VERIFY ->
COMMIT|FAIL`. It is not a list of equally visible terminal states:

| Internal stage | Typed event or behavior | User-visible meaning |
| --- | --- | --- |
| `PREPARE` | Validate the callback, scope, inputs, authorities, and mandatory-reference request. There is no distinct public PREPARE event. | Rejection here performs no contextual provider work. Some source failures can instead begin the FAIL projection. |
| `PLAN` | `bootstrap` or `delta` is first on a normal path. An `omission` event follows when at least one searchable source was not projected. | The shell renders the plan line once; it does not repeat the same omitted count for `omission`. |
| `INVOKE/ACTION` | A provider may answer or issue paired, monotonically indexed `search_started`/`search_completed` and `read_started`/`read_completed` actions. | Search/read lines describe runtime-authorized work, not arbitrary filesystem access. A no-search answer can move directly from PLAN to VERIFY. |
| `VERIFY` | `answer_authorized` follows action authorization, final source revalidation, and domain-result validation. | Rendered as `answer accepted`; references and the checkpoint still must complete. |
| `COMMIT` | `checkpoint_advanced` is emitted only after mandatory references complete and the READY checkpoint commits. | The context state can support a later compatible delta. |
| `FAIL` | `invalidated`, `budget_exhausted`, `action_exhausted`, and `tainted` are applicable facts; `checkpoint_not_advanced` is terminal. | No READY checkpoint was accepted for this attempt. Retry begins a newly authorized operation. |

The controller correlates each stream by workspace, session, subject kind,
subject, agent, and operation ID. Debate events must also carry their exact
phase and nonnegative round. It validates operations independently even when
their lines interleave: SEARCH and READ start/completion pairs must share an
action index, action indexes advance monotonically, an answer must precede a
successful checkpoint, each operation has exactly one terminal checkpoint
outcome, and nothing may follow that terminal event.

## Observe progress from Python

The observer is available on both `StoryRuntime` and a session-bound
`RuntimeSession` for `converse`, `converse_with_retrieval`, `run_prompt`, and
`run_draft`. It is keyword-only and optional on every form. The three observer
contracts are supported only from their direct module:

```python
from story_writing_agents.agent_context.service import (
    ContextExecutionEvent,
    ContextExecutionEventKind,
    ContextExecutionEventSink,
)

events: list[ContextExecutionEvent] = []
observer: ContextExecutionEventSink = events.append

result = await runtime.converse(
    "story_review",
    "c_main",
    "lead_orchestrator",
    "Make the ending quieter.",
    context_event_sink=observer,
)

committed = [
    event
    for event in events
    if event.kind is ContextExecutionEventKind.CHECKPOINT_ADVANCED
]
```

Do not import these names from `story_writing_agents.agent_context` or the
project root; neither facade exports them. The callback runs synchronously and
inline, receives one frozen event, and must return exactly `None`.

### Identity and fact meanings

Every event has `kind`, `scope`, and a required `operation_id`. `scope`
contains content-free workspace, session, subject-kind, subject, and canonical
agent identity. Conversation events have `phase=None` and
`round_number=None`; debate events require both values.

Nonapplicable or unavailable optional facts are `None`, never inferred zeroes
or estimates:

| Event kind | Applicable facts |
| --- | --- |
| `bootstrap`, `delta` | Selected logical source count/bytes; omitted-searchable source count/bytes; mandatory-reference count/selected bytes. |
| `omission` | The positive omitted-searchable source count and its bytes, equal to the preceding plan facts. |
| `search_started` | One-based `action_index`. |
| `search_completed` | Matching `action_index`, exact hit count, serialized observation bytes, and `truncated`. |
| `read_started` | One-based `action_index`. |
| `read_completed` | Matching `action_index`, retrieved source count/selected bytes, complete conversation turn/event counts, and serialized observation bytes. |
| `answer_authorized` | Matching `action_index`. |
| `invalidated`, `budget_exhausted`, `action_exhausted`, `tainted` | Identity only. |
| `checkpoint_advanced`, `checkpoint_not_advanced` | Optional exact aggregate `provider_input_tokens`. |

Provider input tokens are reported only on a terminal event and only when every
submitted physical call reports an exact nonnegative input-token count. That
set includes a mandatory-reference prelude when one was submitted. If any call
omits exact usage, the aggregate is `None`; the runtime does not estimate it.
The shell deliberately omits operation IDs, omitted bytes, mandatory-reference
counts, observation bytes on generic actions, and provider tokens from its
compact copy. A caller that needs those content-free facts must inspect them in
the live callback.

## Observer failures and recovery

The runtime rejects a noncallable, declared coroutine, declared async-generator,
or asynchronously callable observer with `TypeError` before contextual session,
persistence, provider, or transcript effects. Supplying an observer to a
backend without contextual execution raises `BackendProtocolError` before those
effects. Omitting the observer preserves the established provider calls, return
values and schemas, persistence, and retrieval/debate progress behavior.

Once delivery begins, an exception raised by the callback, an awaitable or
async-generator result, or any non-`None` result is an observer failure:

- Before an authoritative answer/checkpoint, the operation aborts and the
  callback exception or validation `TypeError` is re-raised. Submitted provider
  context is tainted when required and the checkpoint does not advance.
- The surrounding advisory/retrieval workflow may already have persisted its
  user event; it records only its established safe runtime-error outcome.
  Debate execution best-effort marks an uncommitted run interrupted.
- Failure while receiving `checkpoint_advanced` propagates after the context
  service has committed READY. It cannot roll that checkpoint back, emit a
  false `checkpoint_not_advanced`, or taint the committed context. The outer
  operation still reports the observer failure rather than pretending delivery
  succeeded.
- If an independent runtime failure already exists, a failure while delivering
  terminal failure progress is suppressed. The original provider, source,
  cancellation, or domain failure remains primary.

The installed controller treats malformed or failed progress as an operational
error instead of rendering untrusted values. Inspect the ordinary domain state
and retry the original request when appropriate; progress itself has no resume
cursor or replay API.

## Privacy, references, and limits

The event schema admits identities, fixed states, booleans, and nonnegative
counters only. It has no field for story or provider text or a source path. The
runtime's existing debug log separately records fixed `agent_context` outcome
categories and safe session/conversation-or-debate/agent identities. It does
not store the observer's operation ID or detailed counters and is not observer
history. See
[Privacy and provider exposure](../operations/privacy-and-provider.md#agent-context-progress-privacy).

Mandatory `${path}` and `${path}#Lx-Ly` references remain exact, ordered,
freshly authorized, and separately accounted for every addressed invocation. A
checkpoint or optional search cannot replace, reorder, summarize, or satisfy
them. Their bodies remain outside lifecycle events.

Contextual lexical search/read is also independent of optional conversation
RAG. Enabling, disabling, or arming RAG does not change the observer contract.
Capturing an included focused-conversation snapshot performs no search by
itself; the subsequent contextual invocation may still search and read its
authorized writing artifacts, focused conversation, active debate, or the
invoking agent's own state.

The lifecycle does not add semantic search, provider retry, durable free-form
memory, arbitrary filesystem discovery, another agent's private state, or
provider-opaque history as a correctness source. It reports runtime decisions;
it does not expand authority.
