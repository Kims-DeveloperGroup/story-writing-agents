# Recovery and limitations

> **Available:** guarded access recovery, shell workflows, and process-level
> POSIX PTY verification. Automatic retry, destructive cleanup, event repair,
> and Windows console emulation are not available.

Key terms: **fail closed** means preserve uncertain state; **taint** means stop
continuing incomplete provider history; **external backup** is outside the workspace.

## Session access recovery

| Process-access result | Meaning | Safe response |
| --- | --- | --- |
| `claimable` | No blocking claim: no claim exists, or an unchanged local POSIX claim is proved dead. Guarded acquisition may be attempted. | Inspect durable state: create when absent; resume when ready or paused. Acquisition rechecks the claim under the guard. |
| `held_here` | This backend owns the exact claim token. It does not by itself prove provider work is allowed. | Provider work also needs durable `ready`, valid configuration, and any required conversation focus. Otherwise resume paused state, repair scope/configuration, or close normally. |
| `blocked_by_live_local_owner` | Another process on this host is alive. | Close that process normally, then retry. |
| `ownership_unknown` | Host is remote, platform/probe is unsupported, permission is denied, or state changed. | Preserve the claim; verify ownership outside the runtime. |

Status/list only classify. Age is not liveness proof. Do not edit a claim or
`.access-claims.lock`. Release compares the exact token under the guard and
preserves foreign/changed ownership. No remote probe occurs.

A same-host missing PID needs no deletion: acquisition replaces its unchanged
claim under the guard. If ownership is unknown, back up and investigate before
retrying. Never manufacture `claimable` by removing files.

<a id="unsupported-v1-session-data"></a>

## Unsupported V1 session data

V1 provider manifests, `<session_id>.lease.json`, `.lease-claims.lock`, V1
`cli_context.json`, and `conversation_selections/` are rejected unchanged with
`WorkspaceVersionUnsupportedError`. There is no migration or compatibility mode.

Use this nondestructive reset:

1. Stop every process using the workspace.
2. Copy the complete workspace to an external backup and verify the copy.
3. Prefer a fresh workspace. Otherwise move—never delete—the old
   `provider_sessions/`, `cli_context.json`, and `conversation_selections/`
   outside the active workspace.
4. Run `story init WORKSPACE` to recreate current scaffold directories.
5. Recreate sessions with `story session create SESSION`, then recreate needed
   conversation defaults through the shell.

Do not copy old provider state, access files, guards, defaults, or CLI context
into V2. Copy editable story/configuration files only after review; keep backup.

## Debate and shell-run recovery

Debate IDs reserve before providers and cannot resume; debate `active` is not
liveness. Inspect, fix, and use a new ID. Context sidecars do not enable replay.

Workspace failure returns safely only when the same session remains `ready` and
`held_here`; failed proof clears scope and exits. `ProgressObserverError` marks
preterminal debates `interrupted` when possible; later failures cannot rewrite results.

## Writing-artifact recovery

Every artifact catalog read, exact artifact read, or commit first checks for a
pending publication journal under the verified paired-lock lease. A valid
`prepared` journal means target publication may not have started or may be
partial; a valid `artifacts_published` journal means all targets were verified
and only repository metadata may remain. Both roll forward: each target must
still equal its recorded old or new hash, then immutable commit history and the
catalog are published last. The completed journal is hash-checked and moved with
native no-replace rename to a `.completed` name in staging. If the journal name
or identity changed, the repository preserves it and blocks instead of retiring
uncertain state. Users may see an infrastructure failure followed by successful
recovery on the next artifact access; inspect the associated debate because its
result and lifecycle are outside the artifact transaction.

Publication temporaries live under the repository's separate `staging/`
directory rather than `transactions/`. Failed `.tmp` bytes, prior `.held`
bytes, failed published `.retired` bytes, and finished `.completed` journals are
all retained there. This residue is normal internal state, is ignored and
preserved by transaction recovery and catalog reads, and may grow. Only exact
JSON journal names in `transactions/` are authoritative; any unexpected entry
there is ambiguity and blocks recovery.

If a journal is invalid or a target diverged, the repository writes
`agent_states/orchestrator_state/writing_artifacts/recovery_blocked.json` and
all later artifact access fails with `writing-artifact recovery is blocked`.
Stop workspace processes, preserve a complete backup, and investigate the
journal and target history. Do not delete or edit the lock, journal, catalog,
commit records, or block marker to force recovery; no automatic repair command
exists. Restore a verified backup when trustworthy reconstruction is not
possible. See [Canonical writing artifacts](../reference/writing-artifacts.md).

Catalog listing and selective prompt-context reads omit an individual missing,
unsafe, hard-linked, nonregular, or hash-stale artifact. They do not hide a
workspace-root, ancestor, lock-lease, catalog-schema, or global provenance
integrity failure; those errors propagate because safe enumeration is no longer
possible. A manual byte edit is therefore nondestructive but makes that entry
ineligible. Restoring the exact cataloged bytes can make it eligible again when
the commit and catalog evidence remain valid.

Stop every workspace process before a manual or external filesystem edit. The
repository lease serializes supported repository writers, but an actively
hostile same-user process mutating live names during the native no-replace call
is outside the supported boundary. Darwin and Linux hosts without the required
native atomic no-replace rename fail on their first artifact operation; other
non-artifact workspace/runtime APIs remain usable.

## Controller cancellation proof

One `asyncio.Runner` serves provider work. SIGINT recovery requires
`KeyboardInterrupt` plus proof: pre-entry must be quiescent; entered work needs
`CancelledError` and a completed join. Otherwise `operation_interrupted` is fatal.

Consensus Ctrl-C after entry also requires durable `INTERRUPTED`; retry uses a
new debate ID. Missing proof abandons attachment bookkeeping and exits `2`.
Cancelled local reads keep completed rows without a second read/write.

## POSIX PTY boundary

POSIX tests use a controlling PTY and VINTR/VEOF. They prove line fallback,
static and live-resume suggestion editing, complete capable-frame clearing, and
restoration across terminal outcomes. Windows consoles remain unverified.

## Pause, quit, and cleanup

Declined or interrupted pause confirmation keeps the current attachment; EOF at
confirmation exits. Confirmed pause writes durable `paused`, releases access,
retains defaults, clears current attachment/current focus/agent focus, and
returns to the lobby. Quit and main/composer EOF release access without pausing.

Cleanup attempts Runner close → access release → backend close, clears process
scope, and reports at most one `cleanup_failed`. Later startup resumes a ready
default or asks for confirmation before resuming a paused default; only an
active default can become focus.

## Current limits

- Codex is the only provider; reserved `gemini` fails validation.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact publication is recoverable and catalog-last, but does not share one
  transaction with debate logs, lifecycle, results, or provider state.
- Advisory input is one line; `/run --output PART/PATH.md` without prompt text
  owns multiline composition. Bare `/run` first prompts for the output.
- Conversation search is active-only; transcript reads retain no cursor.
- Standalone compose/artifact commands and artifact-reference UI are unavailable.

See [Session lifecycle](../design/session-lifecycle.md),
[Troubleshooting](../guides/troubleshooting.md), and
[Filesystem safety](filesystem-safety.md).
