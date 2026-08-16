# Session and workspace storage

> **Availability:** selections, provider sessions, debates, conversation records,
> and manuscripts persist as plaintext workspace files.

Key terms: **session selection**—the default story session; **conversation selection**—one
session's current subject; **manifest**—durable provider metadata; **lease**—temporary attachment metadata.

## Canonical workspace tree

`story init` creates missing scaffold; “lazy” entries appear only when used.

```text
workspace/
├── config/{system_defaults.json,agent_configs.json,agents/,persona_templates/}
├── core_documents/{global_outline.md,part_outlines.md,character_sheets.md,narrative_rules.md}
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── sessions/<session_id>.json                         [lazy overrides]
│   │   ├── cli_context.json                          [lazy session selection]
│   │   ├── conversation_selections/<session_id>.json [lazy conversation choice]
│   │   ├── provider_sessions/.lease-claims.lock              [lazy guard]
│   │   ├── provider_sessions/<session_id>.json               [lazy manifest]
│   │   ├── provider_sessions/<session_id>.lease.json         [temporary]
│   │   └── debates/<session_id>/<debate_id>.jsonl            [lazy history]
│   └── {plot_architect,character_custodian,execution_specialist}_state/
│       └── debates/<session_id>/<debate_id>.jsonl
├── discussion_logs/<session_id>/
│   ├── <debate_id>.{state.json,jsonl,result.json}
│   ├── <debate_id>.context.json                    [lazy selected context]
│   └── interactive/conversations/<conversation_id>/
│       ├── metadata.json                                    [lazy identity]
│       └── transcript.jsonl                                 [lazy events]
└── manuscript/<safe-output-path>
```

Initialization adds `.gitkeep` to initially empty directories. Normal init
preserves files; prompt overwrite replaces only the four role prompts.

## CLI context and selection timing

`agent_states/orchestrator_state/cli_context.json` is absent until a successful
legacy session start or interactive destination first saves a selection. Its
unchanged exact schema is:

```json
{"schema_version": 1, "selected_session": "demo"}
```

Both fields are required; extras are rejected. Version is integer `1`, not
boolean, and selection is a safe session ID. `AgentBackend` never reads or
writes this file.

Each story session has an independent lazy file at
`agent_states/orchestrator_state/conversation_selections/<session_id>.json`:

```json
{
  "schema_version": 1,
  "session_id": "demo",
  "selected_conversation_id": "c_0123456789abcdef0123456789abcdef"
}
```

The three fields are exact. Version is integer `1`; both IDs are safe and the
embedded session must match the filename. `selected_conversation_id` may be
`null` to record an explicit clear. A missing file also means no selection and
is read without creating storage. Malformed JSON, duplicate/missing/extra
fields, unsupported versions, mismatched IDs, and non-UTF-8 bytes fail closed.

Conversation files are independent of `cli_context.json` and of one another.
Each save atomically replaces one complete file; it does not change conversation
metadata. Transition ordering is canonical in [Session lifecycle](../../design/session-lifecycle.md).

## Provider-session manifest

`provider_sessions/<session_id>.json` has exactly:

```json
{
  "active": true, "created_at": "2026-01-01T00:00:00+00:00", "schema_version": 1,
  "session_id": "demo", "threads": {"codex:plot_architect": {
    "agent_id": "plot_architect", "backend": "codex",
    "fingerprint": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "tainted": false, "thread_id": "thread-demo-plot", "updated_at": "2026-01-02T00:00:00+00:00"}},
  "updated_at": "2026-01-02T00:00:00+00:00"
}
```

Version is integer `1`; filename and ID agree; active is boolean; timestamps are
ordered and timezone-aware. Thread keys are `<backend>:<agent_id>`. Each record
has the six shown fields; `tainted` is boolean and other values are nonempty.
Thread ID is at most 128 ASCII alphanumerics, `_`, or `-`, beginning
alphanumeric. Metadata survives detach, end, and close.

## Lease sidecar and guard

`<session_id>.lease.json` has exactly:

```json
{"acquired_at":"2026-01-02T00:00:00+00:00","hostname":"writer-host",
 "pid":12345,"schema_version":1,"session_id":"demo","token":"opaque-ownership-token"}
```

Version is integer `1`; path and ID agree; hostname and token are nonempty; PID
is a positive non-boolean integer; acquired time is timezone-aware. The token
is ownership metadata, not a credential. Owned sidecars normally vanish on
detach, end, or close. Reads never remove one.

`.lease-claims.lock` is a persistent workspace-global advisory guard, not JSON
or either schema; a failed claim may create it. Listing ignores it. Never edit
or remove it while the workspace may be used.

## Other persisted records

Session overrides use [Runtime overrides](runtime-overrides.md). Agent JSONL is
owner-local; discussion JSONL is the public blackboard. Conversation metadata
has its [catalog schema](../conversation-catalog.md), and transcripts have a
separate [event schema](../conversation-transcripts.md). Reads require strict
UTF-8 and exact schemas. Do not edit them. See [Persistence](../../design/persistence.md).
