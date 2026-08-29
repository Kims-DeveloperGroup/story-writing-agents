# Backend and session API

> **Availability:** `AgentBackend`, `BoundAgent`, the exact lifecycle API, and
> pure lifecycle contracts are implemented for Codex and Antigravity. The
> workspace default precedes the final Codex fallback. Backend physical
> structured calls are automatically offered to bounded agent-exchange capture.

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
await BoundAgent.invoke(request: AgentRequest, *,
                        reference_read: ReferenceReadInvocation | None = None) -> AgentReply
await BoundAgent.converse(request: ConversationRequest, *,
                          reference_read: ReferenceReadInvocation | None = None) -> ConversationReply
await BoundAgent.converse_with_retrieval(request, handler, *,
    reference_read: ReferenceReadInvocation | None = None) -> ConversationRetrievalReply
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
agents to exactly `codex` or `antigravity`; omitted routes use workspace
`default_backend`, then Codex. Legacy `gemini` raises
`backend 'gemini' is no longer supported; use 'antigravity'`. A bound
agent exposes `backend`, `definition`, `vendor`, and `staging_directory`.
Provider calls require ready access already held by the backend. Runtime owns
transcripts and retrieval authorization; backend owns provider metadata,
processes, access claims, per-thread serialization, and exact binding-staging
retirement.

The backend is the sole authority for each active agent's provider, model,
effort, and their sources: `launch_override`, `agent_config`, `system_default`,
`built_in_default`, or `provider_default`. Its profile also drives adapter argv,
interactive metadata, confirmation, and debug events. A null model or effort
means the adapter omits that flag; it does not identify or attest a vendor's
server-side default. Provider reply prose and self-identification are never
execution evidence.

