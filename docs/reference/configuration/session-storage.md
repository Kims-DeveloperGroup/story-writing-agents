# Session and workspace storage

> **Availability:** V2 defaults, provider sessions, debates, conversations, and
> manuscripts persist as strict UTF-8 plaintext workspace files.

Key terms: **default**—a persisted command or conversation fallback;
**manifest**—durable provider state; **access claim**—temporary process ownership.

## Canonical workspace tree

`story init` creates missing scaffold directories. “Lazy” files appear on use.

```text
workspace/
├── config/{system_defaults.json,agent_configs.json,agents/,persona_templates/}
├── core_documents/{global_outline.md,part_outlines.md,character_sheets.md,narrative_rules.md}
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── sessions/<session_id>.json                          [lazy overrides]
│   │   ├── cli_context.json                                   [lazy default session]
│   │   ├── conversation_defaults/<session_id>.json             [lazy default conversation]
│   │   ├── provider_sessions/.access-claims.lock               [lazy guard]
│   │   ├── provider_sessions/<session_id>.json                 [lazy manifest]
│   │   ├── provider_sessions/<session_id>.access.json          [temporary claim]
│   │   └── debates/<session_id>/<debate_id>.jsonl              [lazy history]
│   └── {plot_architect,character_custodian,execution_specialist}_state/
│       └── debates/<session_id>/<debate_id>.jsonl
├── discussion_logs/<session_id>/
│   ├── <debate_id>.{state.json,jsonl,result.json}
│   ├── <debate_id>.context.json                                [lazy debate context]
│   └── interactive/conversations/<conversation_id>/{metadata.json,transcript.jsonl}
└── manuscript/<safe-output-path>
```

Initialization adds `.gitkeep` to empty directories. Normal init preserves
files; prompt overwrite replaces only the four role prompts.

## Default records

`agent_states/orchestrator_state/cli_context.json` is absent until successful
session create/reopen or an interactive destination saves the default:

```json
{"schema_version": 2, "default_session_id": "demo"}
```

Both fields are required; the version is integer `2` and the default is a safe
session ID. A missing file means no default. `AgentBackend` never reads it.

Each session can have
`agent_states/orchestrator_state/conversation_defaults/<session_id>.json`:

```json
{
  "schema_version": 2,
  "session_id": "demo",
  "default_conversation_id": "c_0123456789abcdef0123456789abcdef"
}
```

All three fields are required. The embedded session matches the path;
`default_conversation_id` is a safe ID or `null`. Missing means no default.
Saving one default atomically replaces only that complete file and does not
change catalog status or process-local current focus.

## Provider-session manifest

`provider_sessions/<session_id>.json` has exactly:

```json
{
  "schema_version": 2,
  "session_id": "demo",
  "durable_state": "ready",
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-01-02T00:00:00+00:00",
  "provider_threads": {}
}
```

`durable_state` is `ready` or `paused`; `absent` has no manifest. Timestamps
include UTC offsets and are ordered. Thread keys are `<backend>:<agent_id>`.
Each thread value has exactly `backend`, `agent_id`, `fingerprint`, `thread_id`,
`tainted`, and `updated_at`; its key must match its backend and agent. Thread
IDs use the documented safe pattern and `tainted` is boolean. Opening ready
state and releasing access do not rewrite the manifest.

## Access claim and guard

`provider_sessions/<session_id>.access.json` has exactly:

```json
{
  "schema_version": 2, "session_id": "demo",
  "owner_hostname": "writer-host", "owner_pid": 12345,
  "claim_token": "opaque-ownership-token",
  "claimed_at": "2026-01-02T00:00:00+00:00"
}
```

The path and embedded ID match; hostname/token are nonempty, PID is a positive
exact integer, and time has an offset. The token coordinates release and is not
authentication. It is never shown by status/list. Owned claims normally vanish
on switch, pause, quit, EOF, automation completion, or backend close.

`.access-claims.lock` is a persistent workspace-global advisory guard, not JSON
or part of either schema. A failed attempt may create it. Never edit, replace,
or remove it while the workspace may be used.

## Unsupported V1 state

V1 manifests, `<session_id>.lease.json`, `.lease-claims.lock`, V1
`cli_context.json` with `selected_session`, and files under
`conversation_selections/` are detected and rejected with
`WorkspaceVersionUnsupportedError`. They are not migrated, rewritten, or
deleted. Preserve them and follow the
[nondestructive recovery procedure](../../operations/recovery-and-limitations.md#unsupported-v1-session-data).

Readers reject malformed data, invalid keys/types/IDs, and unsupported versions.
Orphan claims fail closed. See [Persistence](../../design/persistence.md).
