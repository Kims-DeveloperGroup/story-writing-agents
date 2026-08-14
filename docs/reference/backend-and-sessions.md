# Backend and session API

> **Availability:** `AgentBackend` and `BoundAgent` are concrete implemented
> classes. Codex is the only implemented provider. Strict lifecycle methods are
> Python-only foundations; `BoundAgent.converse()` is also Python-only. The CLI
> exposes only `session start|end`.

Key terms: **binding**—one immutable agent/provider pairing; **manifest**—the
durable session and thread record; **lease sidecar**—temporary process ownership
metadata; **thread**—a resumable provider conversation reference;
**conversation turn**—one request and its correlated reply.

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

`model=None` uses the Codex CLI default while the invocation ignores user
configuration. Timeout must be positive. Overrides map canonical agent IDs to
configured vendor names for this backend instance. `gemini` is recognized but
rejected as unimplemented; there is no runtime-imported plugin loader.

`bind()` verifies the definition and returns a `BoundAgent` with public
`backend`, `definition`, `vendor`, and `staging_directory` attributes.
`invoke()` runs a debate phase; `converse()` runs one visible conversation turn.

## Conversation calls

| Concern | Contract |
| --- | --- |
| Request | Requires an exact, revalidated `ConversationRequest` addressed to the bound agent before provider work. |
| Reply | Requires exactly four nonempty UTF-8 fields and matching session, conversation, and turn IDs; invalid JSON, fields, or correlation raise `BackendProtocolError`. |
| Provider | Codex is implemented; unsupported configured vendors raise `ConfigurationError`. |
| Lifecycle | Shares `invoke()` lease, timeout, cancellation, taint, and `(session_id, backend, agent_id)` lock rules; changed fingerprints and tainted threads start fresh. |
| Thread scope | `conversation_id` is not a thread key: visible context stays in one conversation while provider-private history can span conversations and debate calls. See the [flow](../design/prompts-and-provider.md#conversation-turn-flow). |
| Persistence | May update provider-thread metadata, not conversation or story content; the caller owns transcript persistence. See [Privacy](../operations/privacy-and-provider.md#conversation-call-boundary). |

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

This value is frozen and slotted. `active` is durable permission for provider
work. `selected` only compares the caller-supplied name; the backend never
reads or writes CLI context. Lease state is temporary attachment:

| State | Meaning at read time |
| --- | --- |
| `available` | No sidecar, or POSIX same-host PID demonstrably gone; reads do not remove dead sidecars. |
| `owned` | This backend holds the matching token. |
| `in_use` | POSIX same-host unowned PID is demonstrably live. |
| `unverified` | Remote host, ambiguous probe, permission failure, or any unowned non-POSIX sidecar; acquisition is blocked. |

When a sidecar exists, its validated hostname and PID remain visible even if
available. Both are `None` only without a sidecar.
The optional selected-session value is validated but need not name a persisted
provider session.

This page is the normative lease-classification and reclamation contract.
Status reads never acquire, reclaim, or remove a lease. On POSIX, same-host
liveness uses the non-destructive `os.kill(pid, 0)` probe: a demonstrably gone
owner is `available`, but only a later claim may reclaim its sidecar; a live
owner is `in_use`. A permission failure or other ambiguous probe is
`unverified`. A remote-host sidecar is also `unverified`. On non-POSIX systems,
every unowned same-host sidecar is `unverified` and cannot be reclaimed.

There is no remote or native non-POSIX owner probe, heartbeat, timeout,
age-based expiry, or stale-remote reclamation. PID probing cannot distinguish
PID reuse, and hostname equality is not a distributed identity guarantee.

## Lifecycle methods

| Method | Exact lifecycle distinction |
| --- | --- |
| `get_session_summary` | Nonmutating, provider-free read; missing raises `SessionNotFoundError`. |
| `list_sessions` | Nonmutating tuple, newest update first then ID; absent directory returns `()`. |
| `prepare_session` | Preflights routes/executable, requires active, retains lease, returns `None`. |
| `create_session` | Preflights, exclusively creates active, retains lease; any manifest raises `SessionExistsError`. |
| `resume_session` | Preflights, retains existing lease, reactivates only if inactive; missing raises not found. |
| `attach_session` | Preflights and retains an existing active session without rewriting manifest. |
| `detach_session` | Releases this backend's lease without ending, deleting threads, or changing selection. |
| `start_session` | Compatibility create/reactivate without executable preflight; preserves threads and releases transient lease. |
| `end_session` | Writes inactive using owned/transient lease, preserves threads, then releases. |
| `close` | Releases owned leases and prompt staging without deactivation; closed backends cannot be reused. |

`prepare_session` and `attach_session` reject inactive state with
`BackendProtocolError`; lifecycle methods validate IDs and persisted state.
An acquisition blocked by a live or unverifiable owner raises
`SessionInUseError` without its token. Changed ownership raises
`BackendProtocolError` and is not deleted. A call releases a lease newly
acquired before its failure; a previously owned lease stays attached after an
owned read/write failure.

Detach, end, and close retain resumable thread metadata. Exact manifest and
sidecar schemas are in
[Session storage](configuration/session-storage.md); ordering and platform
limits are in [Session lifecycle design](../design/session-lifecycle.md).