Runtime-built provider-managed definitions use the agent-context service and
compact wire v5. Package-internal `story_session_context` remains capture and
direct-request compatibility input; it is not the current built-in provider
envelope. A content-free explicit-reference prelude remains separate, and
context continuations carry only the new authorized observation. See
[Agent-context design](../design/agent-context.md#compact-provider-wire-v5).

<a id="referenced-invocation-boundary"></a>

## Referenced invocation boundary

`reference_read` is an additive internal/Python seam. Its synchronous
`ReferenceReadInvocation` coordinator comes from
`WritingArtifactRepository.begin_reference_invocation()` and remains private;
the provider-neutral request and action values are documented in
[Agent contracts](agent-contracts.md#explicit-reference-read-contracts).
Omitting the argument preserves the exact established payload, schema, provider
call count, and repository-I/O-free path.

Before provider work, every referenced call validates the coordinator's exact
content-free request type and synchronous methods. It also validates the bound
canonical role and these request correlations:

| Bound operation | Required correlation |
| --- | --- |
| `invoke()` | `agent_id` equals the request and bound definition; `phase` equals the debate phase value; `round_number` equals the request round. |
| `converse()` | `agent_id` equals the request and bound definition; `phase="conversation"`; `round_number=None`. |
| `converse_with_retrieval()` | `agent_id` equals the request and bound definition; `phase="conversation_retrieval"`; `round_number=None`. Retrieval action indices remain a separate `1..5` sequence. |

The backend then holds the same provider-thread lock across a content-free
prelude and the substantive work. The first provider result must be one
`read_writing_artifacts` action that exactly echoes the full correlation and
ordered path/range batch. A request-specific schema plus decoder and equality
check rejects result-first, partial, reordered, duplicate, broadened, foreign,
or malformed actions before selected content is returned. The private
capability never enters the provider request.

After the exact action, selected strict-UTF-8 text appears once under
`untrusted_writing_artifact_context` in the immediately following substantive
payload. The reply returns only after structural decoding, durable
`result_received`, final source revalidation, and durable `accepted`. All four
canonical roles follow this backend path; their prompt prose is not the
enforcement mechanism. The full state and trust mapping is in
[Canonical writing artifacts](writing-artifacts.md#provider-enforced-invocation-sequence).

| One logical invocation | Without references | With references |
| --- | ---: | ---: |
| Direct conversation | 1 provider call | 2: prelude + reply |
| One agent in one debate phase | 1 provider call | 2: prelude + phase reply |
| Retrieval turn | 1–5 provider calls | 2–6: prelude + unchanged 1–5 retrieval loop |

The backend performs no automatic retry. Cancellation, provider or workflow
failure, drift, and invalid action/correlation terminate the same one-use
coordinator with a content-free outcome. After provider state has advanced,
the compatible thread is tainted where required so a later call cannot assume
clean continuation. An explicit retry or replacement needs a new top-level
operation with a fresh capture, correlation, coordinator, capability, and
ordinal sequence. `StoryRuntime` owns those values and allocates each
invocation with attempt `1`; another agent, phase, replacement, or operation
cannot reuse prior evidence.

Construction first validates the complete twelve-field
`config/system_defaults.json` object and closed per-agent objects in
`config/agent_configs.json`, even when non-null constructor model/effort values
would override persisted fields. Optional fields and canonical agents may be
absent; nullable routes and `default_backend` still produce a complete roster
snapshot through the final Codex fallback. The independent conversation-storage
and conversation-RAG defaults are validated with the same closed object but do
not alter backend routing. It then captures provider routes and resolved
per-agent model/effort values. Direct edits have no watcher: the next
backend construction observes and reconciles valid persisted changes, while
existing other shells retain their captured state. The installed interactive
`/settings` path is different: it stages and preflights a complete candidate,
persists it, and swaps the current shell's backend/runtime generation without a
restart. The one exception is a candidate that changes only Message details:
that presentation-only fast path prepares, verifies, and commits configuration
without authentication, storage readiness, backend/runtime, route-generation,
or provider/thread work, then updates the active flag.
An exact Conversation-RAG-only candidate instead prepares, verifies, and
commits only the startup-fixed configuration without authentication, storage
readiness, backend/runtime work, route-generation, provider/thread work, or an
optional-package probe. It leaves the current RAG service and automatic policy
unchanged until the next shell. Mixed changes activate only their live-capable
settings; desired storage and RAG remain next-shell choices.
Launch overrides remain higher precedence and therefore mask the corresponding
editor fields.

When present, the optional invocation arguments are inserted in this exact
order immediately after `exec` and before any `resume` marker:

```text
--model MODEL
--config model_reasoning_effort="LEVEL"
```

These are Codex arguments. Either line is omitted when its effective value is
null. The reasoning levels
are exactly `minimal`, `low`, `medium`, `high`, and `xhigh`; model/provider
support for `xhigh` is not inferred by workspace validation. Model and effort
values change only invocation arguments and do not become definition,
thread-identity, lifecycle, or request-payload fields. A provider switch
separately advances a durable route generation; nonzero generations participate
in the definition fingerprint so every A→B→A switch starts fresh. Generation
zero preserves the legacy fingerprint exactly. Validation and the
workspace schema are specified in
[Agent configuration](configuration/agent-configuration.md#system-defaults).

Antigravity uses `--model MODEL` and `--effort LEVEL` only when their effective
values are non-null. Provider conversation resume adds `--conversation ID`.
Those model/effort process arguments do not change definition fingerprints,
lifecycle identity, or request payload fields.

## Backend diagnostic side effect

Each `AgentBackend` retains an I/O-free workspace-bound debug writer. Backend
emission begins only after the complete configuration snapshot loads
successfully. The initial Codex executable check and snapshot-load failures
precede diagnostic emission; later constructor validation failures are eligible.
Once eligible, it best-effort records fixed configuration, binding, provider
invocation, session lifecycle, cleanup, completion, cancellation, safe failure,
and execution-profile facts.

Backend-private submission guards catch diagnostic `BaseException`, including
`KeyboardInterrupt` and `SystemExit`. This adds no constructor or method
parameter and does not change results, exceptions, cancellation, claim
compensation, provider-thread taint, or cleanup ordering.

Ordinary records retain schema version 1. Execution identity uses content-free
schema-v2 operations `execution_setting_change`,
`execution_profile_applied`, `execution_profile_confirmed`, and
`execution_profile_recovery_required`. A successful startup emits one active
snapshot per canonical agent with correlation `startup` and never fabricates an
old profile. A hot change correlates accepted old/new, active, and first-
success confirmation records; uncertain compensation emits the error/failed
recovery record. No-op, cancelled, and pre-persist failures emit no successful
transition.

These schema-v2 records intentionally contain exact provider/model/effort
values and sources, route generation, activation state, and optional thread or
context state. They still contain no request or response payload, story prose,
provider stderr, credential, access owner, or claim token. Concrete adapters
continue to drain provider stderr without exposing it to diagnostics.
When a completed Codex or Antigravity response instead reaches structured
decoding and is rejected by a protocol, JSON, or Unicode error, the backend
best-effort submits that exact response text to the separate internal
`debug_logs/provider_error_responses/` store. It does not render the body or
place it in runtime JSONL. Capture failure cannot replace the original decoding
exception or change thread taint, restart, cleanup, cancellation, output, exit,
or retry behavior. Direct `AgentBackend` callers retain the same exceptions;
there is no configuration switch or new public API. See the inspection and
privacy boundary in
[Privacy](../operations/privacy-and-provider.md#rejected-provider-response-artifacts).
`get_session_lifecycle()` and `list_session_lifecycles()` remain provider-free
and read-only for provider-session state, though they may append a bounded
operational record. See
[debug-log privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy).

<a id="agent-exchange-capture-side-effect"></a>

## Agent-exchange capture side effect

Each backend owns an I/O-free `WorkspaceAgentExchangeCapture`. The established
structured provider path and the provider-managed contextual path both begin a
best-effort lifecycle immediately before every physical `adapter.invoke()`.
There is no constructor option, retry switch, or new root public API. Direct
storage inspection remains available through the `agent_exchanges` package.
The installed bare `/exchanges` command is a later controller/terminal
integration over immutable snapshots; the backend still imports no controller,
terminal, picker, viewer, or optional external package.

One successful capture lifecycle uses a fresh
`exchange_<32 lowercase hexadecimal characters>` identity and preserves the
exact supplied session, canonical agent, provider, and one conversation or
debate kind/ID across its records. A writing-artifact reference prelude is a
separate physical call with its own exchange ID, but it inherits the same outer
conversation/debate correlation as the substantive call.

The nested canonical content shapes are:

```json
{"call_kind":"conversation","payload":{},"prior_thread_id":null,"schema":{}}
{"actual_transport_utf8_bytes":null,"response_text":"...","thread_id":"thread_1","usage":null}
{"code":"accepted","outcome":"accepted"}
```

The request shape has exactly `call_kind`, `payload`, `prior_thread_id`, and
`schema`. The response shape has exactly `actual_transport_utf8_bytes`,
`response_text`, `thread_id`, and `usage`; non-null usage has exactly
`input_tokens`, `output_tokens`, and `cached_input_tokens`. The outcome shape
has exactly `code` and `outcome`. These JSON values are stored in the outer
schema-v1 record's string `content`, subject to the 262,144-byte complete-code-
point prefix. Full UTF-8 byte and Python Unicode-code-point counts plus SHA-256
describe the complete capture input, so a truncated record is not the complete
nested value.

The terminal vocabulary is closed:

| Outcome | Fixed code |
| --- | --- |
| `accepted` | `accepted` |
| `provider_failure` | `provider_invocation_failed`, `provider_timed_out`, `provider_contract_invalid` |
| `cancelled` | `cancelled` |
| `rejected` | `malformed_structured_response`, `response_correlation_mismatch`, `response_semantic_rejected`, `action_exhausted`, `answer_validation_failed` |
| `post_response_runtime_failure` | `thread_persistence_failed`, `reference_runtime_failed`, `context_runtime_failed` |

The request append is attempted before invocation. A valid raw
`ProviderResult` is appended immediately on return, before accounting,
decode/domain validation, the separate rejected-response artifact, and thread
persistence. Exactly one terminal append is then attempted when the relevant
backend/context owner knows the outcome. Capture state advances before append;
all capture failures are suppressed and never retried. Missing files, records,
outcomes, or sequence values therefore do not prove that no physical provider
call or result existed.

The user-facing correlation, header, preview, search, and privacy behavior is
documented in [Inspect agent exchanges](../guides/agent-exchanges.md). The
viewer cannot retry or replay this backend lifecycle.

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
later failure.

Each `invoke()`, `converse()`, and `converse_with_retrieval()` call privately
retains its exact binding resource from method entry through success, failure,
or cancellation. Retiring a binding immediately rejects later calls through
that handle, but its staging directory remains available until the final active
call exits. Retirement then releases that exact staging ownership once;
provisional staging is also discarded when binding validation does not
complete.

`close()` is terminal and idempotent. Its first call marks every binding retired
and rejects backend reuse. If calls are active, per-binding release and cleanup
of backend-wide access claims and thread locks wait until the last call exits;
otherwise cleanup proceeds immediately. Per-binding release and claim
cleanup remain best-effort filesystem operations. A Codex release removes one
exact retired staging directory; an Antigravity release retains definitions,
isolated working directories, and provider-native state. Closing never pauses
durable state, deletes provider-thread references, adds a cleanup status or
error contract, or permits backend reuse. There is no user-facing session
close/delete method.

V1 manifests, access sidecars, and guards raise
`WorkspaceVersionUnsupportedError` without migration, deletion, or rewriting.
Malformed, orphaned, path-mismatched, or changed V2 data fails closed. Exact
paths and schemas are in [Session storage](configuration/session-storage.md);
user-visible transitions are in [Session lifecycle](../design/session-lifecycle.md).
Antigravity-specific setup, process controls, and retention are in
[Use Antigravity agents](../guides/antigravity.md).
