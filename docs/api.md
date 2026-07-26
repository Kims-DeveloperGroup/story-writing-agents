# API Reference

## Public Import Surface

The following names are exported from `story_writing_agents`:

- roster metadata: `AGENT_SPECS`, `ROSTER`, `SPECIALIST_IDS`,
  `LEAD_ORCHESTRATOR`, `PLOT_ARCHITECT`, `CHARACTER_CUSTODIAN`,
  `EXECUTION_SPECIALIST`;
- backend API: `AgentBackend`, `load_backend`;
- request and result models: `AgentRequest`, `AgentReply`,
  `AgentStateEntry`, `DebateMessage`, `DebateResult`;
- enums: `InputKind`, `Phase`, `Verdict`, `DebateStatus`, `DirectiveKind`;
- runtime API: `StoryRuntime`, `RuntimeSession`, `Runtime`, `Session`;
- directive API: `RuntimeDirective`, `parse_directive`, `apply_directive`;
- workspace API: `Workspace`, `initialize_workspace`;
- errors: `StoryWritingAgentsError`, `WorkspaceSecurityError`,
  `WorkspaceFormatError`, `ConfigurationError`, `DirectiveError`,
  `SessionIdError`, `ManuscriptExistsError`, `BackendProtocolError`, and
  `DebateExistsError`.

`Runtime` is an alias of `StoryRuntime`; `Session` is an alias of
`RuntimeSession`.

Enum values are:

| Enum | Values |
| --- | --- |
| `InputKind` | `prompt`, `draft` |
| `Phase` | `draft`, `audit`, `propose_revision`, `synthesize`, `finalize` |
| `Verdict` | `approve`, `conflict` |
| `DebateStatus` | `consensus`, `deadlock`, `failed` |
| `DirectiveKind` | `language`, `persona`, `reset` |

## Backend Protocol and Plugin Loading

```python
class AgentBackend(Protocol):
    async def invoke(self, request: AgentRequest) -> AgentReply: ...


def load_backend(spec: str) -> AgentBackend: ...
```

`invoke` must be an asynchronous method and must return an `AgentReply`, not a
provider-native object or raw string. The runtime validates the reply's general
field types and then its phase-specific contract.

`load_backend` accepts only `module:attribute`, where both components are
Python identifiers and the module may contain dotted package components.
Nested attribute traversal, expressions, and shell syntax are rejected. The
attribute may be:

- an already-created backend instance;
- a class that can be instantiated with no arguments; or
- a zero-argument factory returning a backend.

The final value must expose an async `invoke` method. Importing the selected
module and constructing its value executes Python code with the CLI process's
permissions. The syntax restriction is not a sandbox; provider plugins are
trusted code.

This deterministic adapter is sufficient for a protocol smoke test:

```python
from story_writing_agents import AgentReply, AgentRequest, Phase


class ApprovingBackend:
    async def invoke(self, request: AgentRequest) -> AgentReply:
        if request.phase is Phase.DRAFT:
            return AgentReply.candidate(
                request.revision_id,
                request.user_payload,
            )
        if request.phase is Phase.AUDIT:
            return AgentReply.approval(request.revision_id)
        if request.phase is Phase.FINALIZE:
            return AgentReply.finalization(request.revision_id)
        raise RuntimeError(f"unexpected phase: {request.phase.value}")


backend = ApprovingBackend()
```

If saved as an importable `example_backend.py`, its CLI spec is
`example_backend:backend`. A real adapter is responsible for translating every
possible phase to and from its provider.

## Request Model

```python
AgentRequest(
    session_id: str,
    debate_id: str,
    agent_id: str,
    phase: Phase,
    round_number: int,
    revision_id: str,
    system_prompt: str,
    task: str,
    user_payload: str,
    candidate_payload: str | None = None,
    messages: tuple[DebateMessage, ...] = (),
    agent_state: tuple[AgentStateEntry, ...] = (),
)
```

