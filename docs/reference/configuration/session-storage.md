# Session and workspace storage

> **Availability:** V2 defaults, provider sessions, debates, conversations,
> internal bounded runtime debug logs, and manuscripts persist as plaintext
> workspace files.

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
├── debug_logs/
│   ├── .runtime.lock                                           [lazy guard]
│   ├── runtime.jsonl                                           [lazy active log]
│   └── runtime.jsonl.{1..5}                                    [lazy backups]
├── discussion_logs/<session_id>/
│   ├── <debate_id>.{state.json,jsonl,result.json}
│   ├── <debate_id>.context.json                                [lazy debate context]
│   └── interactive/conversations/<conversation_id>/{metadata.json,transcript.jsonl}
└── manuscript/<safe-output-path>
```

Initialization adds `.gitkeep` to empty directories. Normal init preserves
files; prompt overwrite replaces only the four role prompts.

<a id="runtime-debug-log-storage"></a>

## Runtime debug-log storage

New initialization scaffolds `debug_logs/`; an existing initialized workspace
without that directory remains valid. The diagnostics writer creates the
directory on a storage attempt only after opening and reverifying a bound
workspace root and confirming the required safe descriptor-relative and
no-follow primitives. An existing root binds when `Workspace` is constructed; a
specifically missing root's first valid initialization securely creates missing
canonical components descriptor-relatively, retains the verified root
descriptor throughout scaffold/default installation, and rewalks the canonical
root after those writes. It records the final identity only after the retained
descriptor closes successfully. Any other unavailable identity, a replaced
root, or missing safe primitive drops the record, and the directory may remain
absent.
There is no fallback destination. Importing or constructing the internal writer
creates nothing. The active `runtime.jsonl` and persistent `.runtime.lock` guard
remain lazy: the writer creates them only during storage attempts, and backups
appear only after rotation. A guard or active file does not by itself prove that
any complete record was appended.

Each JSONL file is limited to 1,048,576 bytes. Rotation keeps the active file
plus five backups, with `.1` newest and `.5` oldest, for at most 6 MiB of JSONL
data. Before each unlink or rename, the writer requires the involved names to
remain absent or identify the meaningful inode captured for that rotation, then
updates its expectation after a successful shift. A mismatch stops later
rotation, although earlier shifts may remain. Removing `.5` is an ordinary
filesystem unlink, not secure erasure. The guard and `.gitkeep` are outside the
data bound. Debug records are plaintext; their schema is documented in
[Persistence](../../design/persistence.md#workspace-runtime-debug-log),
their containment and failure behavior in [Filesystem safety](../../operations/filesystem-safety.md#debug-log-filesystem-safety),
and their content boundary in [Privacy](../../operations/privacy-and-provider.md#workspace-debug-log-privacy).

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
