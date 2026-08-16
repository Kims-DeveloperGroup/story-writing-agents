# Backend and session API

> **Availability:** `AgentBackend`, `BoundAgent`, one-step/retrieval calls, and
> strict session lifecycle are implemented; Codex alone is supported.

Key terms: **binding**—immutable agent/provider pairing; **manifest**—the
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

`model=None` uses Codex's default; invocation ignores user configuration.
Timeout is positive. Overrides map canonical agents to vendors. Reserved
`gemini` is unimplemented; no runtime plugin loader exists.

`bind()` verifies a definition and returns a `BoundAgent` with public `backend`,
`definition`, `vendor`, and `staging_directory`. `invoke()` runs a debate phase.

## Conversation calls

| Concern | Contract |
| --- | --- |
| One-step | Exact `ConversationRequest`; one correlated reply. Its signature and payload are unchanged. |
| Retrieval | Exact request plus synchronous handler. Thread lock covers the loop; backend receives no memory service. |
| Validation | Malformed JSON/schema or invalid/miscorrelated handler outcomes raise `BackendProtocolError`. Runtime maps provider action identity/index violations to `ConversationRetrievalSemanticError`; unsupported vendors raise `ConfigurationError`. |
| Lifecycle | Both paths share lease, timeout, and `(session, backend, agent)` serialization; changed fingerprints or taint start fresh. |
| Cancellation | Terminate the active process, wait at most two seconds, then kill/wait if needed. Incomplete history is tainted, forcing the next compatible call fresh; a completed answer stays compatible if reply storage later fails. |
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

Available sidecars retain hostname/PID; both are `None` only when absent.
Selected input need not exist. Status reads never acquire/reclaim/remove.
POSIX same-host liveness uses `os.kill(pid, 0)`; only a later claim reclaims
proved-dead ownership. Ambiguous/permission/remote/non-POSIX ownership is
`unverified`. No remote probe, heartbeat, expiry, or stale-remote reclaim exists;
PID reuse and hostname equality remain imperfect evidence.

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
