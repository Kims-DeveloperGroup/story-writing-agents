# Session and workspace storage

> **Availability:** local workspace, CLI selection, provider session, debate,
> agent-state, conversation metadata, and conversation transcript persistence
> described here is implemented. All files are plaintext.

Key terms: **scaffold**—files and directories created by initialization;
**manifest**—durable provider-session metadata; **lease**—temporary attachment
metadata; **lifecycle state**—the persisted status of one debate;
**transcript**—the authoritative visible events for one conversation.

## Canonical workspace tree

`story init` creates missing scaffold; entries marked “lazy” appear only when
used. This is the normative layout overview:

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
│   ├── plot_architect_state/debates/<session_id>/<debate_id>.jsonl
│   ├── character_custodian_state/debates/<session_id>/<debate_id>.jsonl
│   └── execution_specialist_state/debates/<session_id>/<debate_id>.jsonl
├── discussion_logs/<session_id>/
│   ├── <debate_id>.state.json
│   ├── <debate_id>.jsonl
│   ├── <debate_id>.result.json
│   └── interactive/conversations/<conversation_id>/
│       ├── metadata.json                                    [lazy identity]
│       └── transcript.jsonl                                 [lazy events]
└── manuscript/<safe-output-path>
```

Initialization also places `.gitkeep` sentinels in initially empty state,
discussion-log, alternative-template, and manuscript directories. Normal init
does not replace existing files; prompt overwrite mode changes only the four
canonical role prompts.

## CLI context

The file is absent until a successful session start, then has exactly:

```json
{"schema_version": 1, "selected_session": "demo"}
```

Both fields are required and no extras are accepted. Version is integer `1`,
not boolean; selection is a safe session ID. `AgentBackend` never reads or
writes this file. Ending the selected session keeps the selection.

## Provider-session manifest

`provider_sessions/<session_id>.json` has exactly:

```json
{
  "active": true,
  "created_at": "2026-01-01T00:00:00+00:00",
  "schema_version": 1,
  "session_id": "demo",
  "threads": {
    "codex:plot_architect": {
      "agent_id": "plot_architect",
      "backend": "codex",
      "fingerprint": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "tainted": false,
      "thread_id": "thread-demo-plot",
      "updated_at": "2026-01-02T00:00:00+00:00"
    }
  },
  "updated_at": "2026-01-02T00:00:00+00:00"
}
```

Version is integer `1`; filename and embedded ID agree; active is boolean;
timestamps are timezone-aware and ordered. `threads` keys are exact
`<backend>:<agent_id>` pairs. Every record has exactly the six shown fields;
only `tainted` is boolean and all others are nonempty strings. Thread ID is at
most 128 ASCII letters, digits, `_`, or `-`, starting alphanumeric. Metadata
survives detach, end, and close.

## Lease sidecar and guard

`<session_id>.lease.json` has exactly:

```json
{
  "acquired_at": "2026-01-02T00:00:00+00:00",
  "hostname": "writer-host",
  "pid": 12345,
  "schema_version": 1,
  "session_id": "demo",
  "token": "opaque-ownership-token"
}
```

Version is integer `1`; path and ID agree; hostname and token are nonempty;
PID is a positive non-boolean integer; acquired time is timezone-aware. The
token is ownership metadata, not a credential. Owned sidecars normally vanish
on detach, end, or close. Reads never remove one.

`.lease-claims.lock` is a persistent workspace-global advisory guard, not JSON
and not either schema. A failed claim may create it. Listing ignores it. Never
delete, replace, or edit it while the workspace may be used.

## Debate and agent records

Session overrides use the schema in [Runtime overrides](runtime-overrides.md).
Each agent JSONL contains only that owner's concise outcomes. The discussion
JSONL is the full public blackboard. Lifecycle state contains an internal
owner token; normal inspection omits it. Terminal results normally have result
JSON, which omits duplicated messages; external cancellation may leave only
state and blackboard. Conversation metadata has its own exact
[catalog schema](../conversation-catalog.md); its sibling transcript has a
separate [event schema](../conversation-transcripts.md).

All reads validate strict UTF-8 and their exact schemas. Runtime-managed files
must not be manually edited. Persistence flow and durability are explained in
[Persistence design](../../design/persistence.md).
