# Agent-context progress

The typed agent-context lifecycle is used by Python lead turns. The public shell
has no provider-bound story operation, so it emits no progress for ordinary
input.

## Lifecycle meaning

One logical agent invocation follows:

```text
PREPARE -> PLAN -> INVOKE/ACTION -> VERIFY -> COMMIT | FAIL
```

| Stage | Component event | Meaning |
| --- | --- | --- |
| Prepare | No separate public event | Validate scope, authority, callback, and mandatory references. |
| Plan | `bootstrap` or `delta`; optional `omission` | Select bounded sources and leave other authorized sources searchable. |
| Invoke/action | Paired indexed search/read events | A provider may request bounded, runtime-authorized observations. |
| Verify | `answer_authorized` | The answer and final sources passed validation. |
| Commit | `checkpoint_advanced` | A READY context checkpoint can support a later compatible delta. |
| Fail | Fixed failure facts plus `checkpoint_not_advanced` | No checkpoint was accepted for this invocation. |

Events are content-free identities, fixed states, booleans, and bounded
counters. They do not contain prompts, queries, excerpts, reference paths,
provider responses, private agent state, or credentials.

## Python application presentation

A Python application may render compact lines from the callback:

```text
[context] bootstrap: 2 selected / 1,024 bytes; 1 searchable
[context] search action 1 started
[context] search action 1 completed: 1 hits; truncated: no
[context] answer accepted
[context] checkpoint advanced
```

These examples are not current shell output. An ordinary shell line receives
direct-execution-unavailable guidance before any context plan is created.

## Direct types

The observation types remain in the direct module:

```python
from story_writing_agents.agent_context.service import (
    ContextExecutionEvent,
    ContextExecutionEventKind,
    ContextExecutionEventSink,
)
```

They are not exported from the package root or
`story_writing_agents.agent_context` facade. A low-level contextual executor or
`lead_turn(context_event_sink=...)` may accept a synchronous sink that returns
exactly `None`. Coroutine,
async-generator, asynchronously callable, exception-raising, awaitable-returning,
or non-`None` observers fail under the component's validation rules.

`StoryRuntime` and `RuntimeSession` expose this observer only on `lead_turn()`.
It does not authorize specialist execution or artifact mutation.

<a id="privacy-references-and-limits"></a>

## Checkpoint and failure rules

- Normal progress begins with one plan event.
- Search/read starts and completions share monotonically increasing indexes.
- Authorization precedes a successful checkpoint.
- Exactly one checkpoint outcome terminates an operation; nothing follows it.
- Failure facts are content-free and never turn into an artifact result.
- Exact aggregate provider input tokens appear only when every submitted
  physical call reports them; the component never estimates missing usage.

Mandatory file references remain separate ordered read requirements. A
checkpoint or optional search cannot satisfy, reorder, or summarize them.
Optional conversation RAG is also a separate discovery layer.

See [Agent-context design](../design/agent-context.md) and
[privacy](../operations/privacy-and-provider.md#agent-context-progress-privacy).