The frozen request fields mean:

| Field | Meaning |
| --- | --- |
| `session_id`, `debate_id` | Durable workspace identifiers for the call. |
| `agent_id` | One canonical roster ID. |
| `phase` | `draft`, `audit`, `propose_revision`, `synthesize`, or `finalize`. |
| `round_number` | Current audit round or synthesis-cycle number. |
| `revision_id` | Revision the reply must target. |
| `system_prompt` | Persona, domain rules, language, and least-context documents in the documented order. |
| `task` | Phase-specific instruction, including the no-hidden-reasoning requirement. |
| `user_payload` | Original user prompt or draft, kept separate from the system prompt. |
| `candidate_payload` | Current candidate when the phase operates on one. |
| `messages` | Full visible blackboard history as of the call's phase snapshot. |
| `agent_state` | Session-wide persisted outcomes belonging only to `agent_id`, including earlier debates and already-recorded current-debate outcomes. |

Parallel peers receive the same pre-phase `messages` snapshot. Their completed
outputs are logged afterward in canonical roster order.

`DebateMessage` and `AgentStateEntry` have explicit persisted schemas:

```python
DebateMessage(
    sender: str,
    phase: str,
    round: int,
    revision: str,
    kind: str,
    content: str,
    timestamp: str,
)

AgentStateEntry(
    agent_id: str,
    session_id: str,
    debate_id: str,
    phase: str,
    round: int,
    revision: str,
    kind: str,
    content: str,
    timestamp: str,
)
```

Both models are frozen and provide `to_dict()`. Persisted agent-state content is
limited to 4096 characters per entry.

## Reply Model and Phase Contracts

```python
AgentReply(
    revision_id: str,
    content: str = "",
    verdict: Verdict | None = None,
    core_updates: Mapping[str, str] = {},
)
```

Factory signatures are:

```python
AgentReply.candidate(revision_id: str, content: str) -> AgentReply
AgentReply.approval(revision_id: str, content: str = "") -> AgentReply
AgentReply.conflict(revision_id: str, critique: str) -> AgentReply
AgentReply.finalization(
    revision_id: str,
    core_updates: Mapping[str, str] | None = None,
) -> AgentReply
```

Every reply must use a string `revision_id` equal to the revision expected by
the request. `content` must be a string, `verdict` must be a `Verdict` member or
`None`, and `core_updates` must map string filenames to string full-text
content. Phase validation is:

| Phase | Required reply |
| --- | --- |
| `draft` | Non-empty candidate `content`; no verdict or core updates. |
| `audit` | `Verdict.APPROVE` with optional content, or `Verdict.CONFLICT` with a non-empty critique; no core updates. |
| `propose_revision` | Non-empty proposal content; no verdict or core updates. `candidate()` is the applicable factory. |
| `synthesize` | Non-empty complete replacement candidate; no verdict or core updates. |
| `finalize` | No verdict. Content must be empty or exactly equal to the already-approved candidate. Core updates may be empty and are later restricted to the four allowlisted filenames. |

A string such as `"approve"` is not accepted in place of
`Verdict.APPROVE`. Specialists cannot return core updates, and the lead
orchestrator cannot change candidate content after unanimous approval.

## Debate Result

```python
DebateResult(
    session_id: str,
    debate_id: str,
    input_kind: InputKind,
    status: DebateStatus,
    revision_id: str | None,
    rounds_completed: int,
    final_candidate: str | None,
    latest_candidate: str | None,
    manuscript_path: str | None,
    core_updates: tuple[str, ...] = (),
    messages: tuple[DebateMessage, ...] = (),
    error: str | None = None,
    action_required: str | None = None,
)
```

`DebateStatus` has three returned-result values:

- `consensus`: all three specialists approved one revision, finalization
  succeeded, and artifacts were committed;
