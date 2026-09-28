# Session and workspace storage

> **Availability:** V2 defaults, provider sessions, debates, conversations,
> current writing-artifact reference sources, provenance-backed publication records,
> part-scoped manuscripts, explicit-reference evidence, provider-native state,
> bounded runtime debug logs, bounded agent-exchange JSONL, and bounded
> rejected-provider-response artifacts persist as plaintext workspace files.
> Runtime-managed documents and records are strict UTF-8. These retained files
> and low-level components do not imply a current public story-execution path.
> Package-internal session Git resources are implemented for #455, but current
> session commands do not provision them.

Key terms: **default**—a persisted command or conversation fallback;
**manifest**—durable provider state; **access claim**—temporary process ownership;
**provider-native state**—vendor-managed conversation and execution data.

## Canonical workspace tree

`story init` creates one exact pristine schema-v2 root and commits its 25
initializer-owned files to `main`. “Lazy” paths below describe later runtime
owners; #454 does not create them in the initialization baseline.

```text
workspace/
├── .git/                                                       [common Git storage; fixed main baseline plus lazy session seeds]
│   └── story-writing-agents/runtime/session-provisioning/
│       ├── .lock                                               [lazy persistent repository guard]
│       └── <session_id>.json                                   [transient immutable provisioning journal]
├── .gitignore                                                   [exact runtime-family exclusions]
├── .story-writing-agents-initialize-v2.json                    [transient initialization journal; absent on success]
├── config/
│   ├── workspace.json                                          [exact schema-v2 marker and topology policy]
│   ├── {system_defaults.json,agent_configs.json,agents/,persona_templates/}
│   ├── conversation_storage_state.json                        [lazy explicit active-backend state]
│   └── .conversation_storage.lock                             [lazy conversation-storage mutation guard]
├── core_documents/{global_outline.md,part_outlines.md,character_sheets.md,narrative_rules.md} [initialized zero-byte identities]
├── agent_states/
│   ├── orchestrator_state/
│   │   ├── sessions/<session_id>.json                          [lazy overrides]
│   │   ├── cli_context.json                                   [lazy default session]
│   │   ├── conversation_defaults/<session_id>.json             [lazy default conversation]
│   │   ├── provider_route_generations.json                     [lazy route freshness]
│   │   ├── conversation_storage_transfers/
│   │   │   ├── filesystem-to-sqlite.json                       [lazy retained transfer checkpoint]
│   │   │   └── sqlite-to-filesystem.json                       [lazy retained transfer checkpoint]
│   │   ├── provider_sessions/.access-claims.lock               [lazy guard]
│   │   ├── provider_sessions/.antigravity/                      [lazy retained provider state]
│   │   ├── provider_sessions/<session_id>.json                 [lazy manifest]
│   │   ├── provider_sessions/<session_id>.access.json          [temporary claim]
│   │   ├── debates/<session_id>/<debate_id>.jsonl              [lazy history]
│   │   └── writing_artifacts/
│   │       ├── catalog.json                                    [lazy schema-2 current membership]
│   │       ├── applications/<application_id>.json              [lazy schema-1 immutable content-free history]
│   │       ├── transactions/<transaction_id>.json              [schema-1 content-bearing pending journal]
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
│   ├── runtime.jsonl.{1..5}                                    [lazy backups]
│   ├── agent_exchanges/                                        [lazy independent exchange store]
│   │   ├── .agent_exchanges.lock                               [lazy persistent guard]
│   │   ├── .state.json                                         [lazy workspace sequence high-water]
│   │   ├── .state.pending                                      [temporary state replacement]
│   │   └── <session_id>/                                       [lazy private session directory]
│   │       ├── <agent_id>.sequence.json                        [per-session/agent high-water]
│   │       ├── .<agent_id>.sequence.pending                    [temporary state replacement]
│   │       ├── .<agent_id>.segment.pending                     [temporary retained-suffix replacement]
│   │       ├── <agent_id>.jsonl                                [active exchange segment]
│   │       └── <agent_id>.jsonl.{1..7}                         [rotated segments; .1 newest]
│   └── provider_error_responses/                               [lazy bounded rejected responses]
│       ├── .lock                                               [lazy persistent guard]
│       ├── .pending                                            [temporary reusable staging leaf]
│       └── <UTC-basic-timestamp>-<UUID-hex>.json               [complete response artifact]
├── discussion_logs/
│   ├── conversation_storage.sqlite3                            [lazy explicit authoritative SQLite provision]
│   ├── .conversation_storage_fts5.sqlite3                      [lazy derived SQLite search companion]
│   └── <session_id>/
│       ├── <debate_id>.{state.json,jsonl,result.json}
│       ├── <debate_id>.context.json                            [lazy debate context]
│       └── interactive/conversations/<conversation_id>/
│           ├── metadata.json
│           ├── collaboration-history.jsonl                     [lazy canonical v2 history]
│           ├── .collaboration-history.lock                     [lazy excluded safe lock]
│           └── transcript.jsonl                                [unsupported sentinel; active v2 owner rejects it]
└── manuscript/<part-id>/<safe-relative-markdown-path>           [lazy create-only output]

<workspace-parent>/<workspace-name>.story-worktrees/
└── <session_id>/                                               [lazy exact linked session worktree]
```

