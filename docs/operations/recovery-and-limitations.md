# Recovery and limitations

> **Available:** guarded access recovery, shell workflows, and process-level
> POSIX PTY verification. Automatic retry, destructive cleanup, event repair,
> and Windows console emulation are not available.

Key terms: **fail closed** means preserve uncertain state; **taint** means stop
continuing incomplete provider history; **external backup** is outside the workspace.

## Session access recovery

| Process-access result | Meaning | Safe response |
| --- | --- | --- |
| `claimable` | No claim, or a local POSIX PID is proved missing. | Open normally; guarded acquisition replaces an unchanged dead-local claim. |
| `held_here` | This backend holds the exact token. | Continue, pause, switch, quit, or close normally. |
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

## Controller cancellation proof

One `asyncio.Runner` serves provider work. SIGINT recovery requires
`KeyboardInterrupt` plus proof: pre-entry must be quiescent; entered work needs
`CancelledError` and a completed join. Otherwise `operation_interrupted` is fatal.

Consensus Ctrl-C after entry also requires durable `INTERRUPTED`; retry uses a
new debate ID. Missing proof abandons attachment bookkeeping and exits `2`.
Cancelled local reads keep completed rows without a second domain read/write;
their command/cancellation boundary may append bounded diagnostics.

## POSIX PTY boundary

POSIX tests use a controlling PTY and VINTR/VEOF. They prove line fallback,
suggestion editing, clearing, and restoration. Windows consoles remain unverified.

## Pause, quit, and cleanup

Declined or interrupted pause confirmation keeps the current attachment; EOF at
confirmation exits. Confirmed pause writes durable `paused`, releases access,
retains defaults, clears current attachment/current focus/agent focus, and
returns to the lobby. Quit and main/composer EOF release access without pausing.

Cleanup attempts Runner close → access release → backend close, clears process
scope, and reports at most one `cleanup_failed`. Later startup opens a ready
default or asks to reopen paused; only an active default can become focus.

## Current limits

- Codex is the only provider; reserved `gemini` fails validation.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact rollback is best effort across files, not one transaction.
- Advisory input is one line; bare `/run` owns multiline composition.
- Conversation search is active-only; transcript reads retain no cursor.
- Standalone compose/artifact commands are unavailable.

See [Session lifecycle](../design/session-lifecycle.md),
[Troubleshooting](../guides/troubleshooting.md), and
[Filesystem safety](filesystem-safety.md).
