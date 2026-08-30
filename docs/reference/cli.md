# CLI reference

> **Availability:** bare `story` and `story chat` open the TTY shell for session
> and conversation management, persistent settings, advisory/local reads, and
> focused-context consensus after native provider authentication succeeds.
> Provider-bound prompt text accepts inline `${path}` and `${path}#Lx-Ly`
> canonical artifact references. In eligible
> interactive prompt spans, `$`, `$${path}`, and `$$` discover and attach them
> without submitting. Separate provider-free `story storage` commands select a
> desired next-shell backend or migrate and verify already-ready stores.
> Noninteractive `story rag` commands inspect and operate the optional fixed
> local conversation-RAG projection without authenticating a story-agent
> provider. Interactive `/settings` persists the next-shell local/automatic
> policy, and `/rag QUERY` privately arms one forced preflight for the next
> eligible focused story-agent turn.

`story` and `story-writing-agents` are aliases for
`story_writing_agents.cli:main`.

## Top-level syntax

| Command | Exact syntax |
| --- | --- |
| Interactive | `story` or `story chat [SESSION] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--antigravity-executable EXECUTABLE] [--antigravity-model MODEL] [--antigravity-effort EFFORT] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--antigravity-executable EXECUTABLE] [--antigravity-model MODEL] [--antigravity-effort EFFORT] [--codex-timeout SECONDS] [PROMPT | --prompt TEXT | --draft TEXT | --draft-file PATH] --output PART/PATH.md [--debate-id ID] [--max-debate-rounds N]` |
| Inspect | `story inspect [--workspace PATH] [--session ID] [--debate ID]` |
| Session create | `story session create SESSION [--workspace PATH]` |
| Session reopen | `story session reopen [SESSION] [--workspace PATH]` |
| Session pause | `story session pause [SESSION] [--workspace PATH]` |
| Storage use | `story storage use BACKEND [--install-missing --yes] [--workspace PATH]` |
| Storage migrate | `story storage migrate --from BACKEND --to BACKEND [--dry-run] [--workspace PATH]` |
| Storage verify | `story storage verify --from BACKEND --to BACKEND [--workspace PATH]` |
| RAG status | `story rag status [--workspace PATH] [--session ID]` |
| RAG enable | `story rag enable --provider PROVIDER [--workspace PATH]` |
| RAG rebuild | `story rag rebuild [--yes] [--workspace PATH] [--session ID]` |
| RAG search | `story rag search QUERY [--workspace PATH] [--session ID]` |
| RAG disable | `story rag disable [--workspace PATH]` |
| RAG purge | `story rag purge [--yes] [--workspace PATH] [--session ID]` |
| Smoke | `story smoke [--workspace PATH]` |

Lifecycle commands have no `--session`. Omitted reopen/pause uses the default;
create requires a positional name. Create/reopen validate provider configuration,
transition to ready, save the default only after success, and release access.
Pause retains the default. Success prints sorted, indented JSON with exactly:

```json
{
  "access_state": "claimable",
  "durable_state": "ready",
  "session_id": "demo"
}
```

Pause prints `paused` instead of `ready`. Access is always `claimable` after a
successful lifecycle process exits.

<a id="conversation-storage-selection"></a>

## Conversation storage selection

`story storage use BACKEND [--install-missing --yes] [--workspace PATH]`
accepts exactly `filesystem` or `sqlite`. It resolves `--workspace PATH` or the
nearest initialized workspace and saves only the desired backend for the next
shell. It never migrates conversations, deletes a source, changes the active
marker, switches a running shell, dual-writes, or copies the derived FTS
companion.

The command is provider-free and dispatches before debug-log construction,
broader CLI context, authentication, provider/backend/runtime, settings, or
session construction. Import and request validation perform no SQLite or
installer operation. Standard output is reserved for one compact key-sorted
schema-v1 JSON object plus LF. Installer stdout and stderr, when an explicitly
authorized installer runs, are both routed to the CLI's standard error.

SQLite readiness means that the active Python interpreter can import its
standard-library `sqlite3`, report SQLite 3.7.16 or newer, open and close an
in-memory connection, enable foreign keys, and execute a query. The standalone
`sqlite3` executable and FTS5 capability are not gates. Workspace coordination
must be available before installation, provisioning, selection, or activation
can mutate anything.

When that interpreter probe is unavailable, Story recognizes only these exact
host plans:

| Host match | Exact command |
| --- | --- |
| Darwin with `brew` discoverable | `brew install sqlite` |
| Linux with exact `/etc/os-release` `ID=debian` or `ID=ubuntu` | `apt-get install sqlite3 libsqlite3-dev` |
| Linux with exact `/etc/os-release` `ID=fedora` or `ID=rhel` | `dnf install sqlite sqlite-devel` |

Other operating systems, Linux IDs, and package-manager shapes are unsupported;
`ID_LIKE` is not used. The command never adds a shell, `sudo`, `-y`, another
argument, a custom environment, retry, or timeout. It executes the allowlisted
argument tuple at the workspace root with inherited stdin and environment and
runs it at most once. It then fully reprobes SQLite in the same Python process;
an installer exit zero is not readiness proof.