Git logically owns `refs/heads/story/session/<session_id>` at the baseline and
`refs/story-writing-agents/runtime/sessions/<session_id>` at the seed. Those
refs may be loose or packed, so they are identities rather than guaranteed
filesystem leaves in the tree above.

The four core files are empty identities, not generated story content.
Initialization also creates selected empty directories, but Git records only
the exact regular-file manifest below. It creates no manuscript, session ID,
session branch/worktree/internal ref, provider state, conversation, artifact
catalog, runtime log, or later commit.

### Exact initialization manifest

The one parentless commit named `Initialize story workspace` contains every and
only these paths, sorted by UTF-8 path bytes:

```text
.gitignore
agent_states/character_custodian_state/.gitkeep
agent_states/execution_specialist_state/.gitkeep
agent_states/orchestrator_state/.gitkeep
agent_states/orchestrator_state/conversation_defaults/.gitkeep
agent_states/orchestrator_state/sessions/.gitkeep
agent_states/plot_architect_state/.gitkeep
config/agent_configs.json
config/agents/character_custodian/AGENTS.md
config/agents/common/AGENTS.md
config/agents/execution_specialist/AGENTS.md
config/agents/lead_orchestrator/AGENTS.md
config/agents/plot_architect/AGENTS.md
config/agents/specialist_collaboration/AGENTS.md
config/persona_templates/alternative_templates/.gitkeep
config/persona_templates/default_kim_eun_hee.md
config/system_defaults.json
config/workspace.json
core_documents/character_sheets.md
core_documents/global_outline.md
core_documents/narrative_rules.md
core_documents/part_outlines.md
debug_logs/.gitkeep
discussion_logs/.gitkeep
manuscript/.gitkeep
```

The transient `.story-writing-agents-initialize-v2.json`, `.git`, empty
directory entries, mutable logs/state, later artifacts, and arbitrary files are
excluded. Journal-backed initialization remains recursively closed and rejects
any extra entry. A journal-free ready-main check instead reads only required
directories, these 25 scaffold leaves, mutable configuration and referenced
personae, then verifies the exact baseline/index/`main` topology/history and an
exhaustive inventory in which every session branch/ref/worktree/journal fact is
exactly correlated. It does not enumerate unknown filesystem entries or
ignored/untracked Git entries. Those optional entries remain untrusted and are
validated by their owner when consumed.

A ready linked session validates all 25 Git-materialized leaves and their
ancestors, plus its local marker, configuration, referenced personae, index,
and correlated Git identity. It does not require schema-only empty runtime
directories that Git cannot materialize. Untracked entries and unstaged story
work are allowed; staged changes require recovery. Later session branch tips
may contain added paths, whose committed story/history validity belongs to the
story-commit and session-recovery owners rather than generic readiness.

`config/workspace.json` records this naming policy for one validated
session ID `<id>`:

```text
branch:   story/session/<id>
ref:      refs/story-writing-agents/runtime/sessions/<id>
worktree: <workspace-parent>/<workspace-name>.story-worktrees/<id>
```

The initial session set is empty. `story init` still creates none of these
resources. The package-internal #504 provision operation can materialize one
exact correlated triple while keeping the pristine `main` root invariant;
#455 owns command wiring. #508 provides internal content-free history evidence;
#509 owns story commits, #510 owns checkpoints, and runtime owns the separate
confirmation UI. Session provisioning/history state is separate from both the
root initialization journal and writing-artifact transaction journals.

Artifact state appears only when its direct repository owner is accessed; the
installed Lead path reaches it only after separate review and Apply controls.
`recovery_blocked.json` appears
only after artifact recovery cannot be trusted. Staging residue is
non-authoritative, never catalog-listed, retained after successful and failed
publication, and may grow. Only exact JSON entries under artifact
`transactions/` are artifact-recovery journals; they are unrelated to the
root initialization journal. See
[Canonical writing artifacts](../writing-artifacts.md).