- `deadlock`: conflicts remained after the allowed replacement revisions;
- `failed`: a handled backend, protocol, finalization, or artifact error
  prevented consensus.

`result.consensus` is true only for `consensus`.
`result.to_dict(include_messages=True)` returns JSON-compatible enum values and
includes the blackboard by default; pass `include_messages=False` to omit it.

`rounds_completed` counts synthesized replacements. `final_candidate` and
`manuscript_path` are populated only after consensus. `latest_candidate`
retains the most recent candidate for deadlock and most handled failures.
`core_updates` lists filenames actually applied. `error` describes failure;
`action_required` describes the user ruling needed for deadlock.

`active` and `interrupted` are lifecycle inspection statuses rather than
`DebateResult` statuses. An externally cancelled coroutine re-raises
`asyncio.CancelledError` after best-effort transition to terminal
`interrupted`; it does not return a `DebateResult`.

## Runtime and Session

```python
StoryRuntime(
    workspace: Workspace | str | pathlib.Path,
    backend: AgentBackend | None = None,
)

StoryRuntime.session(session_id: str) -> RuntimeSession
StoryRuntime.apply_directive(
    session_id: str,
    text: str,
) -> dict[str, object]
StoryRuntime.set_runtime_overrides(
    session_id: str,
    overrides: Mapping[str, Any],
) -> dict[str, Any]
StoryRuntime.inspect_session(session_id: str) -> dict[str, Any]
StoryRuntime.inspect_debate(
    session_id: str,
    debate_id: str,
) -> dict[str, Any]

await StoryRuntime.run_prompt(
    session_id: str,
    prompt: str,
    *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None,
    debate_id: str | None = None,
) -> DebateResult

await StoryRuntime.run_draft(
    session_id: str,
    draft: str,
    *,
    manuscript_name: str | None = None,
    max_debate_rounds: int | None = None,
    debate_id: str | None = None,
) -> DebateResult
```

A missing backend is valid for directives and inspection, but debate execution
raises `BackendProtocolError`. Prompt and draft payloads must be non-empty
strings. If `debate_id` is omitted, the runtime generates one. If
`manuscript_name` is omitted, it uses
`<session_id>_<debate_id>.md` beneath `manuscript/`.

Preflight errors such as invalid input, invalid round limits, unsafe IDs, a
missing backend, or an already-reserved debate raise an exception directly.
Handled execution failures normally return a `failed` result. Unexpected
exceptions that escape execution are re-raised after a best-effort failed
lifecycle transition.

The bound-session convenience API is:

```python
RuntimeSession(runtime: StoryRuntime, session_id: str)

RuntimeSession.apply_directive(text: str) -> dict[str, object]
RuntimeSession.set_overrides(
    overrides: Mapping[str, Any],
) -> dict[str, Any]
RuntimeSession.inspect() -> dict[str, Any]
RuntimeSession.inspect_debate(debate_id: str) -> dict[str, Any]
await RuntimeSession.run_prompt(prompt: str, **options: Any) -> DebateResult
await RuntimeSession.run_draft(draft: str, **options: Any) -> DebateResult
```

Example:

```python
import asyncio

from story_writing_agents import StoryRuntime
from example_backend import backend


async def main() -> None:
    session = StoryRuntime(".", backend).session("demo")
    result = await session.run_draft(
        "A neutral draft.",
        debate_id="debate_01",
        manuscript_name="demo.md",
    )
    print(result.to_dict(include_messages=False))


asyncio.run(main())
```

## Workspace and Directive Utilities

```python
Workspace(root: str | os.PathLike[str])
initialize_workspace(root: str | os.PathLike[str]) -> Workspace

RuntimeDirective(
    kind: DirectiveKind,
    language: str | None = None,
    agent_id: str | None = None,
    persona: str | None = None,
)
parse_directive(text: str) -> RuntimeDirective | None
apply_directive(
    workspace: Workspace,
    session_id: str,
    text: str,
) -> dict[str, object]
```

