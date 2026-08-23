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

<a id="authentication-recovery"></a>

## Authentication recovery

Only a fresh native status probe authorizes a provider. Never infer success
from a completed login process, a previous picker row, a configured route, or a
provider session manifest.

| State or outcome | Meaning | Recovery |
| --- | --- | --- |
| `unavailable` | The provider executable could not be launched and its picker choice is disabled. | Install the Codex CLI or Antigravity `agy`, correct the executable path or `PATH` in the environment that launches `story`, then retry startup or `/auth`. |
| `unauthenticated` | The exact probe returned the native unauthenticated code. | Select the provider to run `codex login` or bare `agy`, complete the native prompt, and wait for a fresh probe. |
| `indeterminate` | Launch, wait, timeout, or return status did not prove either supported state. | Check `codex login status` or `agy models` outside the runtime, repair the native CLI or environment, then retry. Selection itself performs another probe and never authorizes optimistically. |
| Login fails or returns without authentication | The following fresh probe still is not authenticated. | Read the native CLI's own terminal guidance, repair outside the runtime, and select it again. The runtime has no captured provider output or URL to recover. |
| Startup picker Cancel | No provider was chosen. | The hard gate remains open; choose an enabled provider. Ctrl-C or EOF exits normally before context/backend/controller/session construction. |
| `/auth` picker Cancel, interrupt, or EOF | The picker ended without a new authorization. | The shell continues, explicit routes stay unchanged, and any already persisted default remains. Run `/auth` again when ready. |
| Ctrl-C during native login | The inherited provider process and runtime received the interrupt. | The runtime best-effort stops and reaps that child. Startup exits normally; an already running shell follows its fatal interruption cleanup. Retry after checking native state. |
| Settings provider authentication is cancelled or fails | Fresh authorization was not proved before assignment. | The pending route is unchanged. Retry the provider choice or cancel settings. Any missing default already established by completed authentication remains durable. |
| Save-time status is stale or fails | One distinct effective provider did not freshly authenticate before the file transaction. | Repair or complete native sign-in, return to Save, and reprobe. No pending settings file update occurs; completed auth/default writes are not rolled back. |

Interactive startup first needs any authenticated provider, then every distinct
effective route under CLI override → agent route → workspace default → Codex.
Removing or rewriting an explicit route is not an authentication recovery
mechanism. Noninteractive commands do not open the picker; prepare native
authentication before using their provider routes.

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
static and live-resume suggestion editing, complete capable-frame clearing, and
restoration across terminal outcomes. Windows consoles remain unverified.

## Pause, quit, and cleanup

Declined or interrupted pause confirmation keeps the current attachment; EOF at
confirmation exits. Confirmed pause writes durable `paused`, releases access,
retains defaults, clears current attachment/current focus/agent focus, and
returns to the lobby. Quit and main/composer EOF release access without pausing.

Cleanup attempts each applicable stage once in this order: Runner close, known
access release, backend close, then state clearing. A failure in one stage does
not skip later stages. An uncaught body `KeyboardInterrupt` or `SystemExit`
outranks cleanup failures; without one, the first cleanup process control wins.
The winner propagates only after all later stages and state clearing run, with
the same exception object and traceback. The Runner reference remains only when
Runner close itself was interrupted by process control; successful close or an
ordinary close failure clears it. Attachment, focus, and memory always clear,
and the controller closes.

With no retained process control, ordinary cleanup failures return `2` and
attempt one nonduplicated `cleanup_failed`; a diagnostic process control cannot
replace a retained body or cleanup winner. This arbitration applies only to
controls that reach the outer lobby boundary. A command that handles Ctrl-C or
cancellation retains its documented cancelled/recovery result. Later startup
resumes a ready default or asks for confirmation before resuming a paused
default; only an active default can become focus.

Backend close releases recorded access claims and ephemeral Codex staging. It
does not pause or delete the durable story session, remove saved provider-thread
references, or erase provider-native conversations. Antigravity definitions,
work directories, and isolated native state remain plaintext under its
provider-state root. A transport or protocol failure can taint one
session/backend/agent thread; its next call starts a fresh provider conversation
and preserves the old native data. There is no user-facing session-close
command or secure-erasure workflow.

## Current limits

- Codex and Antigravity are supported. Nullable routes inherit the workspace
  default and final Codex fallback. The legacy backend name `gemini` is
  rejected with guidance to use `antigravity`.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact rollback is best effort across files, not one transaction.
- Advisory input is one line; bare `/run` owns multiline composition.
- Conversation search is active-only; transcript reads retain no cursor.
- Standalone compose/artifact commands are unavailable.

See [Session lifecycle](../design/session-lifecycle.md),
[Troubleshooting](../guides/troubleshooting.md), and
[Filesystem safety](filesystem-safety.md). Antigravity-specific state,
preflight, and restart contracts are in the
[Antigravity guide](../guides/antigravity.md).
