# Session lifecycle design

<a id="canonical-lifecycle-state"></a>

| Durable ↓ / process access → | `claimable` | `held_here` | `blocked_by_live_local_owner` | `ownership_unknown` |
| --- | --- | --- | --- | --- |
| `absent` | Create it. | Invalid | Invalid | Invalid |
| `ready` | Resume it. | Work when configured. | Close the local owner. | Verify; preserve claim. |
| `paused` | Resume to ready. | Valid, but work is paused. | Close the local owner. | Verify; preserve claim. |

**Durable state** says missing, ready, or paused. **Process access** says
claimable, held here, blocked locally, or unknown. The axes are independent;
`absent` means neither manifest nor claim. Conversation and debate states differ.

`SessionLifecycleState` is frozen, slotted, keyword-only, with fields
`session_id`, `durable_state`, `access_state`, `owner_hostname`, `owner_pid`,
`created_at`, `updated_at`. Owner fields pair; persisted timestamps are ordered
and offset-aware. A dead-local claim can be claimable while retaining owner facts.

## What the shell state means to a user

`/status` presents a snapshot, not one combined lifecycle label. Durable state
answers whether provider work is intentionally available; process access
answers whether this process may use it. A ready but claimable session therefore
still cannot run provider work until this shell resumes and holds it. A paused
session must become ready as part of resume. Blocked or unknown ownership cannot
be bypassed from the shell.

Defaults and current scope are also separate. A saved default only tells a
later command or process which session or conversation to try. Current
attachment is the session held now; current focus is the active conversation
used now; agent focus chooses the ordinary advisory responder. Provider
readiness is derived from validated configuration plus `ready` and `held_here`.
It never acquires access or changes another field.

Successful create/resume writes orientation with an `AttachmentKind` result:
`created`, `opened` for an already-ready session, or `reopened` for a paused
session made ready. Those result labels describe what happened; `opened` and
`reopened` are not interactive command words.

## Defaults, process scope, and readiness

The **default session** is a command fallback; **current attachment** is held by
this shell. **Default conversation** persists per session. **Current focus** is
the conversation used now; **agent focus** is its responder. Defaults prove no
current scope.

`provider_work_is_ready()` is pure: exact `True` configuration + `ready` +
`held_here`. Ready plus claimable reports readiness `no`.

## Internal transition mechanism

```mermaid
sequenceDiagram
    participant C as Controller/runtime
    participant B as AgentBackend
    participant G as Claim guard
    participant M as V2 manifest
    C->>B: validate configuration
    C->>B: lifecycle pre-read
    B->>G: lock; inspect claim
    alt absent or proved-dead local claim
        G->>G: create/replace this process claim
    else live local or unknown owner
        G-->>C: typed failure; preserve claim
    end
    B->>M: re-read under access
    opt create, reopen, or pause
        B->>M: create/replace manifest
    end
    C->>B: correlated lifecycle post-read
    alt normal release
        B->>G: compare token; remove own claim
    else failure after new claim
        B->>G: compensate only that new claim
    end
```

Every runtime provider-work preparation requires synchronous validation →
pre-read → acquire → post-read. Results must correlate, preserve identity, and
end `ready` + `held_here`. Failure compensates only newly acquired access.

## Commands, errors, and exact next actions

| Facts or need | Command / stable failure | Exact next action |
| --- | --- | --- |
| Missing | `/session create NAME`; `story session create SESSION` | `Create a session.` |
| Ready + claimable | `/session resume [NAME]`; `story session reopen [SESSION]` | `Resume the session.` |
| Paused + claimable/held here | `SessionPausedError` / `session_paused` | `Resume the session to make it ready.` |
| Live local owner | `SessionAccessBlockedError` / `session_access_blocked` | `Close the owning local process normally, then retry.` |
| Unknown owner | `SessionOwnershipUnknownError` / `session_ownership_unknown` | `Verify ownership; do not remove the access claim.` |
| Invalid provider setup | `ConfigurationError` or `BackendProtocolError` / `provider_preflight_failed` | `Repair provider configuration, then retry.` |
| Held, configured, no focus | `conversation_required` | `Create or choose a conversation.` |
| Held, configured, focused | Continue discussion/run | `Continue with the current focus.` |

`/status` fields are exactly `Session`, `Durable state`, `Process access`,
`Default session`, `Current attachment`, `Default conversation`, `Current
focus`, `Agent focus`, `Provider readiness`, `Next action`. `/session list`
uses `Session`, `Durable state`, `Process access`, `Default session`, `Provider
readiness`, `Next action`. Both are read-only for lifecycle/domain state and
omit owner host, PID, and token; their workspace-aware execution may append a
bounded operational debug record.
`session_not_attached` gates attached commands;
`workspace_version_unsupported` reports rejected V1 data.

Automation create/reopen saves the default, releases, then prints only sorted,
indented `access_state`, `durable_state`, `session_id`. Pause retains the
default. Lifecycle commands have no `--session`.

## Shell transitions and compensation

Bare `story` resumes a ready default and asks for confirmation before resuming a
paused default. `story chat SESSION` creates an absent session or resumes a
persisted session; `/session resume` never creates. Switching validates
configuration and lifecycle, acquires or durably reopens the
destination, performs correlated post-validation, loads destination focus,
persists the destination default, releases the source, then commits in-memory
attachment/focus and resets agent focus to `lead`.

Snapshot/highlight work, cancel, and rejection before acquisition are
mutation-free. The later sequence is not transactional: a successful
paused-to-ready reopen is not rolled back if focus loading, default persistence,
source release, or another later step fails. Later failure releases newly
acquired destination access where possible and does not advance in-memory
attachment/focus, but durable lifecycle/default rollback is not promised. A
reported default-write failure may follow `os.replace`; inspect persisted state
instead of assuming the old value. Source-release failure after default
persistence is fatal `cleanup_failed`, and the new default remains. The
standalone automation command remains `story session reopen`; it is distinct
from interactive grammar.

<a id="conversation-lifecycle-and-compensation"></a>

Conversation creation or resume saves its default before changing current
focus. A reported save failure leaves in-memory focus unchanged but may occur
after replacement of the persisted default, which must then be inspected.
Archiving focused content saves another active default or `null`; save failure
restores archive state only while ready access remains proved.

Confirmed `/session pause` retains defaults, clears attachment and both focuses,
and releases. Quit/EOF releases without pausing. A later process establishes a
new attachment and restores only an active default conversation. See
[Backend API](../reference/backend-and-sessions.md#lifecycle-value-contracts),
[storage](../reference/configuration/session-storage.md), and
[recovery](../operations/recovery-and-limitations.md).