`Workspace.initialize()` creates missing scaffold content and returns the same
workspace. Common direct methods include:

```python
Workspace.path(relative) -> pathlib.Path
Workspace.read_text(relative) -> str
Workspace.read_json(relative) -> Any
Workspace.write_text(relative, content, *, overwrite=True) -> pathlib.Path
Workspace.write_json(relative, value, *, overwrite=True) -> pathlib.Path
Workspace.read_persona(template_name: str) -> str
Workspace.read_core_documents(
    filenames: tuple[str, ...],
) -> dict[str, str]
Workspace.read_manuscript(manuscript_name: str) -> str
Workspace.write_manuscript(
    manuscript_name: str,
    content: str,
) -> pathlib.Path
Workspace.load_session_overrides(session_id: str) -> dict[str, Any]
Workspace.save_session_overrides(
    session_id: str,
    overrides: Mapping[str, Any],
) -> pathlib.Path
Workspace.read_agent_state(
    agent_id: str,
    session_id: str,
    debate_id: str,
) -> tuple[AgentStateEntry, ...]
Workspace.read_debate_messages(
    session_id: str,
    debate_id: str,
) -> tuple[DebateMessage, ...]
Workspace.inspect_session(session_id: str) -> dict[str, Any]
Workspace.inspect_debate(
    session_id: str,
    debate_id: str,
) -> dict[str, Any]
Workspace.commit_approved_artifacts(
    manuscript_name: str,
    candidate: str,
    core_updates: Mapping[str, str],
) -> tuple[str, tuple[str, ...]]
```

Lower-level append, reservation, transition, and result-save methods are
publicly callable on `Workspace`, but are orchestration persistence primitives;
normal integrations should use `StoryRuntime`.

## Inspection Shapes

Session inspection returns:

```json
{
  "session_id": "demo",
  "overrides": {
    "language": null,
    "agents": {}
  },
  "debates": []
}
```

`debates` is sorted by debate ID. A debate with a persisted result is represented
by the result fields plus `"terminal": true`. A reservation without a result
uses a summary containing `session_id`, `debate_id`, `input_kind`, `status`,
`terminal`, `revision_id`, `rounds_completed`, and `error`.

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

For a known debate, `state` contains lifecycle schema fields but omits the
internal `owner_token`; `result` contains the persisted result fields without
messages; and `messages` contains the validated full JSONL blackboard. Possible
inspection statuses include `not_found`, `active`, `consensus`, `deadlock`,
`failed`, and `interrupted`.

## CLI

Installed command syntax:

```text
story-writing-agents init [workspace]

story-writing-agents directive
  [--workspace WORKSPACE]
  --session SESSION
  TEXT

story-writing-agents run
  [--workspace WORKSPACE]
  --session SESSION
  --backend MODULE:ATTRIBUTE
  (--prompt PROMPT | --draft DRAFT)
  [--output MANUSCRIPT_PATH]
  [--debate-id DEBATE_ID]
  [--max-debate-rounds MAX_DEBATE_ROUNDS]

story-writing-agents inspect
  [--workspace WORKSPACE]
  --session SESSION
  [--debate DEBATE]
```

The default workspace is `.`. `run` prints the complete
`DebateResult.to_dict()` JSON, including messages. Other successful commands
print their JSON result. Errors are written to standard error as
`ExceptionType: message`.

Exit codes are:

| Code | Meaning |
| --- | --- |
| `0` | Initialization, directive, or inspection succeeded; or a debate reached consensus. |
| `2` | CLI usage failed or a caught exception occurred, including preflight and plugin-loading errors. |
| `3` | The debate returned `deadlock`. |
| `4` | The debate returned `failed`. |

See [Configuration](configuration.md) for persisted schemas and
[Security, Privacy, and Limitations](security.md) before exposing a workspace
to sensitive content or loading a third-party backend.
