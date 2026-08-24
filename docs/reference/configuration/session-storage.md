# Session and workspace storage

> **Availability:** V2 defaults, provider sessions, debates, conversations,
> current writing-artifact reference sources, provenance-backed publication records,
> part-scoped manuscripts, explicit-reference evidence, provider-native state,
> and bounded runtime debug logs persist as plaintext workspace files.
> Runtime-managed documents and records are strict UTF-8.

Key terms: **default**—a persisted command or conversation fallback;
**manifest**—durable provider state; **access claim**—temporary process ownership;
**provider-native state**—vendor-managed conversation and execution data.

## Canonical workspace tree

`story init` creates missing scaffold directories. “Lazy” files appear on use.

```text
workspace/
├── config/{system_defaults.json,agent_configs.json,agents/,persona_templates/}
├── core_documents/{global_outline.md,part_outlines.md,character_sheets.md,narrative_rules.md} [lazy agent publications]
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── sessions/<session_id>.json                          [lazy overrides]
│   │   ├── cli_context.json                                   [lazy default session]
│   │   ├── conversation_defaults/<session_id>.json             [lazy default conversation]
│   │   ├── provider_sessions/.access-claims.lock               [lazy guard]
│   │   ├── provider_sessions/.antigravity/                      [lazy retained provider state]
│   │   ├── provider_sessions/<session_id>.json                 [lazy manifest]
│   │   ├── provider_sessions/<session_id>.access.json          [temporary claim]
│   │   ├── debates/<session_id>/<debate_id>.jsonl              [lazy history]
│   │   └── writing_artifacts/
│   │       ├── catalog.json                                    [lazy current provenance]
│   │       ├── commits/<commit_id>.json                        [lazy immutable history]
│   │       ├── transactions/<commit_id>.json                   [authoritative pending journal]
│   │       ├── staging/.artifacts-*.tmp                        [retained staged bytes]
│   │       ├── staging/.artifacts-*.held                       [retained prior bytes]
│   │       ├── staging/.artifacts-*.retired                    [retained failed publication]
│   │       ├── staging/.artifacts-*.completed                  [retained completed journal]
│   │       ├── input_snapshots/<operation_id>/manifest.json    [reference request; schema v2]
│   │       ├── input_snapshots/<operation_id>/operation_events/<sequence>.json
│   │       ├── input_snapshots/<operation_id>/invocations/<invocation_id>/manifest.json
│   │       ├── input_snapshots/<operation_id>/invocations/<invocation_id>/events/<sequence>.json
│   │       ├── .artifacts.lock                                 [permanent readiness triple]
│   │       ├── .artifacts.lock.anchor                          [same lock inode]
│   │       ├── .artifacts.lock.ready                           [same lock inode; ready marker]
│   │       └── recovery_blocked.json                           [exceptional fail-closed marker]
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
└── manuscript/<part-id>/<safe-relative-markdown-path>           [lazy create-only output]
```

Initialization adds `.gitkeep` to selected empty state and manuscript
directories. Normal init preserves files; prompt overwrite replaces only the
four role prompts. Initialization
does not create any of the four core documents or a manuscript. Artifact state
appears only when the artifact repository is accessed or a unanimous run
publishes output; `recovery_blocked.json` appears only after recovery cannot be
trusted. Staging residue is non-authoritative, never catalog-listed, retained
after successful and failed publication, and may grow. Only exact JSON entries
under `transactions/` are recovery journals. The three permanent lock names are
hard links to one regular inode with link count three; partial or old two-link
state is invalid and is not migrated or reconstructed. See
[Canonical writing artifacts](../writing-artifacts.md).

`input_snapshots/` is lazy, internal authorization evidence for nonempty
explicit-reference operations. Its operation and invocation manifests and
events are create-only, canonical strict-UTF-8 schema-v1 JSON. Event filenames
are zero-padded contiguous sequence numbers; invocation ordinals are also
contiguous. Records contain schema and operation/invocation identity,
path/range indicators, provenance, hashes, counts, correlation, states,
checkpoints, and fixed outcomes. A private invocation manifest also binds its
capability identifier, which validated caller history does not expose.
Selected bytes, source/provider text, prompts, and results are never stored
there.

The directory is excluded from the canonical artifact catalog, completion, and
picker namespaces. Do not hand-edit it. A missing operation directory means no
history; a present partial or invalid tree is corrupt and fails closed for that
operation. See
[Recovery](../../operations/recovery-and-limitations.md#explicit-reference-evidence-recovery)
and [Canonical writing artifacts](../writing-artifacts.md#authorized-reads-and-lifecycle-evidence).

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

Codex and Antigravity threads may coexist in one manifest and remain distinct
because the backend is part of the key. A compatible thread requires the same
backend, agent, and definition fingerprint and must not be tainted. A mismatch
or taint starts a fresh provider conversation after the next successful call;
it does not delete the older provider-native data.

## Antigravity-native state

Antigravity owns a stable root below `provider_sessions/.antigravity/` and
partitions bound definitions by agent and definition fingerprint. Invocation
state is separately partitioned by session, agent, and definition fingerprint.
Each invocation uses sibling isolated provider and working directories and
copies only the verified bound rules into the working directory. The runtime
deliberately retains this plaintext state across switch, pause, quit, EOF, and
`AgentBackend.close()` so a compatible provider conversation can resume later.

The manifest stores only the validated provider conversation reference;
Antigravity owns the native conversation contents beneath its isolated state.
Neither close nor taint is deletion, secure erase, or a retention policy. Back
up and protect this root with the rest of the workspace, and do not edit its
provider-managed contents.

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
Orphan claims fail closed. See [Persistence](../../design/persistence.md) and
[Use Antigravity agents](../../guides/antigravity.md).
