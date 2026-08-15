# Session and workspace storage

> **Availability:** local workspace, selection, session, debate, agent-state,
> conversation, and transcript persistence is implemented. All files are plaintext.

Key terms: **selection**—the saved default session ID; **manifest**—durable provider-session
metadata; **lease**—temporary attachment metadata; **transcript**—visible events.

## Canonical workspace tree

`story init` creates missing scaffold; “lazy” entries appear only when used.

```text
workspace/
├── config/
│   ├── system_defaults.json
│   ├── agent_configs.json
│   ├── agents/<canonical-agent-id>/AGENTS.md
│   └── persona_templates/{default_kim_eun_hee.md,alternative_templates/}
├── core_documents/
│   ├── global_outline.md
│   ├── part_outlines.md
│   ├── character_sheets.md
│   └── narrative_rules.md
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── sessions/<session_id>.json                         [lazy overrides]
│   │   ├── cli_context.json                                  [lazy selection]
│   │   ├── provider_sessions/.lease-claims.lock              [lazy guard]
│   │   ├── provider_sessions/<session_id>.json               [lazy manifest]
│   │   ├── provider_sessions/<session_id>.lease.json         [temporary]
│   │   └── debates/<session_id>/<debate_id>.jsonl            [lazy history]
│   └── {plot_architect,character_custodian,execution_specialist}_state/
│       └── debates/<session_id>/<debate_id>.jsonl
├── discussion_logs/<session_id>/
│   ├── <debate_id>.{state.json,jsonl,result.json}
│   └── interactive/conversations/<conversation_id>/
│       ├── metadata.json                                    [lazy identity]
│       └── transcript.jsonl                                 [lazy events]
└── manuscript/<safe-output-path>
```

Initialization places `.gitkeep` sentinels in initially empty state,
discussion-log, alternative-template, and manuscript directories. Normal init
preserves existing files; prompt overwrite replaces only the four role prompts.

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

The interactive controller saves a destination only after acquiring and
validating its lease. During a switch it saves the destination before detaching
the source. Failed acquisition or selection writing preserves the prior
selection. If source detach then fails, the destination selection remains: the
controller cannot safely roll back to a source whose lease is uncertain.

Reattaching an already selected session does not rewrite the file. Ending keeps
selection, including when it now points to an inactive session. Attachment
itself remains in process and in the temporary lease, never in CLI context.

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

Version is integer `1`; filename and embedded ID agree; active is boolean;
timestamps are timezone-aware and ordered. Thread keys are exact
`<backend>:<agent_id>` pairs. Each record has exactly the six shown fields; only
`tainted` is boolean and the rest are nonempty strings. Thread ID is at most 128
ASCII letters, digits, `_`, or `-`, starting alphanumeric. Metadata survives
detach, end, and close.

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
owner-local; discussion JSONL is the public blackboard. Lifecycle state keeps
an internal owner token omitted by normal inspection. Completed debates normally
add result JSON without duplicated messages; external cancellation may leave
only state and blackboard. Conversation metadata has its [catalog schema](../conversation-catalog.md),
and transcripts have a separate [event schema](../conversation-transcripts.md).
All reads require strict UTF-8 and exact schemas. Do not manually edit these
files. Durability is in [Persistence design](../../design/persistence.md).
