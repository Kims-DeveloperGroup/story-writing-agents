# Backend and session API

> **Availability:** `AgentBackend`, `BoundAgent`, the exact lifecycle API, and
> pure lifecycle contracts are implemented for Codex and Antigravity. Codex is
> the default provider.

Key terms: **binding**—agent/provider pairing; **manifest**—durable session and
thread state; **access claim**—temporary exclusive process ownership;
**lifecycle value**—validated provider-neutral facts.

## Backend surface

```python
AgentBackend(workspace: Workspace | str | pathlib.Path, *, executable: str = "codex",
             model: str | None = None, timeout_seconds: float = 300,
             backend_overrides: Mapping[str, str] | None = None,
             antigravity_executable: str = "agy",
             antigravity_model: str | None = None,
             antigravity_effort: str | None = None)
await AgentBackend.bind(definition: AgentDefinition) -> BoundAgent
await BoundAgent.invoke(request: AgentRequest) -> AgentReply
await BoundAgent.converse(request: ConversationRequest) -> ConversationReply
await BoundAgent.converse_with_retrieval(request, handler) -> ConversationRetrievalReply
AgentBackend.close() -> None
```

`model=None` resolves separately for each Codex-bound agent: per-agent
`codex_model`, workspace `codex_model`, then the provider default. A non-null
constructor model has precedence over every persisted Codex model. Codex
reasoning resolves per agent, then from the workspace, then the provider
default; there is no constructor reasoning argument.

For Antigravity, the executable defaults to `agy`. Constructor model and effort
have precedence over per-agent `antigravity_model`/`antigravity_effort`, then
workspace values, then provider defaults. Effort is `None`, `low`, `medium`, or
`high`. Timeout is positive and bounds both adapters. Overrides map canonical
agents to exactly `codex` or `antigravity`; omitted routes use Codex. `gemini`
raises `backend 'gemini' is no longer supported; use 'antigravity'`. A bound
agent exposes `backend`, `definition`, `vendor`, and `staging_directory`.
Provider calls require ready access already held by the backend. Runtime owns
transcripts and retrieval authorization; backend owns provider metadata,
processes, access claims, and per-thread serialization.

Construction first validates the complete seven-field
`config/system_defaults.json` object and closed per-agent objects in
`config/agent_configs.json`, even when non-null constructor model/effort values
would override persisted fields. Optional fields and canonical agents may be
absent; validation still produces a complete roster snapshot. It then snapshots
provider routes and resolved per-agent model/effort values for that backend's
lifetime. Editing either file does not refresh an active backend, existing
binding, provider thread, fingerprint, or request payload; the next backend
construction observes the new valid values.

When present, the optional invocation arguments are inserted in this exact
order immediately after `exec` and before any `resume` marker:

```text
--model MODEL
--config model_reasoning_effort="LEVEL"
```

These are Codex arguments. Either line is omitted when its effective value is
null. The reasoning levels
are exactly `minimal`, `low`, `medium`, `high`, and `xhigh`; model/provider
support for `xhigh` is not inferred by workspace validation. The configured
values change only the invocation arguments and do not become definition,
thread-identity, lifecycle, or request-payload fields. Validation and the
workspace schema are specified in
[Agent configuration](configuration/agent-configuration.md#system-defaults).

Antigravity uses `--model MODEL` and `--effort LEVEL` only when their effective
values are non-null. Provider conversation resume adds `--conversation ID`.
Those process arguments do not change definition fingerprints, lifecycle
identity, or request payload fields.

## Backend diagnostic side effect

Each `AgentBackend` retains an I/O-free workspace-bound debug writer. Backend
emission begins only after the complete configuration snapshot loads
successfully. The initial Codex executable check and snapshot-load failures
precede diagnostic emission; later constructor validation failures are eligible.
Once eligible, it best-effort records fixed configuration, binding, provider
invocation, session lifecycle, cleanup, completion, cancellation, and safe
failure facts.

Backend-private submission guards catch diagnostic `BaseException`, including
`KeyboardInterrupt` and `SystemExit`. This adds no constructor or method
parameter and does not change results, exceptions, cancellation, claim
compensation, provider-thread taint, or cleanup ordering.

`get_session_lifecycle()` and `list_session_lifecycles()` remain provider-free
and read-only for provider-session state, but they may append a bounded
operational record. The logger receives no request or response payload,
configured model/reasoning/effort value, access owner or claim token, or
provider stderr. Concrete adapters continue to drain provider stderr without
exposing it to diagnostics. See
[debug-log privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

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
| `validate_provider_configuration` | No claim or provider turn; resolves executables for effective routes and, for Antigravity, performs a bounded isolated `agy --help` capability probe. Exact `True` or a typed failure. |
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
ephemeral Codex staging. Antigravity definitions, isolated working directories,
and provider-native state are retained. Close never pauses durable state,
deletes manifest thread references, or permits backend reuse. There is no
user-facing session close/delete method.

V1 manifests, access sidecars, and guards raise
`WorkspaceVersionUnsupportedError` without migration, deletion, or rewriting.
Malformed, orphaned, path-mismatched, or changed V2 data fails closed. Exact
paths and schemas are in [Session storage](configuration/session-storage.md);
user-visible transitions are in [Session lifecycle](../design/session-lifecycle.md).
Antigravity-specific setup, process controls, and retention are in
[Use Antigravity agents](../guides/antigravity.md).
