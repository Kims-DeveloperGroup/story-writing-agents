# Runtime API

> **Availability:** `StoryRuntime` runs advisory turns and debates. Debate calls
> optionally capture selected-conversation context and synchronously report typed
> progress; the result shape is unchanged.

Key terms: **legacy run**—a call without `conversation_context`; **included
snapshot**—one transcript window reused for a debate; **progress
sink**—a synchronous observer; **terminal durability**—result and state saved
before terminal progress is delivered.

## Construction and entry points

```python
StoryRuntime(workspace: Workspace | str | pathlib.Path,
             backend: AgentBackend | None = None, *,
             conversation_event_store: ConversationEventStore | None = None)
StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(session_id: str, text: str) -> dict[str, object]
StoryRuntime.set_runtime_overrides(session_id: str, overrides: Mapping[str, Any]) -> dict[str, Any]
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(session_id: str, debate_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate_context(session_id: str, debate_id: str) -> dict[str, Any] | None
await StoryRuntime.converse(session_id: str, conversation_id: str,
                            agent_id: str, text: str) -> ConversationResult
await StoryRuntime.run_prompt(session_id: str, prompt: str, *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None, debate_id: str | None = None,
    conversation_context: DebateContextRequest | None = None,
    progress_sink: DebateProgressSink | None = None) -> DebateResult
await StoryRuntime.run_draft(session_id: str, draft: str, *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None, debate_id: str | None = None,
    conversation_context: DebateContextRequest | None = None,
    progress_sink: DebateProgressSink | None = None) -> DebateResult
```

`RuntimeSession` binds the session ID. Its `run_prompt()` and `run_draft()` use
the same keyword-only options, defaults, and result type; it also exposes
`converse()`, `inspect_debate()`, and `inspect_debate_context(debate_id)`.
`Runtime` and `Session` are exact aliases.

## Debate context

```python
DebateContextRequest(*, conversation_id: str, mode: DebateContextMode)
```

The request is frozen, slotted, keyword-only, and has no defaults. Included
mode reads and canonicalizes one bounded context before debate reservation,
persists and reuses it for every phase, and performs no search or retrieval.
Fresh mode records the selected conversation identity but performs no context
read and sends no context. A legacy call does neither and has no sidecar.

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
             status: DebateStatus, revision_id: str | None,
             rounds_completed: int, final_candidate: str | None,
             latest_candidate: str | None, manuscript_path: str | None,
             core_updates: tuple[str, ...] = (),
             messages: tuple[DebateMessage, ...] = (),
             error: str | None = None, action_required: str | None = None)
ConversationResult(session_id: str, conversation_id: str, turn_id: str,
                   agent_id: str, content: str,
                   context: ConversationContextWindow)
```

Validation, observer errors, cancellation, and uncertain persistence raise.
Expected debate failures normally return a terminal `failed`
result. `DebateResult`, its JSON, legacy calls, and CLI exits are unchanged.

`converse()` still reads pre-turn context, appends one user event, makes one
provider call, and appends one reply or safe runtime error. Debate context does
not append or modify a transcript. See [Agent contracts](agent-contracts.md)
for request fields and [Recovery](../operations/recovery-and-limitations.md) for
operator consequences.