Host installation is refused when any of these recognized variables has a
nonempty value: `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `CIRCLECI`, `BUILDKITE`,
`TF_BUILD`, `JENKINS_URL`, `TEAMCITY_VERSION`, `TRAVIS`, `APPVEYOR`,
`BITBUCKET_BUILD_NUMBER`, `CODEBUILD_BUILD_ID`, or `DRONE`. Empty values and
unrelated variables do not trigger that refusal.

The CLI never prompts for consent. `--install-missing` and `--yes` must be
supplied together and are valid only with `sqlite`; either flag alone or either
flag with `filesystem` reports `invalid_request`. With no authorized install,
a recognized plan reports `installation_required`. In `/settings`, the
equivalent interactive path previews the exact command, highlights NO, and
requires a separate exact YES; explicit decline and recognized-CI refusal both
map to `installation_disallowed`.

After functional readiness, an explicit SQLite selection preflights the fixed
database and provisions it create-only when absent. Provisioning creates an
empty destination; it never copies the filesystem store. Under the workspace
lease, selection then compares fresh canonical archives from the current active
backend and requested target. Same-backend selection or an empty active source
needs no parity. A nonempty source requires exact parity: an empty target
reports `migration_required`, while a nonempty divergent target reports
`parity_mismatch`. Exact comparison includes active and archived metadata and
every event, including `runtime_error`; FTS is excluded.

Use this sequence when moving a nonempty filesystem store to SQLite:

```bash
story storage use sqlite
# If installation_required, review the table above before authorizing:
story storage use sqlite --install-missing --yes
# If migration_required, the empty database remains ready:
story storage migrate --from filesystem --to sqlite --dry-run
story storage migrate --from filesystem --to sqlite
story storage verify --from filesystem --to sqlite
story storage use sqlite
story
```

Use the reverse migration direction before selecting filesystem when SQLite is
active and nonempty. Every invocation rereads current state. Installation or an
empty provisioned database can remain after later parity, persona-catalog,
configuration-conflict, or configuration-write failure. Selection does not
change activation, but `configuration_write_failed` can include rollback
uncertainty; inspect both configuration files before retrying. Never delete
either store to force the workflow.

A representative completed no-op selection is:

```json
{"activation":"next_shell","configuration":"unchanged","desired_backend":"filesystem","failure":null,"installation":"not_required","operation":"use","outcome":"completed","parity":"not_required","provisioning":"not_required","schema_version":1}
```

The exact fields are `activation`, `configuration`, `desired_backend`,
`failure`, `installation`, `operation`, `outcome`, `parity`, `provisioning`, and
`schema_version`. `operation` is always `use`; `outcome` is `completed` or
`failed`; `desired_backend` is `filesystem`, `sqlite`, or `null`. The state
vocabularies are:

```text
installation/provisioning: not_required completed not_run failed
parity:                    not_required verified unverified mismatch
configuration:             saved unchanged preserved
activation:                next_shell unchanged
```

`failure` is `null` on completion; otherwise it is exactly one of:

```text
configuration_conflict configuration_invalid configuration_write_failed
coordination_failed installation_disallowed installation_failed
installation_required installation_unsupported interpreter_unavailable
invalid_request migration_required parity_mismatch provisioning_failed
workspace_invalid
```

Explicit decline or a recognized CI marker reports
`installation_disallowed`; an unsupported host plan reports
`installation_unsupported`; invalid consent flags report `invalid_request`;
functional SQLite absence without authorized installation reports
`installation_required`. Installer exit zero followed by failed same-process
reprobe reports `installation:"completed"` with
`failure:"interpreter_unavailable"`. Configuration rollback uncertainty maps
to `configuration_write_failed`. Reports contain no counts, conversation or
session identities, subjects, events, paths, exception text, installer output,
or provider data.

Exit status is `0` only for `completed`. Every reported selection failure and
every argparse syntax failure exits `2`; selection has no exit `1`. A completed
report always says `activation:"next_shell"`, including when configuration was
already canonical. That field means the running process was not switched; it
does not claim that the active marker changed.

At the next bare `story` or `story chat`, startup reloads desired and active
state under one outer lease. It composes the current active bundle, freshly
checks target readiness and canonical parity, composes the complete target
bundle, and compare-and-set advances the active marker before provider
authentication. Any pre-CAS failure aborts startup without falling back to the
old backend. A successful marker CAS is not rolled back if later terminal,
authentication, backend, or session startup fails. See
[storage state](configuration/session-storage.md#optional-sqlite-conversation-storage),
[filesystem safety](../operations/filesystem-safety.md#conversation-storage-selection-and-startup-safety),
and [recovery](../operations/recovery-and-limitations.md#conversation-storage-selection-and-activation-recovery).

<a id="conversation-storage-migration"></a>

## Conversation storage migration

`BACKEND` is exactly `filesystem` or `sqlite`, and the two values must differ.
The command uses `--workspace PATH` or the nearest initialized workspace. The
`--from` value must equal the workspace's current conversation-storage
activation state; absence of that state means filesystem generation zero.
SQLite must already be provisioned and pass readiness checks whether it is the
source or destination. These commands never install or provision SQLite,
change activation or settings, delete the source, dual-write future changes,
or copy the derived FTS companion.

Use the same direction for planning, transfer, and confirmation:

```bash
story storage migrate --from filesystem --to sqlite --dry-run
story storage migrate --from filesystem --to sqlite
story storage verify --from filesystem --to sqlite
```

Each invocation freshly exports both stores under one workspace mutation lease
and classifies every conversation identity as `create`, `identical`,
`conflict`, `unsupported`, or `destination_only`. Conflict or unsupported
SQLite values block both operations. `unsupported` means a source subject
revision or event sequence cannot fit SQLite's signed 64-bit integer range.
Destination-only data blocks migration; verify instead reports an otherwise
valid create/destination-only difference as a parity mismatch. Exact parity
covers every canonical active/archived metadata field and every event including
`runtime_error`. FTS data is excluded.

`--dry-run` is accepted only by `migrate`. It performs activation/readiness,
fresh exports, planning, and—when the plan is otherwise unblocked—checkpoint
validation, but writes neither destination conversations nor a checkpoint. It
reports `work_required` when at least one conversation would be created and
`completed`/`verified` when no creation is needed. A blocker still reports
`failed`. Dry-run and verify may create only the empty
`config/.conversation_storage.lock` used for their outer lease; verify ignores
checkpoint contents entirely.

Valid command grammar writes exactly one compact, key-sorted schema-v1 JSON
object plus LF to stdout and nothing to stderr. It creates no runtime debug
record and dispatches before broader CLI context, authentication, provider,
runtime, settings, or session construction. A representative dry-run line is:

```json
{"checkpoint":"none","counts":{"conflict":0,"create":3,"destination_only":0,"identical":0,"unsupported":0},"dry_run":true,"failure":null,"from":"filesystem","operation":"migrate","outcome":"work_required","parity":"unverified","schema_version":1,"source":{"conversations":3,"events":4},"to":"sqlite"}
```

The exact top-level fields are `checkpoint`, `counts`, `dry_run`, `failure`,
`from`, `operation`, `outcome`, `parity`, `schema_version`, `source`, and `to`.
`counts` has exactly the five planning categories above; `source` has
`conversations` and `events`. `checkpoint` is `none`, `resumed`, `advanced`, or
`completed`; `outcome` is `completed`, `work_required`, or `failed`; `parity`
is `verified`, `mismatch`, or `unverified`. Verify always reports
`"dry_run":false`. `failure` is `null` unless failed, then one of:

```text
activation_invalid activation_mismatch backend_unavailable checkpoint_invalid
checkpoint_unsupported conflict coordination_failed destination_invalid
destination_only invalid_request operational_failure parity_mismatch
provisioning_needed source_changed source_invalid unsafe_state unsupported
workspace_invalid
```

Reports expose only fixed backend/outcome/failure values, checkpoint state, and
aggregate counts. They do not include conversation identities, subjects,
events, paths, exception text, malformed values, or FTS data.

Exit status is fixed:

- `0`: successful migration, valid dry-run (including `work_required`), or
  verified equality;
- `1`: verify-only, otherwise valid create or destination-only parity mismatch;
  and
- `2`: argparse usage, request/precondition failure, conflict, unsupported
  data/version, unsafe state, checkpoint failure, source/destination failure,
  or another operational failure.

A successful migration retains a terminal direction-specific checkpoint and
freshly proves source/destination canonical parity, but never activates the
destination. Failure after destination work does not imply rollback across
both stores. See [Conversation storage migration recovery](../operations/recovery-and-limitations.md#conversation-storage-migration-recovery)
before retrying and [Session storage](configuration/session-storage.md#conversation-migration-checkpoints)
for checkpoint contents.

<a id="conversation-rag-operations"></a>

## Conversation RAG operations

The installed `story rag` family operates only the fixed network-free `local`
provider. It performs no story-agent authentication, provider call, external
provider discovery, or optional-package installation. The base installation
can inspect status; enable, rebuild, and search require the exact-pinned
`generic-rag` 0.1.0 distribution and capability manifest. Installing that
extra does not change workspace configuration.

The operation scopes are exact:

| Operation | Scope and effect |
| --- | --- |
| `status` | Session-scoped. Uses `--session ID` or the saved default. Optimistically reads the persisted active filesystem/SQLite authority and derived state twice. It does not acquire/create the conversation-storage mutation lock, create the RAG subtree, rewrite configuration, activate desired storage, or clean orphans. Every valid observation—including non-ready state—has outcome `completed` and exit `0`. |
| `enable` | Workspace-scoped. Requires exact `--provider local`; no session is accepted. It checks the optional distribution/version/capabilities before a complete configuration compare-and-set. Success saves provider `local` or reports configuration `unchanged`; it preserves the current automatic boolean and builds nothing. |
| `rebuild` | Session-scoped. Without `--yes`, returns `confirmation_required` before storage activation. With confirmation, it runs configured-storage startup/activation, requires enabled compatible local RAG, rebuilds the complete authorized active target from the beginning, verifies publication while the mutation lease remains held, then returns the same inspection shape as status. It never provisions or migrates storage. |
| `search` | Session-scoped. Requires one nonblank strict-UTF-8 positional `QUERY`, runs configured-storage startup/activation, requires enabled compatible local RAG, and performs bounded literal-first plus local-vector retrieval. Every retained range is reauthorized and reread from the current authoritative store. It invokes no story agent and never echoes the query. Quote multiword shell input. |
| `disable` | Workspace-scoped. Accepts no session. A complete configuration compare-and-set clears the provider and sets automatic to `false`; derived and authoritative data remain. |
| `purge` | Session-scoped. Requires `--yes` and removes only the addressed session's validated derived active/status/generation contents. It does not require provider enablement, compose authoritative storage, or alter configuration; authoritative conversations and other sessions remain. |

Status composes only the persisted active backend. Confirmed rebuild and search
instead call the normal desired-storage activation workflow before RAG
dispatch, so a ready and canonically equal next-shell selection can advance the
active marker. Neither operation installs/provisions SQLite, migrates data,
falls back to another backend, or couples the derived generation store to a
storage adapter. Purge does not consult either backend.

Valid command grammar writes exactly one compact key-sorted schema-v1 JSON
object plus LF to standard output, writes nothing to standard error, and
creates no runtime debug record. Every report has this exact top-level key set:

```text
automatic, authoritative_checkpoint, compatibility, configuration, counts,
excerpts, failure, indexed_checkpoint, operation, outcome, provider, purged,
rebuild_required, retrieval_outcome, retrieval_reason, schema_version, state,
truncated
```

`schema_version` is `1`; `operation` is `status`, `enable`, `rebuild`, `search`,
`disable`, or `purge`; and `outcome` is `completed` or `failed`. `provider` is
`"local"` or `null`. `automatic` reports the persisted boolean when applicable.
`configuration` is `not_applicable`, `saved`, `unchanged`, or `preserved`.
`state` is `disabled`, `unavailable`, `missing`, `ready`, `stale`, `failed`,
`corrupt`, `incompatible`, or `null`. Compatibility is `compatible`, one of
`provider_unsupported`, `dependency_missing`, `version_incompatible`, and
`capability_incompatible`, or `null` when inapplicable.

`counts` always contains exact `authoritative` and `indexed` objects:

```text
authoritative: conversations, documents, events, source_utf8_bytes
indexed:       conversations, documents, events, fragments, generation_bytes,
               generations, orphan_generations, payload_bytes,
               source_utf8_bytes
