# Recovery and limitations

> **Available:** shell workflows and process-level POSIX PTY verification.
> Automatic retry, stale-owner cleanup, event repair/deletion, and Windows
> console emulation are not.

Key terms: **terminal**—nonresumable; **lease proof**—session active and `OWNED`;
**taint**—discard incomplete history; **durable proof**—trusted persisted progress.

## Debate and shell-run recovery

Debate IDs reserve before providers and cannot resume/reuse; `active` is not
liveness. Inspect, fix, and choose a new ID. Context sidecars evidence context
mode, not replay.

Expected outcomes use fixed codes, not result/model/exception prose. Workspace
failure returns safely only with the same active `OWNED` session; failed proof
clears state and exits. See
[CLI mappings](../reference/cli.md#interactive-consensus).

Observer failure raises `ProgressObserverError`; before durability it
best-effort marks `interrupted`, while later failure cannot rewrite results.
Python cancellation joins and emits `INTERRUPTED` only after persistence;
observer failure remains its cause.

## Controller cancellation proof

One lifecycle `asyncio.Runner` serves advisory/consensus until cleanup. Delivery
must first produce `KeyboardInterrupt`; controller proof then decides recovery.

Advisory first SIGINT returns attached pre-entry only when Runner inspection
proves quiescence, without state/provider work. Entered recovery requires
runtime `CancelledError` and join. Only then can runtime append
`CancelledError: conversation turn was cancelled`, failed storage leave an
unmatched user, or incomplete history become tainted.
Nonquiescent, unjoined, or bare cancellation is fatal `operation_interrupted`.
See
[exact records](../reference/interactive-terminal.md#exact-cancellation-records).

Consensus Ctrl-C before entry is provider-free/nonmutating when the Runner is
quiescent. After entry, recovery requires joined cancellation and trusted
terminal `INTERRUPTED`; retry uses a new ID. `INTERRUPTED` may precede `STARTED`
and leave `/inspect last` unset. Missing proof reports `operation_interrupted`,
abandons attachment bookkeeping, then exits `2` with `cleanup_failed`.

Cancelling search/transcript/history is provider-free/read-only. Completed rows
remain; final result/hint is omitted, with no second read/write.

## POSIX PTY boundary

On supported POSIX, process tests use a controlling PTY and configured
VINTR/VEOF bytes. VINTR becomes SIGINT/`KeyboardInterrupt`; VEOF exercises real
terminal EOF paths.

The `TERM=dumb` journey proves unchanged line-input fallback without suggestions
or ANSI. A capable `TERM=xterm-256color` case shows the full panel before Enter,
prefix editing and clearing, no provider/durable mutation, and exact attribute
restoration. The wider suite covers shell workflows, cancellation, provider
taint, debate durability, leases, and cleanup. The shell creates no history.

Unavailable POSIX modules, PTY setup, or VINTR/VEOF bytes skip explicitly.
Windows console emulation remains unverified.

## Input, EOF, terminal, and cleanup

Main Ctrl-C reports no action; composer Ctrl-C or `/cancel` discards. Negative,
blank, or interrupted end confirmation stays attached; its EOF exits. Confirmed
end deactivates/releases, retains selections, clears scope, resets lead, and
returns to lobby. Main/compose EOF and quit aliases clean up without deactivation.

The capable editor restores captured attributes after submit, control events,
and active failure. Unsupported or recoverable pre-input setup uses fallback;
unproved restoration is fatal `internal_error`. Terminal I/O `OSError` is
`operation_interrupted`; input `RuntimeError` and other callback failure are
`internal_error`. Both exit `2` through cleanup; diagnostics remain best effort.

Cleanup attempts `Runner.close()` → detach → `backend.close()`, including later
stages after failure. It clears process scope, exits `2`, and emits at most one
best-effort `cleanup_failed`. A Runner-close `BaseException` propagates after
later cleanup, retaining its unproved reference. Durable state follows normal
lifecycle rules.

## Session, management, and current limits

A lease proves ownership, not remote health. Another/uncertain owner blocks
attachment regardless of age; never delete coordination files. Restart restores
only an active saved conversation. Missing/archived selection opens unselected
without rewriting. Selected archive uses
[bounded compensation](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).

History is one snapshot; later appends wait. Corrupt transcript/catalog/
inspection data fails closed. Missing explicit inspect is
`not_found`; absent `last` is normal and process-local. Failed directives retain
prior override bytes; successful management changes only overrides.

- Codex is the only provider; reserved `gemini` fails preflight.
- Provider calls have timeout, but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact rollback is best effort across files, not one transaction.
- Advisory input is single-line; bare `/run` owns multiline composition.
- Search is active-only; transcript reads retain no cursor.
- Standalone compose/artifact commands are unavailable.

For provider failure, check executable, login/key, model, timeout, session, and
lease. Never skip damaged records. See [Inspection](../guides/inspection-and-recovery.md)
and [Filesystem safety](filesystem-safety.md).
