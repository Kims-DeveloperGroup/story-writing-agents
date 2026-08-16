# Backend and session API

> **Availability:** `AgentBackend`, `BoundAgent`, one-step and retrieval calls,
> and strict session lifecycle are implemented. Codex is the only provider.

Key terms: **binding**—one immutable agent/provider pairing; **manifest**—the
durable session/thread record; **lease**—temporary process ownership;
**thread**—a resumable provider conversation reference.

```python
AgentBackend(
    workspace: Workspace | str | pathlib.Path,
    *,
    executable: str = "codex",
    model: str | None = None,
    timeout_seconds: float = 300,
    backend_overrides: Mapping[str, str] | None = None,
)
await AgentBackend.bind(definition: AgentDefinition) -> BoundAgent
await BoundAgent.converse(request: ConversationRequest) -> ConversationReply
await BoundAgent.converse_with_retrieval(request: ConversationRetrievalRequest,
                                         handler: ConversationRetrievalActionHandler) -> ConversationRetrievalReply
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

`model=None` uses the Codex CLI default; invocation ignores user configuration.
Timeout is positive. Overrides map canonical agents to vendor names. Reserved
`gemini` is unimplemented; there is no runtime-imported plugin loader.

`bind()` verifies a definition and returns a `BoundAgent` with public `backend`,
`definition`, `vendor`, and `staging_directory`. `invoke()` runs a debate phase.

## Conversation calls

| Concern | Contract |
| --- | --- |
| One-step | Exact `ConversationRequest`; one correlated reply. Its signature and payload are unchanged. |
| Retrieval | Exact request plus synchronous handler. One thread lock covers the loop; backend receives no memory service. |
| Validation | Malformed JSON/schema or invalid/miscorrelated handler outcomes raise `BackendProtocolError`. Runtime maps provider action identity/index violations to `ConversationRetrievalSemanticError`; unsupported vendors raise `ConfigurationError`. |
| Lifecycle | Both paths share lease, per-call timeout, cancellation, taint, and `(session, backend, agent)` serialization; changed fingerprints or taint start fresh. |
| Scope and writes | Conversation ID is not a thread key. Private history may span conversations/debates. Only provider metadata may change. |

Runtime owns transcript writes and retrieval authority. See [Runtime API](runtime-api.md#one-conversation-turn), [retrieval](conversation-retrieval.md), and [provider flow](../design/prompts-and-provider.md#conversation-turn-flow).

## Session status

```python
SessionSummary(
    session_id: str,
    active: bool,
    selected: bool,
    lease_state: SessionLeaseState,
    lease_hostname: str | None,
    lease_pid: int | None,
    created_at: str,
    updated_at: str,
)
```

This frozen value separates durable `active`, caller-computed `selected`, and
temporary lease state. Backend never reads or writes CLI context.

| State | Meaning at read time |
| --- | --- |
| `available` | No sidecar, or POSIX same-host PID demonstrably gone; reads do not remove dead sidecars. |
| `owned` | This backend holds the matching token. |
| `in_use` | POSIX same-host unowned PID is demonstrably live. |
| `unverified` | Remote host, ambiguous probe, permission failure, or any unowned non-POSIX sidecar; acquisition is blocked. |

A sidecar's hostname/PID remain visible when `available`; both are `None` only
without a sidecar. Selected input need not name a persisted session. Status
reads never acquire, reclaim, or remove.
POSIX same-host liveness uses non-destructive `os.kill(pid, 0)`; only a later
claim reclaims a demonstrably dead sidecar. Permission/ambiguous probes, remote
hosts, and every unowned non-POSIX sidecar are `unverified`. There is no remote
probe, heartbeat, expiry, or stale-remote reclaim. PID reuse and hostname
equality remain imperfect identity evidence.

## Lifecycle methods

| Method | Exact lifecycle distinction |
| --- | --- |
| `get_session_summary` | Provider-free read; missing raises `SessionNotFoundError`. |
| `list_sessions` | Provider-free, newest update then ID; absent directory returns `()`. |
| `prepare_session` | Preflight, require active, retain lease, return `None`. |
| `create_session` | Preflight and exclusively create active; any manifest means exists. |
| `resume_session` | Preflight, retain lease, reactivate only when inactive. |
| `attach_session` | Preflight and retain existing active without manifest rewrite. |
| `detach_session` | Release this lease; keep active state, threads, and selection. |
| `start_session` | Compatibility create/reactivate without executable preflight; release transient lease. |
| `end_session` | Write inactive, preserve threads, release lease. |
| `close` | Release leases/staging without deactivation; prohibit reuse. |

Lifecycle methods validate IDs/state. Prepare/attach reject inactive state.
Live or unverifiable ownership raises `SessionInUseError` without its token;
changed ownership raises `BackendProtocolError` and is not deleted. Failure
releases a newly acquired lease but retains a previously owned one.

Detach, end, and close retain threads. See [Session storage](configuration/session-storage.md) and [Session lifecycle](../design/session-lifecycle.md).