```

Status and successful rebuild populate the counts plus SHA-256 authoritative
and optional indexed checkpoints and `rebuild_required`. Enable/disable leave
those payload fields empty. Purge reports only nonnegative `generations` and
`orphan_generations` in the always-present `purged` object, sets state to
`missing`, and sets `rebuild_required:true`.

Search sets `retrieval_outcome` to `complete`, `partial`, or
`literal_fallback`; `retrieval_reason` is `none`, `partial`, `disabled`,
`unavailable`, `stale`, `corrupt`, `incompatible`, or `failed`; and `truncated`
is a boolean. `excerpts` is ordered with contiguous `g1` through `g20`
citations. Each item has exactly `citation_id`, `content`, `source`, and
`reference`; `source` has `conversation_id`, `event_kind`, `sequence`,
`session_id`, and `turn_id`; `reference` has `document_id`, `end_codepoint`,
`fragment_id`, `revision_id`, and `start_codepoint`. Content is the bounded
authoritative reread, not provider-returned text. Query text, subject, score,
workspace path, credential, exception, and provider diagnostic are absent.

`failure` is `null` on completion. A failed report keeps state/checkpoint/
retrieval fields null and its counts, purge counts, and excerpt list empty; it
uses exactly one of:

```text
invalid_request workspace_invalid session_invalid provider_disabled
provider_unsupported dependency_missing version_incompatible
capability_incompatible configuration_invalid configuration_conflict
configuration_write_failed storage_unavailable coordination_failed
confirmation_required unsafe corrupt incompatible provider_failed
operation_failed
```

A semantic/precondition failure exits `2` with that single JSON line. Argparse
syntax errors also exit `2`, but argparse owns their usage text on standard
error and emits no report. Completed status/search results exit `0` even when
state is non-ready or retrieval used an explicit literal fallback. There is no
RAG exit `1`. `KeyboardInterrupt` and `SystemExit` remain process controls.

The persisted keys, defaults, and compatibility invariant are in
[Agent configuration](configuration/agent-configuration.md#system-defaults).
The trust and output boundary is in
[Privacy and provider exposure](../operations/privacy-and-provider.md#conversation-rag-command-privacy),
and safe recovery is in
[Recovery and limitations](../operations/recovery-and-limitations.md#conversation-rag-recovery).

<a id="smoke-report"></a>

## Smoke report

`story smoke` writes compact key-sorted JSON. Success keys are exactly
`checks`, `command`, `debate_id`, `debate_status`, `note`, `provider_tested`,
`revision_id`, `rounds_completed`, `session_id`, `status`, and `workspace`; a
named invariant failure adds only `failed_check`.

On a pass, `provider_tested:false`; checks, in order, are
`workspace_initialized`, `directive_persisted`,
`conversation_catalog_persisted`, `conversation_routing`,
`conversation_search`, `conversation_continuity`, `conversation_retrieval`,
`consensus_context`, `revision_workflow`, `consensus_persisted`,
`manuscript_committed`, and `core_documents_unchanged`.

Implicit smoke owns, initializes, and removes its temporary `Workspace`.
Explicit CLI smoke initializes one `Workspace`, retains it, and passes that
exact instance to the smoke workflow; the CLI does not resolve the path again
for handoff or post-run diagnostics.
Explicit workspaces retain any plaintext diagnostics that were successfully
written.
Exit `0` passes, `1` names a failed invariant, and `2` covers usage/setup or an
unexpected failure. See the [smoke guide](../guides/smoke-test.md).

<a id="runtime-debug-logs"></a>

## Runtime debug logs and failure hints

Workspace-aware installed commands best-effort append fixed operational records
to `debug_logs/runtime.jsonl` only after workspace initialization or discovery
succeeds. This includes successful interactive operations that are read-only
with respect to story/session state, such as inspection, shell status, help,
literal shell search, transcript, history, and lifecycle reads. The bounded log
is a separate side effect; those operations retain their domain-state contract.
The compact machine workflows under `story storage` and `story rag` dispatch
before debug-log construction and never create or append this log.

After that successful workspace boundary, the CLI marks later application
errors as hint-eligible before attempting to construct an optional diagnostic
handle. Handle acquisition and record submission are independently guarded, so
neither is required for the command to continue or for an eligible hint to
appear.

| Boundary or result | Internal behavior | What the user sees |
| --- | --- | --- |
| Before a usable workspace | No workspace-bound diagnostic handle is acquired, so this invocation submits no record. | Argparse, TTY, interactive-option, initialization, and discovery errors retain their established output and show no debug-log hint. |
| Resolved or initialized workspace; normal activity | The CLI marks hint eligibility and attempts its optional handle; the owning CLI, controller, backend, or runtime may attempt fixed trace records. | Normal success and domain-result output is unchanged. When created, the active log may be inspected at the fixed relative path. |
| Handled post-workspace application error | The responsible boundary may attempt a content-safe error record; presentation does not depend on a handle or record existing. | Interactive rendering adds the exact third notice below. A caught noninteractive exception preserves its first stderr line and adds the exact second line below. |
| Diagnostic acquisition or submission fails | Consumer-private guards catch even a diagnostic `BaseException`; the handle or record may be absent. | The operation's result, exception, cancellation, cleanup, output, and exit status remain authoritative. A displayed hint is only an availability notice. |
| Implicit smoke | Fixed records are attempted only inside the temporary workspace; normal return or exception propagation removes that workspace and any records successfully written. | A returned compact report has no debug path or hint; a propagated exception also exposes none, and no workspace remains to inspect. |
| Explicit smoke | The CLI initializes and retains one identity-bearing `Workspace`; smoke and every fresh workspace-bound service borrow that exact instance. CLI and smoke diagnostic-handle acquisition and each submission are guarded separately, and the CLI does not re-resolve the path for handoff or post-run diagnostics. | Reports retain their established compact output. After initialization succeeds, a propagated ordinary `Exception` caught by the CLI keeps its first stderr line and always gets the fixed second line, even when the record is absent. Root replacement can omit records but cannot redirect them or change output or exit status. Initialization failure is hint-free; a genuine process-control exception propagates unchanged. |

Each submitted interactive command is classified independently when its final
trace can be written. A normally returned command that successfully rendered a
typed handled error ends `failed`/`rejected`; a normally returned command that
rendered no typed error ends `succeeded`/`completed`. This records whether that
command displayed a handled error, not whether it mutated story/session state or
retried provider work. An earlier command's error cannot reclassify a later
command, and neither classification changes terminal output or domain results.

Every rendered interactive application error after workspace resolution adds
this exact third notice to its existing error panel:

```text
[debug log] If available, workspace-relative: debug_logs/runtime.jsonl
```

Except for implicit temporary smoke, a caught noninteractive exception after
CLI workspace initialization or discovery succeeds retains its established
first stderr line and adds this exact second line, even when diagnostic-handle
acquisition failed and no log file exists:

```text
Debug log (if available, workspace-relative): debug_logs/runtime.jsonl
```

Returned nonexception outcomes do not use the exception hint: a smoke invariant
exit `1` remains compact JSON, and run deadlock/failed-result exits `3`/`4`
retain their established JSON. Success output also has no hint. “If available”
is literal: handle acquisition, contention, unsafe storage, unavailable safe
descriptor-relative filesystem primitives, or another logging failure can
leave the file or a particular record absent. Successful CLI workspace entry—
not logger acquisition—is the hint boundary for `init`, explicit smoke, and
ordinary resolved-workspace commands. Initialization and discovery failures
remain hint-free.

Users can inspect the fixed path beneath the workspace root, but cannot
configure another destination or recover provider stderr through this feature.
Neither a hint nor a record proves that an operation completed. Content and
retention limits are in
[Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy)
and [Session storage](configuration/session-storage.md#runtime-debug-log-storage).

The CLI adds no output, command, or hint for rejected-response artifacts. When
a completed Codex or Antigravity response reaches structured decoding and is
rejected, a separate best-effort JSON record may exist beneath
`debug_logs/provider_error_responses/`; runtime JSONL remains content-safe. The
directory can be absent, and it never contains provider stderr or a
pre-response transport/timeout/cancellation failure. Correlate a candidate by
provider, agent, session, and debate or conversation metadata, and inspect its
hostile provider-controlled body only in a trusted plain-text editor. See the
exact eligibility and safe workflow in
[Privacy](../operations/privacy-and-provider.md#rejected-provider-response-artifacts).

<a id="interactive-provider-authentication"></a>

## Interactive provider authentication

Bare `story` and `story chat` resolve and, when needed, activate configured
conversation storage after workspace/configuration loading and terminal
construction, but before provider authentication. A capable raw terminal shows
the text-free colorful loading row while that activation is pending, then
clears it before the welcome. After authentication, a second loading span
covers CLI-context loading plus `AgentBackend` and controller construction and
clears before the lobby. Callback and incapable terminals remain silent during
both spans. Neither span adds status copy or a durable record. The runtime
launches only these native commands without a shell:

| Provider | Silent bounded status probe | Inherited-terminal sign-in |
| --- | --- | --- |
| Codex | `codex login status` | `codex login` |
| Antigravity | `EXECUTABLE models` | `EXECUTABLE` with no arguments |

For Antigravity, `EXECUTABLE` is `agy` by default or the resolved
`--antigravity-executable PATH` value on `story chat`.

Status probes run at the resolved workspace root with null stdin/stdout/stderr.
Exit `0` is authenticated, exit `1` is unauthenticated, a missing executable is
unavailable, and timeout or any other result is indeterminate. Unavailable
choices are disabled; authenticated choices need no sign-in; unauthenticated
choices start native sign-in; indeterminate choices trigger another status
check. While a login child remains open, Story waits in one-second bounded
intervals and runs the selected provider's existing silent, bounded status
probe after each interval. The probe's null streams keep it unable to consume
pasted input or capture provider output. A non-authenticated probe leaves the
native interaction in control; there is no overall login deadline.

A fresh exact authenticated result is the sole authority. When it arrives,
Story boundedly terminates and reaps only that exact login child if still open,
then refreshes both providers and returns automatically to the calling startup,
`/auth`, or Settings flow. The user does not need an exit command, Ctrl-C, EOF,
extra Enter, confirmation, or second code paste. If the login child exits
first, Story still requires a fresh probe. Prior state, code text, child exit,
and return code never authorize use.

If neither provider is authenticated, the startup picker remains the hard gate.
Cancel returns to that gate; Ctrl-C or EOF exits normally without constructing
the story runtime. Once at least one provider authenticates, a missing workspace
`default_backend` is persisted from the provider authenticated by that immediate
interaction, otherwise the sole authenticated provider, otherwise Codex when
both are authenticated. An existing default is never replaced.

Effective routes then resolve in this order: repeatable CLI override, explicit
per-agent route, workspace default, Codex. Each distinct effective provider
must pass a fresh gate before startup continues; explicit routes are never
rewritten. `init`, `run`, `smoke`, help, malformed invocation, standalone
lifecycle commands, and non-TTY rejection do not open this interactive gate.

Inside the shell, global no-argument `/auth` refreshes both states and opens the
same picker without story-provider work. Cancellation returns to the shell and
keeps any already established default. A newly completed authentication can
durably fill a missing default, but never replaces an existing default or an
explicit route.

Native sign-in inherits stdin, stdout, stderr, environment, process group, and
the resolved workspace root and may open a provider-controlled browser. The
runtime neither synthesizes nor captures URLs, provider output, account facts,
tokens, credentials, authorization codes, or native credential persistence.
Story never supplies its own code prompt: a successful flow has at most one
provider-controlled authorization-code paste, and local browser-capable flows
may need none. A remote provider can still require its own copy/paste step. See
[privacy](../operations/privacy-and-provider.md#native-authentication-boundary)
and [recovery](../operations/recovery-and-limitations.md#authentication-recovery).

## Provider routes and invocation settings

Every installed command that constructs an `AgentBackend` validates and
snapshots both the workspace system defaults and per-agent configuration. The
repeatable `--backend AGENT=BACKEND` accepts canonical
agent IDs and exactly `codex` or `antigravity`; it changes only that backend
instance. Duplicate agents are rejected. Legacy `gemini` is rejected with
fixed migration guidance to use `antigravity`.

For Codex, a non-null `--codex-model` on `story chat` or `story run` overrides
all persisted Codex model values; otherwise each agent inherits its
`codex_model`, workspace `codex_model`, then the provider default. Codex
reasoning resolves from the agent value, workspace value, then provider default.

For Antigravity, `--antigravity-executable` defaults to `agy`.
`--antigravity-model` and `--antigravity-effort` override every
Antigravity-routed agent in that backend instance. Otherwise model and effort
resolve per agent, then from workspace Antigravity defaults, then the provider
default. Effort accepts `low`, `medium`, or `high`. The legacy option name
`--codex-timeout` supplies the positive timeout for both provider adapters.

One mixed-provider shell can be opened with:

```bash
story chat draft \
  --backend plot_architect=antigravity \
  --antigravity-effort medium
