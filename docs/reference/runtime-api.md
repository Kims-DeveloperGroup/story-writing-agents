# Runtime API

> **Availability:** `StoryRuntime` provides unchanged one-step and additive
> bounded-retrieval turns, plus context-aware debates. Interactive `$`/`$$`
> discovery is a controller/terminal workflow and adds no runtime method.

Key terms: **context-omitted run**—no `conversation_context`; **included
snapshot**—one transcript window reused for a debate; **progress
sink**—a synchronous observer; **retrieval turn**—one bounded answer.

## Construction and entry points

```python
StoryRuntime(workspace: Workspace | str | pathlib.Path,
             backend: AgentBackend | None = None, *,
             conversation_event_store: ConversationEventStore | None = None,
             conversation_storage_services: ConversationStorageServices | None = None)
StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(session_id: str, text: str) -> dict[str, object]
StoryRuntime.set_runtime_overrides(session_id: str, overrides: Mapping[str, Any]) -> dict[str, Any]
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(session_id: str, debate_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate_context(session_id: str, debate_id: str) -> dict[str, Any] | None
StoryRuntime.inspect_reference_operation(operation_id: str) -> ReferenceOperationHistory | None
await StoryRuntime.converse(session_id: str, conversation_id: str,
                            agent_id: str, text: str) -> ConversationResult
await StoryRuntime.converse_with_retrieval(session_id, conversation_id, agent_id, text, *, memory_service, progress_sink=None) -> ConversationRetrievalResult
await StoryRuntime.run_prompt(session_id: str, prompt: str, *,
    output_path: str,
    max_debate_rounds: int | None = None, debate_id: str | None = None,
    conversation_context: DebateContextRequest | None = None,
    progress_sink: DebateProgressSink | None = None) -> DebateResult
await StoryRuntime.run_draft(session_id: str, draft: str, *,
    output_path: str,
    max_debate_rounds: int | None = None, debate_id: str | None = None,
    conversation_context: DebateContextRequest | None = None,
    progress_sink: DebateProgressSink | None = None) -> DebateResult
```

`RuntimeSession` binds the session ID. Its `run_prompt()` and `run_draft()` share
the required `output_path`, other options, and result type; it also exposes
both conversation methods, `inspect_debate()`, and `inspect_debate_context()`.
`RuntimeSession` also exposes session-scoped
`inspect_reference_operation(operation_id)`: an operation ID from another
session raises `ValueError`. `Runtime` and `Session` are exact aliases.

Interactive completion and range selection feed the existing methods the same
literal `${path}` and `${path}#Lx-Ly` text a Python caller supplies directly.
The viewer is not exported and no public artifact-selection result was added.

`conversation_storage_services` additively supplies one same-provenance
catalog, event store, search adapter, and mutation coordinator. It is mutually
exclusive with the legacy raw `conversation_event_store`; passing both or a
non-`ConversationStorageServices` value raises `TypeError` before conversation
use. With neither argument, direct Python construction composes the established
filesystem bundle and exposes it as `runtime.conversation_storage_services`.
Passing only the legacy raw event store remains compatible, but does not supply
catalog/search/coordinator provenance.

