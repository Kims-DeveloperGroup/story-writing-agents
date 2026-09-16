# Agent-context progress

The typed agent-context lifecycle is used by Python and interactive Lead turns.
The public observer remains content-free. Supported runtime/session Lead
methods also expose a separate, additive keyword-only `action_progress_sink`
seam with package-internal-typed exact SEARCH and READ progress. The interactive
terminal is its intended consumer.

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

These are the meanings of the public observer. They are distinct from the
detailed interactive progress described below.

## Detailed interactive progress

On a capable raw terminal, a Lead turn displays a transient multiline frame.
Its shared first row is an animated bar followed by a monotonic `HH:MM:SS`
duration and `Working`. Each Lead or specialist operation then has an agent
card. A specialist card retains its exact request. Before an action, the card
says `Preparing context`; once an action is known, it shows the indexed
`SEARCH` or `READ` and whether it is running or completed.

A SEARCH start displays every exact literal query as `Searching for: "..."`
and the exact requested resource/source-kind values (`runtime_input`,
`agent_definition`, `writing_artifact`, `conversation`, `active_debate`, or
`own_agent_state`). Its `Scope` includes those kinds and an optional safe
workspace-relative artifact prefix after `under`. Completion replaces the
start details with each safe relative result locator, its exact line or record
range when bounded, and its UTF-8 byte count. It says `Found: no matches` when
empty and `Results: truncated` when the bounded result set was truncated.

A READ start displays every selected safe relative locator and its exact whole,
line, or record boundary. Completion retains those locators and ranges and adds
the exact UTF-8 byte count for each retrieved selection. The frame never
includes source snippets or excerpts, absolute paths,
credentials, or hidden reasoning. During provider generation, active terminals may
display transient status notices and a bounded rolling preview of streamed output
that is erased prior to persistent output.

The raw driver redraws the whole multiline frame in ANSI-capable and plain
`NO_COLOR` modes and clears it before persistent output. Ordinary line-input,
non-TTY, `TERM=dumb`, non-POSIX, and uncertain-capability paths await the same
operation silently. Native authentication temporarily owns the terminal during
recovery, but the same monotonic timer resumes afterward rather than restarting.

Intermediate SEARCH/READ cards are transient. After confirmed specialist work,
persistent specialist panels follow proposal order. A successful panel includes
the exact request, `succeeded` status, provider-call count, assessment, and
specialist answer. Failed or cancelled panels retain the exact request and
terminal status and state that no specialist answer was recorded. The Lead
panel follows all specialist panels, followed by one persistent
`Elapsed: HH:MM:SS` notice. Direct Lead turns likewise end with the Lead panel
followed by elapsed time.

For Lead session continuity, `bootstrap` means exact retained-context recovery;
`delta` means compatible continuation. Selected-source counts and bytes describe
the bounded source projection and exclude the separate required `h` session
context; actual provider-payload accounting includes it. `checkpoint_advanced`
follows durable publication of the Lead judgment or synthesis, so failed
publication cannot earn coverage for a later delta. No new status surface is
introduced. See [Lead session memory](agent-context.md#lead-session-memory).

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

`StoryRuntime` and `RuntimeSession` expose this observer on the supported Lead
methods documented in the runtime API. It does not authorize specialist
execution or artifact mutation.

The detailed trace types are package-internal implementation details and are
not exported from the package root or `story_writing_agents.agent_context`
facade. The synchronous `action_progress_sink` keyword is present on supported
`StoryRuntime` and `RuntimeSession` Lead methods, but is unsupported as a
general observer contract. Python callers can supply and retain its exact
events; those callers own access control, retention, and cleanup. The
interactive controller is the intended consumer.

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

Detailed progress is transient presentation, not secure erasure. Terminal
capture, a multiplexer, a remote terminal, accessibility tooling, or emulator
scrollback may retain exact search queries and safe relative locators/ranges.
Treat such captured output as story metadata even though source bodies and
provider answers are excluded.

See [Agent-context design](../design/agent-context.md) and
[privacy](../operations/privacy-and-provider.md#agent-context-progress-privacy).