```

Other agents retain their persisted routes. Bare `story` has none of these
options and uses the persisted configuration, workspace default, `codex`
fallback, and `agy`
executable default. Standalone lifecycle commands likewise have no provider
options, so a configured Antigravity route requires `agy` on `PATH`.

There is no `--codex-reasoning-effort` option or supported public
system-default save API. Global `/settings` provides the installed interactive
edit/apply workflow; choose `Agents`, then an agent, then Provider, Model, or
Effort. Each agent row shows its effective `(Provider/model/effort)` values,
and each Provider, Model, and Effort row shows its effective value beside the
field. It supports Codex and Antigravity and edits only the active provider's
per-agent model/effort while retaining inactive-provider values.
Entering Model or Effort discovers the pending effective provider afresh;
opening Settings and provider navigation do not launch catalog commands.
Provider option probing and Model/Effort discovery show transient progress on
capable raw terminals and remain visually silent in ordinary fallback. Custom
models and inheritance remain supported. See the exact
[picker contract](interactive-terminal.md#provider-model-and-effort-pickers).
Direct edits to the validated JSON documents remain possible, but there is no
file watcher: they are reconciled by a newly launched backend, and other
running shells retain their captured state. Confirmed `/settings` Apply Changes
instead validates, persists, and applies live-capable runtime settings to the
current shell without a restart. Desired conversation storage and Conversation
RAG remain next-shell settings and the current bundles are unchanged. A
Message-details-only Apply uses the configuration prepare/verify/commit fast
path and updates the active presentation flag without provider, storage,
backend/runtime, route, or thread activation work. A RAG-only Apply persists
only the startup-fixed system configuration without provider, storage,
backend/runtime, route/thread, composition, or optional-package work. Other
mixed changes retain the full activation path. Launch
overrides stay higher precedence, so the corresponding effective Provider,
Model, or Effort editor is disabled and read-only. Existing
three-key system-default files remain valid and provide no provider-specific
fallback until configured. Exact fields, validation, and initialization
compatibility are in
[Agent configuration](configuration/agent-configuration.md#system-defaults),
and invocation precedence is in
[Runtime overrides](configuration/runtime-overrides.md#resolution-precedence).
Antigravity installation, process controls, and verification are in
[Use Antigravity agents](../guides/antigravity.md).

<a id="interactive-streams"></a>

## Interactive management, scope, and status

Both streams must be TTYs before workspace/backend access or exit `2`. Capable
POSIX main, composition, confirmation, picker, and settings-prompt reads use the
terminal-owned live editor. The complete live frame—context, prompt, editable
buffer, suggestions or choices, validation, and redraws—is cleared before a
submit, commit, cancel, interrupt, or EOF returns; an exceptional failure
attempts the same cleanup before propagating. Persistent semantic
orientation, validation, cancellation, discard, error, and result records are
written after that boundary and remain visible. Conversation-subject input is
always an ordinary read.

Accepted provider-bound content is informative output, not a retained control
frame. On a capable stream, ordinary, `/say`, and `@agent` advisory text is
rendered once after cleanup as a `You → DISPLAY NAME` chat block with the exact
semantic body and no command envelope. Inline, fresh, and committed composer
consensus prompts use a `You → Consensus` chat block. These
messages appear before progress or provider work and remain through later
success, handled failure, cancellation, or post-acceptance interrupt. In
ordinary-line fallback the submitted input already remains visible, so the
terminal adds no duplicate accepted-input message. This is terminal
presentation only, not a storage, transcript-retention, provider-history, or
audit guarantee. Fallback promises complete, nonduplicated meaning, not the
same physical topology as capable output.

After workspace resolution, a capable POSIX terminal shows a bounded,
width-aware four-frame welcome before the authentication gate. Physical widths
of 48 columns and wider progressively reveal the original five-letter colored
`STORY` wordmark; 32–47 columns use the compact spaced identity, and narrower
terminals use the short identity. The capable animation has exactly three
0.4-second waits, then leaves the completed frame in scrollback. The wide
completed projection is nine rows with a 44-cell maximum logo width; compact
and narrow projections remain five rows. Sign-in guidance and its sanitized
result are each transient for 0.8 seconds.

The application treats `NO_COLOR` as color-eligible when it is unset, empty,
whitespace-only, or trimmed exact `0`; `NO_COLOR=0` therefore explicitly keeps
eligible ANSI decoration enabled. Any other value disables decorative ANSI,
without the runtime overwriting or exporting the environment. Presence of
`STORY_REDUCED_MOTION` independently selects one final colored frame when the
TTY is otherwise ANSI-eligible, with no animation, sleep, raw mode, cursor
control, or clearing. A disabling `NO_COLOR` value, non-TTY, `TERM=dumb`,
non-POSIX, callback, and uncertain-output paths render one plain final frame
without delay or ANSI. The welcome does not use the alternate screen or hide
the cursor, and it never includes provider output.
See the exact [terminal startup contract](interactive-terminal.md#startup-welcome-and-authentication).

The capable main input carries a responsive target/runtime header. With Message
details Off, it shows only the sticky agent's full display name, or the exact
one-shot target when the buffer begins with a valid `@agent` route, and performs
no execution-profile or confirmation lookup. With Message details On, the
header also shows Provider, Model, Effort, and confirmation state. For example,
the detailed view can show:

```text
[active runtime] Lead Orchestrator · Antigravity · model agy-model-id · effort high · pending
story[novel:lead]> Who is answering?

