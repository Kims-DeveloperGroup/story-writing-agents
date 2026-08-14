# Runtime API

> **Availability:** prompt/draft execution, directives, overrides, and
> inspection are implemented. A backend is optional only for non-execution use.

Key terms: **runtime**—the debate coordinator; **bound session**—a convenience
wrapper with one fixed session ID; **debate result**—a typed terminal outcome;
**inspection**—a provider-free read of persisted state.

```python
StoryRuntime(
    workspace: Workspace | str | pathlib.Path,
    backend: AgentBackend | None = None,
)
StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(session_id: str, text: str) -> dict[str, object]
StoryRuntime.set_runtime_overrides(session_id: str,
                                   overrides: Mapping[str, Any]) -> dict[str, Any]
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(session_id: str, debate_id: str) -> dict[str, Any]
await StoryRuntime.run_prompt(session_id: str, prompt: str, *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None,
    debate_id: str | None = None) -> DebateResult
await StoryRuntime.run_draft(session_id: str, draft: str, *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None,
    debate_id: str | None = None) -> DebateResult
```

Prompt and draft must be nonempty strings. Omitted debate IDs are generated;
omitted manuscript names become `<session_id>_<debate_id>.md`. Invalid input,
round limit, unsafe ID, missing backend, or existing reservation is a preflight
exception. Handled provider, protocol, finalization, and artifact failures
normally return `failed`. Unexpected execution exceptions are re-raised after
a best-effort failed transition. External `asyncio.CancelledError` is re-raised
after a best-effort terminal `interrupted` transition.

## Bound-session convenience

```python
RuntimeSession(runtime: StoryRuntime, session_id: str)
RuntimeSession.apply_directive(text: str) -> dict[str, object]
RuntimeSession.set_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]
RuntimeSession.inspect() -> dict[str, Any]
RuntimeSession.inspect_debate(debate_id: str) -> dict[str, Any]
await RuntimeSession.run_prompt(prompt: str, **options: Any) -> DebateResult
await RuntimeSession.run_draft(draft: str, **options: Any) -> DebateResult
```

`Runtime` aliases `StoryRuntime`; `Session` aliases `RuntimeSession`.

## Debate result

```python
DebateResult(
    session_id: str, debate_id: str, input_kind: InputKind,
    status: DebateStatus, revision_id: str | None, rounds_completed: int,
    final_candidate: str | None, latest_candidate: str | None,
    manuscript_path: str | None, core_updates: tuple[str, ...] = (),
    messages: tuple[DebateMessage, ...] = (), error: str | None = None,
    action_required: str | None = None,
)
```

`consensus` means all three specialists approved one revision, finalization
succeeded, and artifacts committed. `deadlock` means conflicts remained at the
replacement limit. `failed` means a handled execution problem prevented
consensus. `result.consensus` is true only for consensus.

`rounds_completed` counts synthesized replacements. Only consensus populates
`final_candidate` and `manuscript_path`; deadlock and most handled failures keep
`latest_candidate`. `core_updates` lists applied filenames. Deadlock uses
`action_required`; failure uses `error`. `to_dict(include_messages=True)` emits
JSON-compatible enum values and includes messages by default.

## Inspection shapes

Session inspection returns `session_id`, normalized `overrides`, and `debates`
sorted by ID. A persisted result contributes its result fields plus
`terminal: true`; a reservation summary contains `session_id`, `debate_id`,
`input_kind`, `status`, `terminal`, `revision_id`, `rounds_completed`, and
`error`.

Debate inspection always returns:

```json
{
  "session_id": "demo",
  "debate_id": "debate_01",
  "status": "not_found",
  "terminal": false,
  "state": null,
  "result": null,
  "messages": []
}
```

Known state omits internal `owner_token`. Result omits duplicated messages;
`messages` holds the validated blackboard. Inspection statuses are
`not_found`, `active`, `consensus`, `deadlock`, `failed`, and `interrupted`.
The last three lifecycle-only values `active`, `interrupted`, and `not_found`
are not `DebateStatus` result values.

See [Consensus workflow](../design/consensus-workflow.md) for sequencing and
[Agent contracts](agent-contracts.md) for phase validation.
