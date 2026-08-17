# Backend and session API

> **Availability:** `AgentBackend`, `BoundAgent`, the exact lifecycle API, and
> pure lifecycle contracts are implemented. Codex is the only supported provider.

Key terms: **binding**—agent/provider pairing; **manifest**—durable session and
thread state; **access claim**—temporary exclusive process ownership;
**lifecycle value**—validated provider-neutral facts.

## Backend surface

```python
AgentBackend(workspace: Workspace | str | pathlib.Path, *, executable: str = "codex",
             model: str | None = None, timeout_seconds: float = 300,
             backend_overrides: Mapping[str, str] | None = None)
await AgentBackend.bind(definition: AgentDefinition) -> BoundAgent
await BoundAgent.invoke(request: AgentRequest) -> AgentReply
await BoundAgent.converse(request: ConversationRequest) -> ConversationReply
await BoundAgent.converse_with_retrieval(request, handler) -> ConversationRetrievalReply
AgentBackend.close() -> None
```

`model=None` uses Codex's default. Timeout is positive. Overrides map canonical
agents to providers; reserved `gemini` fails configuration validation. A bound
agent exposes `backend`, `definition`, `vendor`, and `staging_directory`.
Provider calls require ready access already held by the backend. Runtime owns
transcripts and retrieval authorization; backend owns provider metadata,
processes, access claims, and per-thread serialization.

## Lifecycle value contracts

```python
from story_writing_agents import (
    SessionAccessState, SessionDurableState, SessionLifecycleState,
    provider_work_is_ready,
)

SessionLifecycleState(*, session_id: str, durable_state: SessionDurableState,
    access_state: SessionAccessState, owner_hostname: str | None,
    owner_pid: int | None, created_at: str | None, updated_at: str | None)
provider_work_is_ready(state: SessionLifecycleState, *,
                       provider_configuration_ready: bool) -> bool
```

The value is frozen, slotted, keyword-only, and validates without I/O. Wrong
exact types raise `TypeError`; unsafe IDs, incomplete owner/timestamp pairs,
impossible absent facts, and unordered or offset-free timestamps raise
`ValueError`. Valid combinations are in the
[state matrix](../design/session-lifecycle.md#canonical-lifecycle-state).

Readiness is true only for a valid `ready` state, `held_here` access, and exact
`True` configuration readiness. It never reads configuration or storage,
acquires access, invokes a provider, or mutates state.

## Exact lifecycle methods

```python
AgentBackend.get_session_lifecycle(session_id: str) -> SessionLifecycleState
AgentBackend.list_session_lifecycles() -> tuple[SessionLifecycleState, ...]
AgentBackend.validate_provider_configuration(session_id: str) -> bool
AgentBackend.acquire_session_access(session_id: str) -> SessionLifecycleState
AgentBackend.release_session_access(session_id: str) -> SessionLifecycleState
AgentBackend.create_session_with_access(session_id: str) -> SessionLifecycleState
AgentBackend.reopen_session_with_access(session_id: str) -> SessionLifecycleState
AgentBackend.pause_session_and_release_access(session_id: str) -> SessionLifecycleState
```

These are the complete lifecycle surface:

| Method | Contract |
| --- | --- |
| `get_session_lifecycle` | Provider-free read; missing returns `absent` only when manifest and claim are both absent. |
| `list_session_lifecycles` | Provider-free, newest durable update then ID; validates all encountered lifecycle files. |
| `validate_provider_configuration` | No claim or provider call; exact `True` or a typed failure. |
| `acquire_session_access` | Claim an existing session without changing durable bytes. |
| `release_session_access` | Release only this backend's exact claim; otherwise read-only. |
| `create_session_with_access` | Exclusively create a ready V2 manifest and retain access. |
| `reopen_session_with_access` | Acquire and make paused state ready; ready state is byte-preserving. |
| `pause_session_and_release_access` | Acquire, make paused, and release access. |

Create and reopen deliberately do not perform configuration validation; their
caller does so first. Provider work uses the mandatory synchronous sequence
validation → lifecycle pre-read → acquisition → correlated lifecycle post-read.
Missing is `SessionNotFoundError`, paused provider work is
`SessionPausedError`, live local ownership is `SessionAccessBlockedError`, and
unprovable ownership is `SessionOwnershipUnknownError`.

## Ownership, correlation, and cleanup

Claims are serialized by `.access-claims.lock`. A missing local POSIX process
is `claimable`; guarded acquisition re-reads and replaces that unchanged claim.
A live local process blocks. A remote host, unsupported platform, inaccessible
process, failed probe, or changed claim is `ownership_unknown` and is preserved.
No remote probe occurs.

Release compares the opaque token under the guard and removes only the claim
recorded by this backend. Failed create/access/reopen compensates only a claim
newly acquired by that operation. A caller that already held access keeps it on
later failure. `close()` best-effort releases its recorded claims and removes
prompt staging; it never pauses durable state or permits backend reuse.

V1 manifests, access sidecars, and guards raise
`WorkspaceVersionUnsupportedError` without migration, deletion, or rewriting.
Malformed, orphaned, path-mismatched, or changed V2 data fails closed. Exact
paths and schemas are in [Session storage](configuration/session-storage.md);
user-visible transitions are in [Session lifecycle](../design/session-lifecycle.md).