[one-shot runtime] Plot & Structure Architect · Codex · model codex-model-id · effort high · confirmed
story[novel:lead]> @plot Check the current constraints.
```

With Message details On, these runtime-owned facts, the provider argv, and the
profile records below are execution evidence. A model's reply prose or
self-identification is not.

Main prompts project canonical root, subcommand, and finite fixed-option
candidates into a selectable semantic palette. Opening highlights the first
match; Up/Down moves that highlight instead of history. Tab replaces the full
active token, adding exactly one ASCII space only when the replaced token ended
at the buffer end. Enter on a highlighted static suggestion performs the same
completion without submitting, closes the palette, and requires a later Enter
to submit. Escape restores the first pre-completion snapshot when present and
dismisses that exact state. Closed-palette Up/Down uses process-local command
history; Shift-Tab has no action.

Composition shows only the preview-only `/send` then `/cancel`, filtered by an
exact, case-sensitive whole-buffer slash prefix. Confirmation has no
suggestions. `TERM=dumb`, unavailable capabilities, and non-POSIX editing use
ordinary line input with no pre-Enter palette or cursor cleanup. Printed prompt
and input text remain visible, and no synthetic final control frame is added;
composition and confirmation pass their structured plain semantic prompt once.

The one stream terminal retains at most 100 exact nonblank main submissions for
that process. Up recalls older entries, Down recalls newer entries, and Down
past newest restores the exact pre-navigation draft and cursor. Recalls are
editable copies with the cursor at the end. Invalid commands, duplicates,
`/quit`, fallback commits, and nonempty VEOF commits are eligible; whitespace
is preserved. Empty/blank input, Ctrl-C, empty VEOF, physical EOF, failures,
composition, subjects, confirmations, and nested settings input are excluded.
The 101st entry evicts only the oldest. Nothing is persisted.

A capable live resume choice records the full explicit command after frame
cleanup and terminal restoration. In ordinary-line fallback, a submitted
omitted command is the history entry whether its fresh choice read opens a
picker, reports no eligible rows, or reports an error. When a picker opens, the
following numbered selection is history-free. Navigation, static
completion-only Enter, Escape dismissal, cancel, interrupt, EOF, and input
failure add nothing.

Main matching is exact and case-sensitive at the cursor, with ASCII space/tab
token boundaries. Root offers 21 main-available registry tokens, help offers 24
including the composition-only commands, and agent-valued positions offer the
four preferred prompt aliases—`lead`, `plot`, `character`, and `execution`—in
route order. `/override` offers `language`, `persona`, and `reset`; its persona
position offers those same four preferred agents. The older `/language`,
`/persona`, and `/reset` spellings remain parser-compatible but are not
advertised in full help or root completion. `/quit` is the sole termination
command. The parser still accepts all ten declared route aliases; runtime
values are not synthesized by the static grammar projection. Fixed-only
no-match positions show an empty open
palette, while mixed free-value slots close it. Filtering retains an eligible
highlight. The first changing Tab saves the full buffer/cursor snapshot; edits
in the same grammar position keep it, and a grammar-position change resets it.
Static completion-only Enter is not a submission or history entry; a later
submit sends the visible buffer to the parser once. The generic single-select
picker and cancellable-prompt seams remain package-internal. `/settings`
consumes both. A submitted omitted resume command uses the picker only when its
fresh lifecycle/catalog read succeeds with eligible rows; an empty set or read
failure reports the applicable result or error without a picker. The picker's
capable form is live and transient; line fallback is numbered. Exact capable
omitted resume commands also receive controller-owned pre-Enter choices through
the existing main input; dynamic-choice Enter submits an explicit identity and
bypasses the picker.

Artifact activation is a separate overlay inside provider-bound text only:

| Input | Capable-terminal result |
| --- | --- |
| `$` | Opens `Writing artifact selection [open]` with `[controls] Up/Down selects; Enter/Tab inserts a whole-file reference.` Enter or Tab inserts `${path}` without submitting. |
| `$${path}` then Enter or Tab | Opens the named artifact in the independent viewer. |
| `$$` | The second `$` immediately opens ranged-source selection before a third key or newline. Up/Down moves the highlight; Enter or Tab opens that source in the viewer. |
| Shift+Up/Down, then Enter in the viewer | Selects a normalized inclusive range and inserts `${path}#Lx-Ly`. |
| Escape, Ctrl-C, EOF, or handled activation failure | Restores the exact buffer/cursor and performs no dispatch or history append. |
| `\$` | Leaves a literal dollar and performs no activation. |

Activation requires an attached session, a focused active conversation, and a
cursor in provider-bound text. The eligible spans are focused ordinary input;
valid `@agent` and `/say` tails; `/run` prompt text; the `/run` composer; and the
internal chat composer. Lobby input, attached input without focus, management
commands and arguments, IDs, subjects, options, output paths, settings,
confirmations, and output are inert. Shell orientation and full `/help` explain
the attachment/focus prerequisite and distinguish `$` whole-file insertion from
`$$` ranged selection. Escape dismisses an immediate ranged-source panel but
leaves `$$` in the buffer; Enter or Tab reopens it in place. Typing `{` after
`$$` instead continues a direct marker, including paths with spaces, Unicode,
`{`, `$`, and `#`.
Multiple inserted indicators retain order. Completion/viewing remains local;
only the later normal submit reaches the parser and runtime, where each
provider invocation must perform its own read.

The text after `$` is a case-sensitive path fragment over the current safe
reference sources. Discovery uses the explicit workspace or the nearest
initialized ancestor selected by normal CLI resolution, returns lexical paths,
and reads metadata rather than file bytes. It includes the four supported core
documents plus every safely representable lowercase-`.md` descendant anywhere
under `manuscript/`, including direct root files, arbitrary nesting, spaces,
Unicode, `{`, `$`, and `#`. This reference-source boundary is broader than the
strict part-scoped grammar for new consensus publications. No match leaves the
labeled panel open with
`[empty] No matching writing artifacts. Change the path fragment.` When the
source list is empty, `$` immediately shows `[empty] No writing artifacts. Add
a supported canonical core document or Markdown file with a lowercase .md
suffix under manuscript/. Consensus can also publish one.` Enter or Tab then writes the existing
`command_invalid` `empty_sources` error and
restores the draft; previewing the empty state starts no provider work.

The inserted `${path}` or `${path}#Lx-Ly` is an indicator, not embedded file
content. Several indicators may be attached to one line. On later normal
submission, the runtime captures and revalidates the ordered batch and requires
every addressed agent invocation to read every indicator through the
authorized runtime before accepting its result.