<a id="optional-sqlite-conversation-storage"></a>

## Optional SQLite conversation storage

The fixed `discussion_logs/conversation_storage.sqlite3` file is the optional
authoritative SQLite conversation store. Importing its direct module creates
nothing and does not load `sqlite3`; the standard-library module is loaded and
checked only when a probe, preflight, provisioning, composition, adapter,
selection, startup, or transfer operation requests it. `story init` creates
neither this database nor an active-state marker.

For a plain-language walkthrough before these normative storage details, see
[SQLite conversation storage](../../guides/sqlite-conversation-storage.md).

Desired and active storage are separate records. The desired value is the
`conversation_storage_backend` field in `config/system_defaults.json`; it is
exactly `filesystem` or `sqlite`, and missing or `null` means filesystem. The
active record is the strict schema-v1
`config/conversation_storage_state.json`:

```json
{
  "backend": "sqlite",
  "generation": 1,
  "schema_version": 1
}
```

`backend` is exactly `filesystem` or `sqlite`; `generation` is a nonnegative
integer. A missing record is the unwritten filesystem generation-zero baseline.
Malformed, unsafe, or unsupported state fails closed. Do not edit this marker
to switch stores.

`story storage use` and `/settings` save only the desired value after checking
readiness and exact canonical parity. They do not write the active marker.
SQLite selection may explicitly provision the fixed absent database as an
empty destination under a workspace-matched lease, but it does not copy
filesystem conversations. A host package installed or empty database created
during this preparation can remain after a later parity or configuration
failure.

The next interactive shell holds one outer lease while it reloads desired and
active state, composes the current active bundle, freshly checks target
readiness and parity, composes the complete target bundle, and compare-and-set
advances this marker. A pre-CAS failure preserves it and aborts startup without
fallback. A successful CAS remains durable even if later terminal, provider,
backend, or session startup fails. The shell then supplies the one active
catalog/event-store/search/coordinator bundle to its controller, runtime, and
memory services. Direct `StoryRuntime` construction does not perform this
startup selection and defaults to a composed filesystem bundle unless its
caller supplies another complete bundle.

Supported mutations use the lazy `config/.conversation_storage.lock`
coordinator guard; the guard is not a database, desired setting, or activation
record. The filesystem conversation paths can coexist with the SQLite file,
but only the active backend is authoritative for the shell. There is no
automatic synchronization or shared authority. Explicit
`story storage transfer` can copy the active canonical store into the other
already-ready store, and `story storage verify` can compare them; neither
command changes desired or active state.

The main schema-v2 database contains catalog metadata and canonical v2
`collaboration_events`. Any row in the unsupported `conversation_events` table
fails the owning boundary before mutation. The selected shell reads/searches
the v2 authority. The fixed private
`discussion_logs/.conversation_storage_fts5.sqlite3` companion is a derived,
owner-only FTS5 hint resource. It can appear after provisioning or an effective
SQLite mutation, but absence, staleness, an unavailable FTS5 capability, or an
unusable companion never changes main-database readiness or exact search
results. Search falls back to the authoritative literal scan and performs no
repair. A later effective mutation may create an absent companion or refresh a
recognized safe one only after committing the main transaction under the same
workspace mutation lease.

Back up the main database as authoritative plaintext. The companion repeats
normalized searchable transcript text and must receive the same filesystem,
backup, and retention protection whenever it is retained; it may be omitted
from an authoritative backup because exact fallback does not depend on it.
There is no delete API or general repair operation. The supported direct index
owner can create an absent resource or rebuild only a recognized safe one
through its validated publication API; ordinary application search should not
manage hints. Do not hand-edit or remove an unsafe or malformed companion to
force search behavior; the adapter preserves and ignores it.