The installed shell does not ask `StoryRuntime` to read desired or active
storage configuration. Before provider authentication it completes the
separate next-shell activation workflow and injects that already-composed
filesystem or SQLite bundle into the runtime, controller, and scoped memory
services. A running runtime never changes bundles. See the
[selection API](package-api.md#conversation-storage-selection-api) and
[CLI workflow](cli.md#conversation-storage-selection).

Binding resource management is internal and adds no API. For each canonical
agent, the runtime keeps at most one current binding at quiescence. Concurrent
operations with the same fingerprint single-flight binding creation and share
the immutable result. When fingerprints differ, an old and new binding may
overlap temporarily: new work selects the newest desired definition, while
each already-entered debate, advisory, or retrieval operation leases its exact
old binding until success, failure, or cancellation. The idle superseded
binding is then retired. This bounding does not change result objects,
transcripts, provider-thread keys, session manifests, or durable session state.

`output_path` has no default and is the `PART/PATH.md` suffix appended beneath
the canonical `manuscript/` directory. It is validated before session access,
debate reservation, or provider work. Publication becomes
`manuscript/PART/PATH.md` and refuses an existing target. `manuscript` is a
legal `PART`, so `output_path="manuscript/chapter.md"` intentionally publishes
`manuscript/manuscript/chapter.md`. See
[Canonical writing artifacts](writing-artifacts.md).

Consensus agents, not runtime development code, author published text. The
execution specialist authors `r0`; a later synthesized manuscript revision is
lead-authored; and core updates are lead-authored full replacements. The
runtime validates the unanimous result and persists the exact returned bytes.
An advisory conversation call cannot publish. A manuscript revision must use a
new output path because manuscript targets are create-only.

## Runtime diagnostic side effect

`StoryRuntime` construction retains an I/O-free workspace-bound debug writer
but emits no record and creates no diagnostic file or guard. Later instrumented
operations best-effort record fixed configuration, directive, inspection,
advisory, retrieval, consensus, completion, cancellation, and safe failure
facts after they reach their emission boundary; rejected calls are not promised
a record.

Runtime-private submission guards catch diagnostic `BaseException`, including
`KeyboardInterrupt` and `SystemExit`. Constructor and public method signatures,
return values, raised exceptions, cancellation, transcript ordering,
persistence, and artifact behavior remain unchanged when diagnostics succeed or
fail.

For the synchronous provider-free `apply_directive()`,
`set_runtime_overrides()`, `inspect_session()`, `inspect_debate()`, and
`inspect_debate_context()` methods, successful directive and configuration
operations attempt `succeeded`/`completed`; successful inspections attempt
`observed`/`completed`. An ordinary underlying `Exception` attempts
`failed`/`failed`. An underlying `KeyboardInterrupt` or `SystemExit` instead
attempts `cancelled`/`interrupted`, then the method bare-reraises the identical
control with its traceback. A diagnostic failure is swallowed. These direct
Python calls do not translate process control into a return value or status,
retry, append transcript output, or print a CLI hint.

After the advisory or retrieval user event is durable, a provider
`KeyboardInterrupt` or `SystemExit` makes exactly one runtime-component error
submission for `advisory_turn` or `retrieval_turn` with phase `cancelled` and
outcome `interrupted`. The runtime re-raises the identical exception. It appends
no transcript `runtime_error`, performs no automatic retry, and leaves the user
event unmatched and therefore excluded from later context. Failure of this
diagnostic submission cannot replace the exception or alter transcript and
cleanup behavior.

Consequently, `inspect_session()`, `inspect_debate()`, and
`inspect_debate_context()` remain read-only for session/debate state but may
append a bounded operational record. There is no public logger parameter,
configuration switch, telemetry service, or package-root debug-log export. See
[debug-log privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy)
and [storage](configuration/session-storage.md#runtime-debug-log-storage).

## Debate context

```python
DebateContextRequest(*, conversation_id: str, mode: DebateContextMode)
```

The request is frozen, slotted, keyword-only, and has no defaults. Included
mode reads and canonicalizes one bounded context before debate reservation,
persists and reuses it for every phase, and performs no search or retrieval.
Fresh mode records the supplied conversation identity but performs no context
read and sends no context. A context-omitted call does neither and has no sidecar.

`inspect_debate_context()` reads only the strict sidecar and returns `None` when
it is absent; it does not alter the established debate/session inspection JSON.
The exact stored schema lives in [Persistence](../design/persistence.md#debate-context-sidecar).

## Progress types and observer rules

```python
DebateProgressSink = Callable[[DebateProgressEvent], None]
```

`DebateProgressEvent` is frozen, slotted, and keyword-only:

| Field | Type | Default |
| --- | --- | --- |
| `session_id`, `debate_id` | `str` | required |
| `input_kind` | `InputKind` | required |
| `phase` | `DebateProgressPhase` | required |
| `revision_id` | `str | None` | `None` |
| `round_number` | `int` | `0` |
| `context_mode` | `DebateContextMode | None` | `None` |
| `context_event_count`, `context_omitted_turns` | `int` | `0` |
| `conflicting_agents` | `tuple[str, ...]` | `()` |
| `manuscript_path` | `str | None` | `None` |
| `core_updates` | `tuple[str, ...]` | `()` |
| `failure_kind` | `DebateFailureKind | None` | `None` |

Fields are phase-constrained: context metadata belongs only to `started`,
conflicts only to `audit_completed`, artifact data only to
`artifacts_committed`, and failure kind only to `failed`. Exact enum values are
in [Package API](package-api.md#enum-values); the only full ordering is in
[Consensus workflow](../design/consensus-workflow.md).

Coroutine and async-generator sinks are rejected before mutation. A synchronous
sink must return `None`; any exception, non-`None`, or awaitable result raises
zero-argument `ProgressObserverError` with fixed text
`debate progress observer failed`, chained from the failure. Preterminal errors
best-effort mark `interrupted`; postterminal errors never rewrite result, state,
or artifacts. Genuine external cancellation stays `asyncio.CancelledError`; an
interruption-observer failure is its cause.

<a id="one-conversation-turn"></a>

## Results and advisory turns

```python
DebateResult(session_id: str, debate_id: str, input_kind: InputKind,
             status: DebateStatus, revision_id: str | None, rounds_completed: int,
             final_candidate: str | None, latest_candidate: str | None, manuscript_path: str | None,
             core_updates: tuple[str, ...] = (), messages: tuple[DebateMessage, ...] = (),
             error: str | None = None, action_required: str | None = None,
             reference_operation_id: str | None = None)
ConversationResult(session_id: str, conversation_id: str, turn_id: str,
                   agent_id: str, content: str,
                   context: ConversationContextWindow,
                   reference_operation_id: str | None = None)
ConversationRetrievalResult(*, session_id: str, conversation_id: str,
    turn_id: str, agent_id: str, content: str,
    context: ConversationContextWindow, sources: tuple[ConversationRetrievalSource, ...],
    reference_operation_id: str | None = None)
```

Validation, observer errors, cancellation, and uncertain persistence raise.
Expected debate failures normally return a terminal `failed` result.
`DebateResult` adds only the optional reference operation ID; its JSON omits
that key without references. Context-omitted behavior and CLI exits remain
unchanged. The run signature has only the required canonical `output_path`
contract.

With no inline references, result IDs remain `None` and provider call counts
and payloads are unchanged. A referenced operation returns its content-free
`reference_operation_id` only after final revalidation and acceptance; debate
JSON omits the field when it is `None`. `ReferenceOperationFailure`, exported
from `story_writing_agents.writing_artifacts`, carries the ID and a fixed code
when a referenced operation fails.

`converse()` remains one substantive provider call with one user and
reply/error pair; a referenced turn first adds one content-free read prelude.
`converse_with_retrieval()` requires an exact same-session memory service and
optional synchronous sink. Its actions, sources, locking, and durability are in
[Bounded conversation retrieval](conversation-retrieval.md). Debate context
never appends or modifies a transcript.

Inline `${path}` and `${path}#Lx-Ly` indicators are supported by all four
provider-bound entry points above. The runtime preserves the exact source text
in conversation/debate history, uses the parser's escaped-dollar projection for
provider user text, captures the ordered canonical selections once, and gives
each addressed role/phase its own coordinator. See the
[artifact-reference guide](../guides/artifact-references.md) for syntax,
call counts, state meanings, and recovery.
