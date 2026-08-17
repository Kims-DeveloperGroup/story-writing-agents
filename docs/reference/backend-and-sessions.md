# Backend and session API

> **Availability:** `AgentBackend`, `BoundAgent`, one-step/retrieval calls,
> strict legacy session operations, and pure lifecycle contracts are
> implemented; Codex alone is supported.

Key terms: **binding**—agent/provider pairing; **manifest**—durable session/thread
record; **lease**—temporary ownership; **lifecycle value**—pure validated facts.

## Backend surface

```python
AgentBackend(workspace: Workspace | str | pathlib.Path, *, executable: str = "codex",
             model: str | None = None, timeout_seconds: float = 300, backend_overrides: Mapping[str, str] | None = None)
await AgentBackend.bind(definition: AgentDefinition) -> BoundAgent
await BoundAgent.converse(request: ConversationRequest) -> ConversationReply
await BoundAgent.converse_with_retrieval(request: ConversationRetrievalRequest, handler: ConversationRetrievalActionHandler) -> ConversationRetrievalReply
AgentBackend.prepare_session(session_id: str) -> None
AgentBackend.get_session_summary(session_id: str, *, selected_session: str | None = None) -> SessionSummary
AgentBackend.list_sessions(*, selected_session: str | None = None) -> tuple[SessionSummary, ...]
AgentBackend.create_session(session_id: str) -> SessionSummary
AgentBackend.resume_session(session_id: str) -> SessionSummary
AgentBackend.attach_session(session_id: str) -> SessionSummary
AgentBackend.detach_session(session_id: str) -> SessionSummary
AgentBackend.start_session(session_id: str) -> dict[str, Any]
AgentBackend.end_session(session_id: str) -> dict[str, Any]
AgentBackend.close() -> None
```

`model=None` uses Codex's default; invocation ignores user configuration.
Timeout is positive. Overrides map canonical agents to vendors; reserved
`gemini` is unimplemented. `bind()` verifies a definition and returns a
`BoundAgent` with public `backend`, `definition`, `vendor`, and
`staging_directory`.

One-step accepts an exact `ConversationRequest`; retrieval adds an exact request
and synchronous handler. Both share lease, timeout, and
`(session, backend, agent)` serialization. Runtime owns transcripts/retrieval;
backend owns provider metadata. Exact payload, validation, and cancellation
contracts live in [retrieval](conversation-retrieval.md) and [provider flow](../design/prompts-and-provider.md#conversation-turn-flow).

## Lifecycle value contracts

The package facade supports these additive imports:

```python
from story_writing_agents import (
    SessionAccessState, SessionDurableState, SessionLifecycleState,
    provider_work_is_ready,
)
```

The owning module also supports two projections absent from the package facade:

```python
from story_writing_agents.session_lifecycle import (
    lifecycle_from_session_summary, session_summary_from_lifecycle,
)

SessionLifecycleState(*, session_id: str, durable_state: SessionDurableState,
    access_state: SessionAccessState, access_hostname: str | None,
    access_pid: int | None, created_at: str | None,
    updated_at: str | None)
lifecycle_from_session_summary(summary: SessionSummary) -> SessionLifecycleState
session_summary_from_lifecycle(state: SessionLifecycleState, *, selected: bool) -> SessionSummary
provider_work_is_ready(state: SessionLifecycleState, *, provider_configuration_ready: bool) -> bool
```

`SessionLifecycleState` is frozen, slotted, keyword-only, and validated without
I/O. Its exact valid combinations are in the
[canonical state table](../design/session-lifecycle.md#canonical-lifecycle-state).
Legacy `active=True/False` maps to `ready/paused`; lease values
`available/owned/in_use/unverified` map respectively to
`claimable/held_here/blocked_by_live_local_owner/ownership_unknown`.
Both projections are deterministic and perform no I/O, acquisition, or mutation.

Forward projection validates but omits `summary.selected`; selection is the
saved CLI default, never current attachment or conversation focus. Reverse
projection requires the caller's exact `selected` bool. `absent` has no legacy
summary projection. All three functions raise `TypeError` for wrong exact
contracts/scalars and `ValueError` for invalid IDs, pairs, states, or timestamps.

Readiness is true only for a valid `ready` state, `held_here` access, and exact
`True` configuration readiness. The query never reads configuration/storage,
claims access, starts a provider, or mutates state.

## Legacy status and operations

`SessionLeaseState` and `SessionSummary` now live in `session_lifecycle`, but
imports from `story_writing_agents` and `story_writing_agents.models` remain the
same exact objects. `SessionSummary` remains frozen/slotted with eight positional
fields and annotations: `session_id: str`, `active: bool`, `selected: bool`,
`lease_state: SessionLeaseState`, `lease_hostname: str | None`,
`lease_pid: int | None`, `created_at: str`, `updated_at: str`.

Legacy meanings remain `available` (absent or proved-dead local lease), `owned`
(this backend), `in_use` (live local owner), and `unverified` (remote or
ambiguous owner). Reads never acquire, reclaim, remove, or remotely probe.
Dead sidecars may retain owner metadata; absent sidecars report none.

| Method | Unchanged behavior |
| --- | --- |
| `get_session_summary` | Provider-free read; missing raises `SessionNotFoundError`. |
| `list_sessions` | Provider-free, newest update then ID; absent directory returns `()`. |
| `prepare_session` | Preflight, require active, retain lease, return `None`. |
| `create_session` | Preflight and exclusively create active; any manifest means exists. |
| `resume_session` | Preflight, retain lease, reactivate only when inactive. |
| `attach_session` | Preflight and retain existing active without manifest rewrite. |
| `detach_session` | Release this lease; retain active state, threads, and selections. |
| `start_session` | Compatibility create/reactivate without executable preflight; release transient lease. |
| `end_session` | Write inactive, preserve threads, release lease. |
| `close` | Release leases/staging without deactivation; prohibit reuse. |

Prepare/attach reject inactive state. Live/unverifiable ownership raises
`SessionInUseError` without its token; changed ownership raises
`BackendProtocolError` and is not deleted. Failure releases a new lease but
retains a prior one. Backends do not return `absent`; missing manifests retain
`SessionNotFoundError`. No transition, schema, provider, controller, terminal
copy, or shell output changes. See [Session storage](configuration/session-storage.md).