Host installation is available only through explicit selection consent; shell
startup and direct preflight never install or provision. Readiness is the
active Python interpreter's functional SQLite capability, not a standalone
`sqlite3` binary or FTS5. There is no live switch, dual write, source deletion,
MySQL adapter, SQLite-specific RAG coupling, or FTS-dependent result behavior.
Optional RAG can consume the active complete adapter-neutral bundle while
keeping its own derived generation separate. Explicit
transfer remains a separate provider-free command/direct API over two
already-ready stores; it never provisions or activates one. See the
[exact selection workflow](../cli.md#conversation-storage-selection),
[direct package API](../package-api.md#optional-sqlite-conversation-storage)
and [recovery guidance](../../operations/recovery-and-limitations.md#sqlite-conversation-storage-recovery).

Canonical archive wire schema is exactly `2`, while embedded catalog metadata
and transfer reports/checkpoints each retain their own schema version `1`.
These values describe different records. Archive schema 1, filesystem
`transcript.jsonl`, and SQLite `conversation_events` rows are unsupported
owning-boundary inputs: active storage and transfer reject them without
rewriting either store.

<a id="conversation-transfer-checkpoints"></a>

### Conversation transfer checkpoints

Transfer owns one retained checkpoint per direction:

```text
agent_states/orchestrator_state/conversation_storage_transfers/
├── filesystem-to-sqlite.json
└── sqlite-to-filesystem.json
```

Each is strict UTF-8 schema-v1 JSON with exactly these fields and nested shapes:

```json
{
  "schema_version": 1,
  "direction": "filesystem-to-sqlite",
  "source_fingerprint": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "source": {
    "conversations": 1,
    "events": 4
  },
  "progress": {
    "completed_conversations": 1,
    "total_conversations": 1
  },
  "completed": [
    {
      "session_id": "novel",
      "conversation_id": "c_0123456789abcdef0123456789abcdef"
    }
  ]
}
```

The fingerprint is lowercase hexadecimal SHA-256 over the complete canonical
source archive bytes. Counts are bounded by the canonical archive limits;
`completed` is a unique, sorted list of safe `(session_id, conversation_id)`
identities, its length equals `completed_conversations`, and total progress
equals the source conversation count.

Checkpoint files intentionally omit subjects, statuses, timestamps, event
content/kinds, errors, and FTS state. They remain plaintext identity and progress
metadata, so protect them with the workspace. The reader requires a bounded,
regular, singly linked stable file; duplicate/extra/missing fields, malformed or
noncanonical values, another direction, an unsupported version, symlinks, hard
links, replacements, and oversized state fail closed.

For SQLite-to-filesystem transfer, a checkpoint advances only after one
complete exact conversation is present and has been reread. On resume, fresh
source/destination exports must prove every recorded identity still matches.
Filesystem-to-SQLite uses one complete transaction and publishes its terminal
checkpoint only after final parity. A successful transfer retains the complete
terminal checkpoint; a later safe transfer with a changed source fingerprint
replaces it only after an otherwise unblocked plan. Do not edit or delete it to
force recovery.

Dry-run reads and validates the applicable checkpoint when planning reaches
that phase but never writes it. Verify neither reads nor writes a checkpoint.
Both may create the empty `config/.conversation_storage.lock` while acquiring
their outer coordinator lease. Transfer never changes
`config/conversation_storage_state.json`, other settings, or backend activation.
See [transfer recovery](../../operations/recovery-and-limitations.md#conversation-storage-transfer-recovery).

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
data bound. Debug records are plaintext. Ordinary records use schema version 1
and execution-profile records use schema version 2; both coexist in the same
JSONL stream. Their schemas are documented in
[Persistence](../../design/persistence.md#workspace-runtime-debug-log),
their containment and failure behavior in [Filesystem safety](../../operations/filesystem-safety.md#debug-log-filesystem-safety),
and their content boundary in [Privacy](../../operations/privacy-and-provider.md#workspace-debug-log-privacy).

<a id="agent-exchange-storage"></a>

## Agent-exchange storage

`story_writing_agents.agent_exchanges` now provides the direct
`WorkspaceAgentExchangeStore` API and its immutable contracts. Importing the
subpackage and constructing a store perform no I/O. The first `append()` can
create `debug_logs/agent_exchanges/`; `inventory()` on an absent store returns
an empty tuple without creating it, and `snapshot()` on an absent or unmatched
selection returns the fixed `not_found` storage failure without creating it.

`AgentBackend` now automatically maps every physical structured provider call
at its established and contextual seams into this store on a best-effort
basis. It records the request before invocation, any exact valid
`ProviderResult` immediately on return, and one closed outcome attempt. A typed
completed Antigravity failure can also supply a diagnostic response before its
failure outcome without constructing a valid `ProviderResult`. There
is a separate bare `/exchanges` integration. The controller resolves
conversation/debate subjects outside this store, selects exact inventory
identities, and passes one immutable session/scope/agent snapshot to the
base-installed isolated viewer. The store still has no subject, terminal,
viewer, picker, command, or external-package dependency.

Every stored line is one compact, key-sorted, strict-UTF-8 JSON object followed
by exactly one newline. The expanded schema-v1 shape is:

```json
{
  "schema_version": 1,
  "workspace_sequence": 42,
  "sequence": 9,
  "timestamp": "2026-08-30T01:02:03.456+00:00",
  "request_type": "no_response",
  "session_id": "novel",
  "scope_kind": "debate",
  "scope_id": "d20260830T010203_abcd1234",
  "agent_id": "plot_architect",
  "provider": "codex",
  "exchange_id": "provider_call_9",
  "content": "",
  "content_full_utf8_bytes": 0,
  "content_retained_utf8_bytes": 0,
  "content_full_characters": 0,
  "content_retained_characters": 0,
  "content_truncated": false,
  "content_sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

The field set is closed: an extra, missing, duplicate, noncanonical, or
wrong-typed field is corrupt. Exact enums are `codex`/`antigravity` for
`provider`, `request`/`response`/`no_response`/`outcome` for `request_type`, and
`conversation`/`debate` for `scope_kind`. Schema version 1 and its outer field
set are unchanged; automatic capture adds the `outcome` record-kind vocabulary
and a closed nested outcome vocabulary. `session_id` and `scope_id` match
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. `agent_id` is exactly
`lead_orchestrator`, `plot_architect`, `character_custodian`, or
`execution_specialist`. `exchange_id` matches
`[A-Za-z0-9][A-Za-z0-9_-]{0,127}` and is the record caller's physical-call
correlation identity. Automatic capture generates
`exchange_<32 lowercase hexadecimal characters>` and shares it across the
request, optional response, and terminal attempt. All records also preserve the
exact supplied session, scope kind/ID, agent, and provider. Local reference
loading creates no provider exchange. The store itself does not infer
or enforce record grouping, outcome transitions, provider behavior, or a
required `no_response` body.

Automatic capture places one compact key-sorted JSON value inside `content`.
The following are separate ordinary request, response, and outcome values:

```json
{
  "call_kind": "conversation",
  "payload": {},
  "prior_thread_id": null,
  "schema": {}
}
```

```json
{
  "actual_transport_utf8_bytes": null,
  "response_text": "provider text",
  "thread_id": "thread_1",
  "usage": {
    "cached_input_tokens": 3,
    "input_tokens": 11,
    "output_tokens": 7
  }
}
```

```json
{
  "code": "accepted",
  "outcome": "accepted"
}
```

The request fields are exactly `call_kind`, `payload`, `prior_thread_id`, and
`schema`. A returned response has `actual_transport_utf8_bytes`,
`response_text`, `thread_id`, and nullable `usage`; non-null usage has exactly
`cached_input_tokens`, `input_tokens`, and `output_tokens`. These four response
fields remain unchanged when no diagnostic is supplied. An optional
`response_diagnostic` preserves nullable `error` and `status`, the completed native
process's integer `exit_code`, and a `stderr` stream object. The stream has
exactly `encoding` and `data`: `utf-8` with strict decoded text, or `base64`
when the original bytes are not valid UTF-8. The encoding preserves the
original stream bytes before the storage prefix bound is applied.

A recovered Antigravity native `ERROR` includes that diagnostic alongside its
returned response fields. A completed Antigravity failure instead uses exactly
`provider_failure: true` and `response_diagnostic`, as in this synthetic example:

```json
{
  "provider_failure": true,
  "response_diagnostic": {
    "error": null,
    "exit_code": 1,
    "status": null,
    "stderr": {"data": "/w==", "encoding": "base64"}
  }
}
```

The corresponding terminal attempt has `outcome: "provider_failure"` and a
fixed code such as `provider_invocation_failed`. Diagnostic bytes never become
a semantic result or retry authority. Not every failure supplies them: failures
before a completed process, timeouts, and cancellations provide no partial bytes
through the shared runner, so automatic capture invents no response.

These nested values do not change the outer schema-v1 field set, the 262,144-byte
content prefix bound, or rotation and retention below. The bound covers the
complete encoded `content`, so truncation can leave incomplete nested JSON or
base64; inspect full/retained counts, `content_truncated`, and `content_sha256`
before decoding a stream. Encoding is not redaction. The separate
`provider_error_responses` store still accepts only returned bodies rejected by
shared decoding, excluding pre-result native failures. See
[agent-exchange privacy](../../operations/privacy-and-provider.md#agent-exchange-privacy)
and the [recovery gates](../../guides/antigravity.md#recover-a-completed-native-error).

Outcome values are exactly `accepted`, `rejected`, `provider_failure`, `cancelled`, and
`post_response_runtime_failure`. Their permitted fixed codes are:

| Outcome | Code |
| --- | --- |
| `accepted` | `accepted` |
| `rejected` | `malformed_structured_response`, `response_correlation_mismatch`, `response_semantic_rejected`, `action_exhausted`, `answer_validation_failed` |
| `provider_failure` | `provider_invocation_failed`, `provider_timed_out`, `provider_contract_invalid` |
| `cancelled` | `cancelled` |
| `post_response_runtime_failure` | `thread_persistence_failed`, `reference_runtime_failed`, `context_runtime_failed` |

Direct store callers may still use `no_response`; automatic backend capture
uses `outcome` for its closed terminal record and never stores arbitrary
exception text there.

The store assigns both sequence values and the timestamp. `workspace_sequence`
is one workspace-wide high-water sequence. `sequence` is independent for each
`(session_id, agent_id)` stream, not for each conversation/debate scope. The
timestamp is a valid UTC RFC 3339 instant with milliseconds and `+00:00`.
High-water state is reserved durably before a record append, so a failed call
can leave a gap. Gaps are valid and never reused; duplicate or decreasing
retained workspace or stream sequences are corrupt. The corresponding
canonical state documents are:

```json
{"schema_version":1,"workspace_sequence":42}
{"schema_version":1,"sequence":9}
```

`content` retains the longest complete strict-UTF-8 prefix at or below 262,144
bytes. `content_full_utf8_bytes` describes the caller's complete value;
`content_retained_utf8_bytes` describes the stored prefix.
`content_full_characters` and `content_retained_characters` are the corresponding
Python string lengths: counts of Unicode code points in the complete input and
decoded retained prefix. They are not UTF-8 byte counts, UTF-16 code units,
grapheme clusters, or terminal display columns. `content_truncated` is true
exactly when both retained counts differ from their full counts, and
`content_sha256` remains lowercase SHA-256 over the complete UTF-8 value,
prefixed by `sha256:`. A split multibyte code point is omitted, so a truncated
prefix can be up to three bytes shorter than the limit. These facts identify
truncation and permit comparison with a complete value; they do not encrypt,
authenticate, intrinsically verify an omitted suffix, or recover it.

Each active or backup segment is at most 2,097,152 bytes. Rotation occurs
before an append would overflow the active segment and retains one active plus
seven backups per session/agent stream, with `.1` newest and `.7` oldest.
After a successful append or reader validation, at most 16,777,216 bytes of
managed exchange JSONL are retained across the complete `agent_exchanges`
subtree. An interrupted post-append prune can temporarily leave up to one
two-MiB segment beyond that bound; readers report `capacity_exceeded`, and the
next safe append prunes before reserving a new sequence. Locks, high-water
state, pending files, directories, and every sibling under `debug_logs/` are
outside that data count.
Global pruning removes the oldest currently retained complete records by
`workspace_sequence`; per-stream rotation may already have expired older
records. Ordinary rotation or pruning is not secure erasure.

`inventory(session_id)` validates the complete store, then returns content-free
groups ordered by first workspace sequence for each exact
`(scope_kind, scope_id, agent_id)` selection. `snapshot(selection)` returns a
new immutable `bytes` value containing only that exact session/scope/agent's
retained canonical lines in stream-sequence order. Neither result exposes a
path, descriptor, or writable handle. Readers serialize with writers, never
repair storage, and create nothing when the store is absent.

`append()` returns a frozen content-free receipt with `session_id`, `agent_id`,
`exchange_id`, both assigned sequences, the assigned timestamp, both content
byte counts, both character counts, the truncation boolean, and the full
digest. It does not return content, a path, or a writable storage object.

Automatic capture treats every append as fail-open. Its lifecycle state moves
before each append, catches every serialization/storage failure, and never
retries. A request failure disables that exchange; a later failure can leave a
request without a response or outcome. A completed-failure diagnostic response
is attempted at most once; failure of its serialization or append still permits
the fixed terminal attempt. Gaps, absent exchange IDs, and cleanup-only storage
failures are therefore not proof that no provider call occurred.

All store files are verified owner-only regular single-link leaves with mode
`0600`; the exchange and session directories are mode `0700`. The independent
guard has a 0.1-second acquisition bound. Public storage failures contain only
one of `unsupported_platform`, `contention`, `unsafe_storage`,
`corrupt_storage`, `capacity_exceeded`, `io_failed`, or `not_found`, with no
path or content detail. Filesystem enforcement and recovery are documented in
[Filesystem safety](../../operations/filesystem-safety.md#agent-exchange-filesystem-safety),
persistence ordering in
[Persistence](../../design/persistence.md#agent-exchange-persistence), and the
plaintext boundary in
[Privacy](../../operations/privacy-and-provider.md#agent-exchange-privacy).
The installed inspection workflow is in
[Inspect agent exchanges](../../guides/agent-exchanges.md).

<a id="rejected-provider-response-storage"></a>

## Rejected provider-response storage

`debug_logs/provider_error_responses/` is a separate lazy plaintext store for
completed Codex or Antigravity response text rejected by structured decoding.
Workspace initialization, module import, backend construction, successful
provider work, and ineligible provider failures do not create it. An eligible
backend rejection attempts storage, but bounded lock contention, unsupported or
unsafe filesystem state, malformed existing entries, or another storage failure
can leave the directory or record absent without replacing the original error.

The persistent `.lock` serializes writers with a 0.1-second acquisition bound.
`.pending` is a synchronized same-directory staging leaf; a later storage
attempt can validate and remove a stale one before atomically publishing a
complete timestamp-and-UUID `.json` file. `.pending`, `.lock`, and unknown or
unsafe entries are not complete response records and must not be hand-edited.

Each complete schema-v1 file is at most 2,097,152 bytes. Its body retains at
most 262,144 UTF-8 bytes at a complete code-point boundary while its digest and
original byte count describe the full response. Before publication, the store
prunes the oldest verified complete files until no more than 32 files and
16,777,216 total serialized bytes remain. Pruning and ordinary user deletion
are not secure erasure. The exact content boundary and safe inspection workflow
are in [Privacy](../../operations/privacy-and-provider.md#rejected-provider-response-artifacts).

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

<a id="provider-route-generations"></a>

## Provider route generations

`agent_states/orchestrator_state/provider_route_generations.json` is a
runtime-owned schema-v1 freshness record:

```json
{
  "agents": {
    "character_custodian": {"generation": 0, "provider": "codex"},
    "execution_specialist": {"generation": 0, "provider": "codex"},
    "lead_orchestrator": {"generation": 0, "provider": "codex"},
    "plot_architect": {"generation": 0, "provider": "codex"}
  },
  "schema_version": 1
}
```

Those are the only keys. Every canonical agent has an exact `codex` or
`antigravity` provider and a nonnegative integer generation; canonical JSON
uses sorted keys. When the record is absent, current configuration derives
generation zero. Backend construction creates or reconciles it, including a
provider change made directly in configuration while processes were stopped.
Each provider change increments that agent's generation, including A→B→A.
Only a confirmed full-path `/settings` Apply Changes updates it in the same
prepared boundary as the two configuration files when a switch requires it. A
Message-details-only or Conversation-RAG-only Apply Changes performs no route-
generation work.

The generation is not a user preference. A nonzero value participates in the
definition fingerprint and forces a fresh target-provider thread; zero keeps
legacy fingerprints unchanged. Other active backend instances retain their
captured map. Do not edit or delete this record to force reuse or recovery; use
`/settings`, or stop all processes and restore a verified complete workspace
backup when the record is damaged.

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
include UTC offsets and are ordered. A thread key is either the base
`<backend>:<agent_id>` form or the fingerprint-qualified
`<backend>:<agent_id>:<fingerprint>` form. Each thread value has exactly
`backend`, `agent_id`, `fingerprint`, `thread_id`, `tainted`, and `updated_at`;
its key must match its backend, agent, and, when qualified, its safe
fingerprint. Thread IDs use the documented safe pattern and `tainted` is
boolean. Opening ready state and releasing access do not rewrite the manifest.

The base key is used when the effective continuation fingerprint is the bound
definition fingerprint. A different effective fingerprint, including a Codex
or Antigravity session-generation fingerprint, uses the qualified key. Lookup
and taint for that generation address only the exact qualified record; they
never fall back to or mutate the legacy base record. A successful store writes
the qualified record while retaining the legacy base record.
Other qualified generations are retained as well: ordinary load, store, and
taint operations do not prune provider-thread history.

Codex and Antigravity threads may coexist in one manifest and remain distinct
because the backend is part of the key; multiple qualified fingerprints for one
backend and agent may coexist as well. A compatible thread requires the same
backend, agent, and effective continuation fingerprint and must not be tainted.
A mismatch or taint starts a fresh provider conversation after the next
successful call; it does not delete the older provider-native data.

This qualified-key extension retains schema version 2 but is forward-only once
a qualified record has been written. Older binaries that accept only base keys
reject such a manifest; do not downgrade in place or delete individual thread
records to make it open. Stop all writers and restore a verified pre-upgrade
workspace backup, or run a version that understands qualified records.

## Antigravity-native state

Antigravity owns a stable root below the canonical control workspace's
`provider_sessions/.antigravity/`, including when `AgentBackend` was constructed
from a session worktree. It partitions bound definitions by agent and
definition fingerprint. Invocation state is separately partitioned by session,
physical session-generation digest, agent, and definition fingerprint at
`sessions/<session>/<generation>/<agent>/<definition>/provider`. It remains
outside the selected session worktree, while provider CWD remains that exact
worktree. Any overlap is rejected before per-invocation state directories or
provider work. The runtime deliberately retains this plaintext state across
switch, pause, quit, EOF, and `AgentBackend.close()` so a compatible, proved-
generation provider conversation can resume later. Foreign or unproved
generation state is retained but starts fresh rather than being adopted.

The selected session worktree contains persistent runtime-control projections
at `.agents/agents/<native-selector>/agent.md`. A domain-separated SHA-256
selector is derived from the semantic agent ID and deterministic base role
bytes, then used as the role YAML name, directory, and `--agent` value.
Projection owns and revalidates an immutable plan tying the semantic ID, native
selector, and exact final bytes. Before setup, the adapter recomputes that plan,
the staged role, and component-byte accounting from the immutable definition
snapshot and requires the staged file and binding accounting to match. The
semantic ID still owns runtime state, settings, and accounting. Each projected
file exactly matches the staged role, is singly linked with exact mode `0400`,
and has owner-controlled directories. Content-qualified versions are published
create-only, coexist, and are never replaced or pruned. An exact safe concurrent
winner may be adopted only after full validation; a differing or unsafe winner
is preserved and rejected. A pre-existing empty qualified directory is
preserved and rejected before Git-exclusion or role-tree mutation. Selected-
role content validation is bounded by the exact planned size; unselected
qualified leaves are metadata-validated without content reads. The top-level
`.agents` namespace and its case-folded aliases must remain untracked; any
tracked variant fails closed before projection mutation or provider submission,
including on case-sensitive worktrees for portability.

The common Git exclusion contains `/.agents/`; its parser recognizes LF and
CRLF records, not lone CR separators, and fails closed without rewriting when
a missing entry follows a terminal lone CR. Intended final bytes may not exceed
64 KiB. If `exclude` is absent, it is published create-only. If a present safe
file is readable by its effective owner, runtime opens it no-follow. After
exact identity, size, and named-path checks, the retained inode may be
normalized to `0600` before the suffix append; runtime then syncs it and
requires exact final named-path content and identity readback. A normal append
preserves the inode; a concurrent pathname winner is preserved and never
overwritten.

Story references, SEARCH/READ sources, and artifact paths also exclude this
tree. It is therefore unversioned protected control data, not mutable provider-
native residue or story content. If required effective-user, descriptor-
relative, no-follow, nonblocking, or create-only link support is missing,
projection fails before access. Differing/unsafe bytes or foreign topology fail
closed and are preserved. Failed fresh setup removes only still-empty,
identity-matching directories created by that call.

Each successful invocation supplies one JSON result with a safe
`conversation_id`; resume requires it to equal the retained reference. The
manifest stores only that validated reference. It does not store native
`init`/step attestations: the JSON transport exposes none. Request JSON travels
as a process-visible `--print` argument with empty stdin; see
[provider privacy](../../operations/privacy-and-provider.md#provider-processes-and-credentials).

Antigravity owns the native conversation contents beneath its external state.
Neither close nor taint is deletion, secure erase, or a retention policy. Back
up and protect this root with the rest of the workspace, and do not edit its
provider-managed contents or the protected session role projection.

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
`WorkspaceVersionUnsupportedError`. They are not converted, rewritten, or
deleted. A root with recognizable pre-v2 workspace state is not accepted as a
v2 workspace. Preserve it and create a separate new v2 workspace; there is no
migration, compatibility, manual-reset, or fallback procedure. See
[workspace initialization recovery](../../operations/recovery-and-limitations.md#workspace-initialization-recovery).

Readers reject malformed data, invalid keys/types/IDs, and unsupported versions.
Orphan claims fail closed. See [Persistence](../../design/persistence.md) and
[Use Antigravity agents](../../guides/antigravity.md).