Line fallback prints a numbered source list and complete numbered viewer. It
accepts exact ASCII `START-END` or `/cancel`, then prints the amended draft and
requires a second Enter before submission. Blank accepts that draft; nonblank
replacement text is reevaluated. Source-list/viewer input is history-free, and
fallback scrollback cannot be erased. Fixed empty, invalid, stale, unreadable,
and repository-unavailable failures use content-free `command_invalid` or
`workspace_invalid` output. Exact text and recovery are in the
[artifact-reference guide](../guides/artifact-references.md#discover-view-and-attach-in-the-shell).

This adds no `/compose`, standalone artifact command, public viewer API, or
noninteractive picker. `story run` scripts must quote literal `${...}` prompt
text so the invoking shell does not expand `$`.

`/auth` and `/settings` are alias-free, take no argument, and are available with
or without an attached session. `/auth` follows the authentication contract
above. `/settings` edits isolated Current and Pending snapshots for Language,
Persona, Maximum debate rounds, Conversation storage, Conversation RAG,
Message details, and Agents. A clean root shows exactly those seven choices in
that order, with no persistence action or save guidance. Dirty state appends
`Apply Changes (*Required)` and then `Discard Changes`, for nine choices.
The clean root's current rendered frame contains all seven rows; the dirty
root's current frame contains all nine rows regardless of the active choice.
This describes the complete live redraw or fallback prompt in terminal
scrollback, not a guarantee that the physical terminal height shows every row
without scrolling. Nested Settings catalogs retain a seven-row window.
Conversation storage
independently offers Filesystem and SQLite for the next shell; it is not an
agent-provider route. Conversation RAG opens a nested editor with Provider
Disabled/Local and Automatic Off/On. Disabled forces Automatic Off, selecting
Local does not enable Automatic, and On requires pending Local. Message details
offers Off and On. Off is the default compact presentation and does not delete
stored content. `Agents` opens a nested canonical-agent picker. Each canonical
agent has a responsive `Current:` or `Pending:` summary with its effective
`(Provider/model/effort)` profile. Selecting an agent opens a provider-neutral
Provider/Model/Effort picker; each field independently shows `Current:` or
`Pending:` with its effective value. An omitted effective model or
effort is displayed as `provider default`. Current/Pending confirmation
comparisons retain provider-qualified rows for both providers on capable
streams, so inactive stored values remain identifiable while the transient
comparison is visible. Ordinary-line fallback omits that comparison.

The inline agent and field comparisons are against the active runtime profile,
not a flattening of every configuration property. An edit to only an inactive
provider's stored model or effort can therefore leave the effective agent and
field summaries `Current`; the warning-semantic pending-change count beside
the top-level Agents choice still records that unsaved configuration change.
Launch-override fields remain disabled and show the effective override value.
These summaries use only already validated local Settings and backend state;
rendering them does not authenticate, discover a provider catalog, persist a
value, or invoke a story provider.

Every picker and text/number input reached through `/settings`, including a
Provider editor's subordinate authentication picker, uses the Settings form
presentation. It separates the current-view title, optional `Context:`,
guidance, choices, responsive state summaries, stacked descriptions, notices,
current value, and editable value. With an active row, a picker heading is
`Choices N/M`, using its absolute ordinal and the complete choice count;
without one, it is `Choices M`. It contains no shown-range text. `>` marks the
active row, `(selected)` marks the selected value, and unavailable choices show
`(disabled)` plus `Disabled: REASON`. The form omits
`[available]`, `[highlighted]`, `[focused]`, and `[open]`, and does not build a
breadcrumb title. Global `/auth`, omitted-resume selection, and every other
generic picker retain the standard presentation and seven-row window. ANSI
color reinforces these textual facts but never replaces them.

For state summaries, the complete picker—not only its visible seven-row
window—sets one shared column from its widest fully decorated label. Physical
cell width includes wide and combining Unicode correctly. If at least 20 cells
remain from that column to the right edge, eligible rows render
`LABEL — Current: VALUE` or `LABEL — Pending: VALUE`; wrapped summary text
continues at the same column without truncation. If only 19 cells remain, or at
any narrower width, the same summary moves to the established indented row
below its label. Live and numbered fallback views use this same boundary. A
resize can switch the layout without changing selection or values.

At the Settings root, each one-level value, including Conversation storage,
Conversation RAG, and Message details, shows one effective summary: muted
`Current` when unchanged or warning-semantic `Pending` when changed. Agents
similarly warning-marks its explicit nonzero nested pending-change count. In
Agents and Agent settings, effective `Current` summaries remain muted and
effective `Pending` summaries use warning semantics. Dirty confirmation
context on capable streams keeps Current neutral and Pending warning. These
roles reinforce the visible words and counts. The summary opt-in covers only
those root facts, a nonzero root Agents count, nested Conversation RAG
Provider/Automatic, the Agents roster profile, and Agent settings
Provider/Model/Effort. Clean Agents has no summary. Persona paths, help and
custom/inherit guidance, Discard guidance, authentication text, and disabled
reasons remain stacked descriptions. Root and editor views retain complete
textual values under narrow, plain, fallback, and `NO_COLOR` output, so color
is never required to identify state; fallback confirmation context is omitted
as described below.

Every retained Language, Persona, Maximum debate rounds, Conversation storage,
Conversation RAG Provider, Conversation RAG Automatic, Message details,
Provider, Model, Custom model, and Effort editor always shows `Entered changes
remain pending in memory and are not saved; choose Apply Changes in Settings to
persist them.` The text is present on initial entry, after a valid Enter reopens
the editor, and during validation. The established entered-value success result
remains unchanged and is still the only inline completed result.
Maximum debate rounds and Custom model render the view name and optional
`Context:` first, guidance next, validation or completed context when present,
then `Current value: VALUE` and the final `New value > ` input. The initial
editable value, exact validation, retained-commit behavior, and fallback input
semantics are unchanged.

The dirty top-level Settings picker shows `[save required] Choose Apply Changes to persist
pending changes` if and only if the
complete pending configuration differs structurally from its opening baseline.
This is recomputed on every top-level read, so the warning is absent initially
and after a complete revert. It is also absent from Agents, Agent settings,
value editors, and Apply/Discard confirmations. Capable, narrow, `NO_COLOR`, and
fallback views expose the complete text. Fallback cannot erase historical
scrollback, but the current prompt reflects the current comparison.

When dirty, the root appends exact plain `Apply Changes (*Required)` followed by
`Discard Changes`. The Apply warning suffix is exactly `(*Required)`; only that
suffix receives eligible warning styling, while the label and preceding space
remain neutral. Discard has the exact description `Pending changes will be
discarded.` Clean state renders neither action. Plain, `NO_COLOR`, narrow, and
fallback views preserve the same labels, order, description, and guidance.

Only entering Model or Effort runs discovery. Each entry uses the pending
effective provider; Effort also uses the pending effective model. Model order
is Inherit, provider-order discovered models, Current custom when the pending
value is absent, then Custom model. Effort order is Inherit, exact selected-
model Codex efforts or Antigravity's global `low`, `medium`, `high`, then
Current compatibility when a pending effort is unreported.

Codex runs `codex debug models --bundled` and projects list-visible rows with
exact per-model supported/default reasoning. Antigravity runs `agy models` or
the resolved executable's `models` subcommand, preserves aligned opaque
slug/display order, and never infers effort from model text. Each fresh attempt
is limited to five seconds and one MiB of retained stdout; stderr/raw
diagnostics are discarded, with no automatic retry, cache, or catalog
persistence. Fixed failure guidance preserves inherit/current/custom paths.
Only the chosen opaque model/effort value is saved.

Settings pickers and prompts follow the same transient input
lifecycle as all capable input; fallback readers retain printed input and add no
synthetic final picker frame. Nested Escape returns to
the parent silently without a Current/Pending snapshot or cancellation record.
In Language, Persona, Maximum debate rounds, Conversation storage,
Conversation RAG Provider/Automatic, Message details, Provider, Model, Custom
model, and Effort, the first valid commit applies the normalized pending value
and keeps the editor active. The next valid commit of that same normalized
value returns one level silently; a different valid value applies, stays
active, and re-arms the comparison. Invalid input, authentication failure,
Custom model navigation, and editor re-entry reset it. This is consecutive
semantic commit policy in the `/settings` controller, not raw-key timing or
generic Picker/Prompt behavior.

Apply Changes alone opens the dirty transient yes/no persistence confirmation.
Discard Changes opens the existing dirty discard confirmation. Root Settings
Escape retains the same cancellation action: clean settings exit immediately
without confirmation, while dirty settings open the discard confirmation. Each
dirty confirmation has changed-only Current/Pending context on capable
streams: only differing fields appear in both sections, each with a `(Changed)`
cue. That comparison, the confirmation picker, and its activation notice are
one live FORM frame rather than separate durable output. The activation notice
joins only the applicable exact sentences:
`Runtime changes apply to this shell after Apply Changes.`, `Desired storage
changes apply to the next shell.`, and `Conversation RAG changes apply to the
next shell; this running shell retains its current RAG service and automatic
policy.`
The driver clears the whole capable frame before YES, NO, confirmation Escape,
Ctrl-C, or EOF returns, and attempts the same cleanup before an input or render
failure propagates. The controller does not write a separate Current/Pending
panel, so returning to Settings or the shell leaves no redundant comparison.
Ordinary-line fallback, including `TERM=dumb`, omits both the comparison and
activation notice—even on an invalid-answer retry—because printed fallback
content cannot be erased. Its YES/NO choices and action outcomes are unchanged.
The existing `No pending settings changes.` confirmation panel remains a
defensive internal projection and is not selectable clean-root UX. YES
commits the requested dirty action; NO or confirmation Escape returns to the
root Settings picker, and invalid fallback input retries. Ctrl-C or EOF still
cancels the whole workflow without writing. The
submitted `/settings` line has already been cleared with the capable main frame;
its history entry remains. Ordinary-line fallback leaves its printed input
visible, with the same history semantics. `/cancel` is literal model text in the
Custom model prompt.

Choosing a provider starts authentication before changing that agent's pending
route. A failed or cancelled authentication leaves the route candidate
unchanged. Successful authentication is subordinate and returns to Provider;
the route and any missing workspace default remain pending in memory. Escape or
cancellation before confirmed Apply Changes leaves configuration bytes
unchanged. No configuration value is persisted before Apply Changes is
confirmed.

After Apply Changes confirmation, the controller rechecks that pending still
differs from the opening baseline. A clean final recheck returns to the Settings
root without authentication, persistence, output, exit, or a saved event.

When `show_message_details` is the sole difference in that exact immutable
candidate, the controller prepares and verifies the configuration, commits it,
and updates the active presentation flag only after commit. This fast path does
not authenticate, stage or activate a backend/runtime, check storage readiness,
switch routes or generations, or perform provider/thread work. If only the two
Conversation RAG values differ, the controller prepares, verifies, and commits
the system configuration without authentication, storage selection or
readiness, backend/runtime prepare, commit, discard, or activation, route/thread
work, composition, optional-package probing or installation, provider catalog,
subprocess, socket, or network work. The running RAG service and automatic
policy remain unchanged. If any other field differs, including a mixed Message
details or RAG change, the controller uses the full path: it freshly gates each
distinct effective provider once before
entering the configuration transaction. A stale,
unavailable, unauthenticated, indeterminate, failed, cancelled, or interrupted
status prevents the settings file transaction. A missing workspace default
learned during this gate remains in the pending candidate. The controller then
stages and preflights the candidate adapters, prepares runtime quiescence, and
validates the prepared configuration and route state. It next runs
conversation-storage readiness, optional explicit installer confirmation,
create-only provisioning, and fresh parity, while rejecting any exact-byte
change to either configuration document, the optional provider-route generation
record, or the persona catalog since opening. A host prerequisite, empty SQLite
database, or lazy storage guard created during readiness may remain after a
later failure, but Apply Changes never migrates or activates conversation data.

The prepared persistence boundary contains
`config/system_defaults.json`, `config/agent_configs.json`, and, when needed,
`agent_states/orchestrator_state/provider_route_generations.json`. Changed
targets are written in that order; a write failure attempts exact reverse-order
restoration of every attempted target, including the failing target. After
persistence on the full path, the controller commits the staged backend/runtime
generation.
Safe compensation restores the exact prior configuration and route files and
leaves the old runtime active; if restoration or activation cannot be proved,
the shell reports `recovery_required` and must not be used until restarted
against the persisted state.

A live-capable Apply, including Message-details-only, returns to the main prompt
with `Settings saved and applied to this running shell now.` A RAG-only Apply
instead reports `Conversation RAG settings were saved for the next shell. This
running shell retains its current Conversation RAG service and automatic
policy.` A storage change reports `Restart is required for sqlite.` when
selecting SQLite (`filesystem` appears on the reverse change). Mixed changes
emit only the applicable live, RAG-next-shell, and storage-restart notices. The
current controller/runtime storage and RAG bundles never change.

When effective profiles changed, the completed Settings panel adds one compact
`[profiles changed]` summary with the number of changed agent profiles. It does
not repeat agent names, property values, Before/Active sections, unchanged
roster status, pending-confirmation detail, or same-provider reuse eligibility.
For one provider switch, the summary includes `The provider switch starts a
fresh thread with story-session context on the next turn.` For multiple
switches it includes `Provider switches start fresh threads with story-session
context on the next turn.` Use `/agents` for active values and confirmation;
use `/status` for thread and source details. Changed profiles remain `pending`
until their first complete logical provider call succeeds. A provider switch
still requires a fresh target-provider thread, and a same-provider model/effort
change remains eligible for native-thread reuse even though the completion
panel does not list that eligibility.
A successfully persisted dirty snapshot, and activated snapshot where
applicable, emits those success notices and returns to the main prompt.
Cancellation, interruption,
authentication or storage-readiness gating, declined installation,
provisioning or parity failure, a clean final recheck, validation conflicts,
write or activation failure, compensation, and rollback outcomes emit no false
success. Apply Changes invokes no story-model turn, does not alter session or
launch overrides, and stores no credentials or provider catalog data.

Conversation RAG editing itself performs no optional-package probe, import,
installation, network request, or credential prompt. At the next shell, the
controller reads the backend construction snapshot once. Disabled skips local
composition without writing. Local composes exactly once with the persisted
automatic policy when the complete conversation-storage bundle is available.
A missing or incompatible pinned extra leaves configuration unchanged, emits
one fixed warning, and keeps a RAG-free shell and `/settings` usable. Reinstall
the reviewed dependency with `python -m pip install --editable '.[rag]'`, then
restart; startup never installs it. The current shell never adopts a setting
saved after construction.

Entering or validating a value remains in-memory only; no configuration value
is persisted before confirmed Apply Changes. This does not change startup or
global `/auth` default persistence. It adds no terminal primitive, public API,
or global picker rule; the #270 one-level Escape, submitted-input retention,
generic cancellation, YES/NO confirmation, and closing session-resume picker
contracts remain unchanged. Stable internal save/cancel identities and the
defensive clean route are controller details, not selectable Settings actions
or a global navigation rule.

On capable real-CLI streams, orientation/current context, `/status`, `/inspect`,
central errors/recovery, and accepted management, advisory, retrieval, and
consensus output use responsive semantic layout with fixed decorative ANSI.
Full/context help, the agent roster, session/conversation catalogs, search,
transcript, and focused history use that layout too. `NO_COLOR` removes only
decoration and leaves the same structured plain text; narrow output stacks and
wraps labels without dropping facts. Composition and confirmation now use
ordered semantic input views, and composer entry/error/send/discard records use
semantic notices; no internal generic composer-status site remains. These
presentation changes do not alter command grammar, established callback/read
order, or noninteractive success stdout, JSON, ordering, or exit contracts. The
documented availability line is the sole additive noninteractive stderr
contract for a caught exception after CLI workspace initialization or discovery
succeeds, independently of whether a diagnostic handle or file is available.

Generic semantic `Message` output retains its inline layout. User and agent
messages place the speaker header directly above the default-styled `│ ` body
rail and own one unrailed trailing separator; only the header receives a stable
speaker accent. Adjacent message owners therefore have exactly one visible blank
row between them, while explicit blank body rows remain railed. Physical-cell
wrapping preserves whitespace, strict-JSON presentation, and CJK/full-width
content, with a row-local rail fallback when a narrow row cannot fit its leading
glyph.
Message details Off is the compact default and never removes stored content. A
direct agent reply still shows its speaker and full body; On adds Provider,
Model, and Effort with each source, then Confirmation and Thread. The accepted
user message has no diagnostic metadata. The main runtime header similarly
shows only the target display name when Off and the full execution profile when
On. `/agents` and `/status` are diagnostic commands and remain fully detailed in
both modes.

After attachment, `Conversation scope` appears before the first prompt and is
then repeated only when its session ID, visible conversation handle, subject,
or sticky agent changes. An unchanged panel is suppressed, but the controller
still revalidates any current focus against a fresh complete-catalog read before
every attached prompt. External rename, archive, deletion, and prefix-collision
changes are therefore detected at the same boundary. Detaching resets only
this presentation snapshot, so a later attachment presents scope again. This
controller-local cadence changes no command grammar, terminal API, persisted
selection, conversation storage, or capable/fallback semantics.

Before local result work, accepted request context keeps output interpretable.
`/search` shows its normalized query and active scope; a filtered
`/conversation list` shows its normalized query and active/all scope, while an
unfiltered list adds no request record. After transcript identity resolution,
`/transcript` shows the visible conversation handle and effective start/count
before reading the page. These safe semantic records remain visible when later
work returns empty, fails safely, or is cancelled; fallback may therefore show
both its original command and the normalized result context without repeating
the same verbatim snapshot.

During interactive startup, fresh authentication probing, omitted session or
conversation resume preparation, one advisory turn, or while `/settings`
prepares Provider, Model, or Effort options, a capable raw stream may show the
terminal-owned loading row.
It is a text-free sixteen-cell block-shade shimmer that travels out and back.
ANSI terminals cycle a fixed bold color palette as it moves. The driver clears
the complete row before each
semantic/progress/result write, redraws while the operation is
pending, and clears and joins both operation and indicator tasks before the
final record, refreshed authentication view, resume picker, empty/error result,
or completed Settings picker. `NO_COLOR` keeps the same spatial motion without
SGR. Advisory turns emit no `[routing]` or
`[waiting]` notice. Callback construction and non-TTY, `TERM=dumb`, non-POSIX,
missing, or uncertain-capability paths await the operation silently; the installed CLI's
TTY guard still rejects non-TTY shell entry before this point. An underlying
operation failure takes precedence over a simultaneous indicator failure;
indicator-only failures use the existing internal-error path after cleanup.

`/help [COMMAND]` (`/?`), `/status`, `/auth`, `/settings`, and `/session list` are global.
Session grammar is `/session list | create NAME | resume [NAME] | pause`. Explicit
`story chat SESSION` creates an absent session or resumes a persisted session.
Bare entry resumes a ready default and asks for confirmation before resuming a
paused default.

On a capable main prompt, exact omitted `/session resume` displays all persisted
sessions before submission, ordered by newest durable update then ID. Rows show
session ID plus durable and process-access state. Current attachment is
highlighted first, otherwise the saved default, otherwise the first row.
Up/Down previews, Tab is a no-op, and Enter submits the highlighted identity as
one complete explicit resume command. Escape only dismisses the rows and leaves
the omitted command in the buffer. If that omitted command is submitted instead,
including after Escape or through nonempty VEOF, the controller rereads the
lifecycle list. A successful nonempty read opens a history-free picker, live on
a capable stream; ordinary-line fallback shows the numbered form and accepts
blank for the highlight, an in-range ASCII decimal ordinal, or exact `/cancel`.

Picker cancellation or interrupt and rejection before acquisition are
mutation-free; EOF performs normal shell cleanup without selecting. An empty
list emits `[session] No story sessions found.` without opening the picker; a
lifecycle-read failure reports the applicable error without opening it.
Choosing the current attachment reports that it is already attached. Every
other choice is freshly
validated before acquisition or durable reopen. Later steps load destination
focus, persist the destination default, release the source when switching, and
only then commit in-memory scope. A successful paused-to-ready reopen is not
rolled back by later failure; newly acquired access is released where possible,
but durable lifecycle/default rollback is not promised. A reported default
write failure may follow atomic replacement, and source-release failure after
default persistence is fatal `cleanup_failed` with the new default retained.
Inspect persisted state after either error. Explicit
`/session resume NAME`, startup default handling, `/session list`, and standalone
`story session reopen` remain direct and unchanged.

Conversation selection grammar is exactly `/conversation resume [ID]`. With
`ID`, it directly resolves the established exact full ID or accepted
case-sensitive unique prefix of at least 12 characters across the complete
session catalog, then rejects an archived result. With `ID` omitted, capable
main input displays active conversations before submission in catalog order.
Rows use subject
labels and show a stable handle: the first 12 ID characters when unique across
the complete catalog, otherwise the full ID.
Current active focus is highlighted first, then an active saved default, then
the first row. Capable Up/Down previews, Tab does nothing, Enter submits the full
immutable ID through one explicit command, and Escape only dismisses the rows.
Submitting the omitted command instead performs a fresh catalog read and, when
needed for highlighting, a saved-default read. A successful read with active
rows opens the generic picker: live and transient on a capable stream, or
numbered blank/ordinal/`/cancel` input in fallback. Cancel and interrupt are
mutation-free; EOF performs normal shell cleanup without selecting. No active
rows emits
`[conversation] No active conversations found.` without opening the picker;
a catalog or saved-default read failure reports the applicable error without
opening it.

The committed full ID is freshly resolved against the complete catalog before
selection. A now-missing or archived choice is rejected, refreshed metadata is
used, and the saved default is committed before current focus changes. A
reported save failure leaves in-memory focus unchanged but may follow atomic
replacement of the persisted default; inspect the saved selection. The
existing explicit-ID path, `/conversation list` active-only default and
`--all`, startup restoration, agent routing, noninteractive behavior, schemas,
and package/public terminal APIs are unchanged.

<a id="agent-exchange-inspection"></a>

### Agent-exchange inspection

`/exchanges` is alias-free, accepts no argument, and requires the currently
attached session but no conversation focus. It invokes no provider, creates or
resumes no session, and does not change session, conversation, debate, or agent
focus.

The command reads the attached session's bounded authoritative exchange
inventory. An empty inventory reports
`No agent exchanges are recorded for session SESSION.` without opening a
picker. Otherwise it opens two history-free transient pickers:

1. one separately correlated Conversation or Debate scope, ordered by its
   newest retained workspace sequence and labeled with exact kind, full ID,
   subject, and aggregate record count; then
2. one of all four canonical agents in roster order, labeled with full display
   name, canonical ID, and a retained count or `No recorded exchanges`.

Conversation subjects include archived catalog entries. Debate display subjects
come only from the first exact canonical initiation and use the fixed
`[subject unavailable]` fallback when it cannot be proven. Cancel, interrupt,
or EOF at either picker selects nothing and opens no view. An agent without
retained records opens an immutable empty snapshot with the selected header.

For a nonempty selection, the controller copies one exact session/scope/agent
snapshot into immutable bytes, validates the optional package's exact
`ViewerSpec`, `ViewerHost`, and `view_jsonl` API before terminal acquisition,
then opens it through Story's sole raw-terminal driver. The header contains
session, exact Conversation or Debate ID and subject, and agent ID. The external
viewer supplies read-only Simple/Verbose JSON, source-line gutters, exact
go-to-line, structured color, bounded string previews, enumerated-field search,
`n`/`N`, transient result position, help, and complete state disposal on close.

If `jsonl-viewer` is absent, the command reports `configuration_invalid` and
guides installation of `story-writing-agents[viewer]`. A mismatched public API
reports the same category with reinstall guidance. Fixed content-free errors
cover busy, unsupported, unsafe/corrupt, uncorrelatable, disappeared, or failed
views; eligible failures add only the normal sanitized runtime-debug hint.
There is no replay, retry, live tail, edit, export, raw path, or external error
prose. See [Inspect agent exchanges](../guides/agent-exchanges.md) for the exact
install, picker, viewer, privacy, and recovery workflow.

Full help is one grouped semantic `Panel` in command-registry order; contextual
help is one command record with its group, syntax, aliases, provider/artifact
effects, and example. `/agents` is one canonical roster list. Every agent row
shows `ID`, `Aliases`, `Advisory domain`, an `Execution` composite with
provider/model/effort and each source, and `Confirmation`. The compact value
shape is `PROVIDER [SOURCE] · model VALUE [SOURCE] · effort VALUE [SOURCE]`.
Session and conversation catalogs and search hits use `Record` rows where
applicable; transcript events and focused history use attributed `Message` rows.
Each emits one positioned `ListView` per established row callback. Empty states
are explicit. If Ctrl-C interrupts search, transcript, or history after
some rows, those completed rows remain, one cancellation record follows, and
no trailing result, hint, or history-context record is emitted.

Transcript and history message rows always retain their position, Sequence,
human direction header (`You → Agent`, `Agent → You`, or `Runtime → You`), and
full body. Message details On additionally shows canonical sender/recipient IDs,
Turn, and Kind. Retrieval sources always show Subject and Conversation;
event-level sources always show Sequence and add Turn and Kind only when On.
Conversation-level sources have no Sequence because they have no event
citation. Retrieval reply execution-profile metadata also appears only when On.

`/status` emits one read-only semantic lifecycle record with fields in this
exact order:

```text
Session | Durable state | Process access | Default session | Current attachment
Default conversation | Current focus | Agent focus | Provider readiness | Next action
```

It then emits four execution-profile records in canonical roster order. Each
uses this exact field order:

```text
Provider | Provider source | Model | Model source | Effort | Effort source
Confirmation | Thread | Route generation
```

Sources are `launch override`, `agent config`, `system default`,
`built in default`, or `provider default`. Thread values are `not observed`,
`fresh pending`, `reusable pending`, `fresh`, `reused`, or
`fresh after provider switch`. `provider default` means Story omitted that
model/effort flag; it is not a claim about the vendor's hidden server-side
selection.

`/session list` emits one row per persisted session, newest update then ID:

```text
Session | Durable state | Process access | Default session
Provider readiness | Next action
```

Neither command acquires access or displays owner hostname, PID, or claim token.
A default is not necessarily the current attachment; a default conversation is
not necessarily current focus. Readiness requires valid configuration, `ready`,
and `held_here`.

Conversation management, local reads, directives, and inspection need current
attachment. `/exchanges` is one such inspection but needs no current focus.
Advisory turns, `/history`, `/run`, and `/draft-file` also need current focus on
an active conversation. Search is active-only; transcript reads can include
archives. See [grammar](interactive-command-parser.md) and [lifecycle
mapping](../design/session-lifecycle.md).

`/rag QUERY` is alias-free and requires attachment plus an active current
focus. Its query is nonblank, control/format-free, and at most 200 codepoints.
The command performs no retrieval and emits no query text; it replaces one
process-local pending query for that exact session/conversation. The next
ordinary, `/say`, or routed `@agent` turn consumes it and forces one preflight,
overriding automatic policy only for that turn. Ineligible commands retain it;
scope change, pause/detach, quit, or cleanup clears it. Cancellation before the
runtime coroutine enters retains it, while entry consumes it even if the turn
then fails or is cancelled. The query is not provider-bound text, so artifact
activation is inert there; put `${path}` or `${path}#Lx-Ly` references in the
following eligible story message. Literal `/search` remains a separate
immediate local read.

<a id="interactive-consensus"></a>

## Interactive consensus and cancellation

| Syntax | Context behavior |
| --- | --- |
| `/run --output PART/PATH.md PROMPT` or `/run --output PART/PATH.md` then `/send` | Bounded owner/core belonging-session facts plus one focused transcript snapshot. |
| `/run --fresh --output PART/PATH.md PROMPT` | Retains bounded owner/core facts and focused identity; sends no focused transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft plus one focused snapshot; prompts for `PART/PATH.md`. |

After prompt and path validation, inline, fresh, and committed composer runs
write one exact-body `You → Consensus` chat block before the `Consensus request`
record and before progress. Every request record names the validated `Output`.
`/draft-file` instead adds only its validated `Source` path: loaded draft bytes
are never echoed as a terminal message. Prompt messages and request context
remain visible through later debate success, deadlock, handled failure,
cancellation, or post-acceptance interrupt. Ordinary-line fallback retains its
already printed prompt text and receives no duplicate message; the distinct
output/source context is still written. None of this changes `story run` or
other noninteractive output.

Each accepted progress event is one atomic `Debate phase · DISPLAY_NAME` Panel.
The trusted sentence-case title names the actual phase. Active headings omit
`[loading]`; successful completion is silent, while error or cancelled state
remains visible as applicable. The canonical Phase field and the Outcome
field's existing role/state are unchanged. Every panel shows Debate ID, Phase, Round, Revision,
Action, Outcome, and Conflicts; Message details On inserts Handoff after
Revision. Newly readable durable speech is grouped into contiguous persisted
phase/round/revision sections within that same panel and shown in full except
for fixed safe replacement of error-kind content. On adds each speech's Kind. A
debate-log read warning remains inside the panel, and a failed panel write
advances no cursor or progress cache. Successful consensus receives a separate
final `Consensus` Record after terminal progress; deadlock and failed outcomes
instead use typed error presentation. This interactive presentation does not
change `story run` JSON or exit behavior.

In both `/run --output ...` and `/run --fresh --output ...`, option boundaries
use ASCII space or tab only. Unicode whitespace cannot separate `--output` from
its path or the path from its prompt. Attached to the flag it causes a parse
error; captured in the path token it is not silently normalized and fails path
validation. Both fail before provider or artifact work.

After leading `--fresh`, the only accepted second leading long option is exact
`--output`. Another `--...` token fails parsing; a single-hyphen word or
ordinary text begins the prompt, and later `--...` text remains literal prompt
content.

When interactive `--output` is omitted, the controller first prompts for a safe
create-only `PART/PATH.md` beneath `manuscript/`; invalid input retries, cancel
returns to the main prompt, and EOF exits normally. The noninteractive
`--output` option is required by argument parsing. Runs revalidate scope and may
write artifacts, never transcripts. Main Ctrl-C
takes no action; composer Ctrl-C or `/cancel` discards its buffer; EOF cleans up.
Missing cancellation proof is fatal. Exact terminal records are in the
[terminal reference](interactive-terminal.md#exact-cancellation-records).

Each composition attempt shows `Mode`, `Next line`, `Lines`, `Characters`,
guidance, then the focused numbered prompt. The `/send`, `/cancel`
`SuggestionPalette` is a preview, not completion or dispatch. `/say` is not
suggested; `/say /send` and
`/say /cancel` remain literal escapes. Typing, editing, or clearing the preview
has no provider, runtime, filesystem, or storage effect. Up/Down, Tab,
Shift-Tab, and Escape are no-ops in composition and confirmation; those routes
remain history-free.

## Workspace, noninteractive behavior, and exits

Except `init`/`smoke`, commands need the nearest initialized workspace. For
`directive`, `run`, and `inspect`, explicit `--session` wins over the default.
`story run` receives no conversation focus and does not reopen a paused session.
It requires `--output PART/PATH.md`; it never generates an output name, and a
single-component filename is invalid. Exact path and provenance rules are in
[Canonical writing artifacts](writing-artifacts.md).

Exits: `0` success or normal shell exit; `1` failed smoke invariant; `2`
usage/setup/fatal shell; `3` noninteractive deadlock; `4` noninteractive
failure. See [Consensus runs](../guides/consensus-runs.md) and
[Terminal safety](../operations/terminal-safety.md).
