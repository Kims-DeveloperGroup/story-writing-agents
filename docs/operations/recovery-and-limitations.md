# Recovery and limitations

> **Available:** shell workflows and process-level POSIX PTY verification.
> Automatic retry, stale-owner cleanup, event repair/deletion, and Windows
> console emulation are not.

Key terms: **terminal**—nonresumable; **lease proof**—session active and `OWNED`;
**taint**—discard incomplete history; **durable proof**—trusted persisted progress.

## Debate and shell-run recovery

Debate IDs reserve before providers and cannot resume/reuse; `active` is not
liveness. Inspect, fix, and choose a new ID; deadlock needs revised input.
Context sidecars evidence included/fresh/legacy mode, not replay.

Expected outcomes use fixed codes, not candidate, critique, result-action,
model, or exception prose. Workspace failure returns safely only with the same
active `OWNED` session; failed proof clears state and exits. See
[CLI mappings](../reference/cli.md#interactive-consensus).

Observer failure raises `ProgressObserverError`; before durability it
best-effort marks `interrupted`, while later failure cannot rewrite results.
Python cancellation joins and emits `INTERRUPTED` only after persistence;
observer failure remains its cause.

## Controller cancellation proof

One lifecycle `asyncio.Runner` serves advisory/consensus until cleanup. Delivery
must first produce `KeyboardInterrupt`; controller proof then decides recovery.

Advisory first SIGINT returns attached pre-entry only if Runner task inspection
proves quiescence; fixed copy appears without selection/target/durable mutation
or runtime/provider call. Entered recovery requires runtime `CancelledError`
and join. Only then can runtime append
`CancelledError: conversation turn was cancelled`, failed storage leave an
unmatched user, or incomplete history become tainted.
Nonquiescent pre-entry, unjoined, or bare cancellation is fatal
`operation_interrupted`. See
[exact records](../reference/interactive-terminal.md#exact-cancellation-records).

Consensus Ctrl-C before entry is provider-free/nonmutating when the Runner is
quiescent. After entry, recovery requires joined cancellation and trusted
terminal `INTERRUPTED`; retry uses a new ID. `INTERRUPTED` may precede `STARTED`
and leave `/inspect last` unset. Missing proof reports `operation_interrupted`,
abandons attachment bookkeeping, then exits `2` with `cleanup_failed`.

Cancelling search/transcript/history is provider-free/read-only. Completed rows
remain; final result/hint is omitted, with no second read/write.

## POSIX PTY boundary

On supported POSIX, process tests give the CLI a real controlling PTY and send
its configured VINTR/VEOF bytes rather than injecting controller exceptions.
VINTR becomes process SIGINT/`KeyboardInterrupt`; the proof rules above then
apply. VEOF exercises actual terminal EOF input paths.

The suite covers TTY/non-TTY guards, prompts, subject/search/retrieval/consensus,
composition, cancellation, and partial rendering under
`NO_COLOR=1` and `TERM=dumb`. It confirms provider termination/taint, interrupted
debate durability, lease release, and Runner/backend cleanup. The shell creates
no terminal history.

Unavailable POSIX modules, PTY setup, or byte VINTR/VEOF skips explicitly.
Windows console emulation is unverified and out of scope.

## Input, EOF, terminal, and cleanup

Main-prompt Ctrl-C reports no action. Composer Ctrl-C or `/cancel` discards its
buffer. No/blank/Ctrl-C end confirmation stays attached; confirmation EOF exits
without ending. Confirmed end deactivates/releases the lease, retains saved
selections, clears process scope, resets target to lead, and returns to the
lobby. Main/compose EOF, `/quit`, and `/exit` clean up without deactivation.

Terminal input/output `OSError` is `operation_interrupted`; input `RuntimeError`
is `internal_error`. Both exit `2` through cleanup. Progress-output `OSError` or
`KeyboardInterrupt` is `operation_interrupted`; other callback failure is
`internal_error`. Diagnostic output is best effort and never skips cleanup.

Cleanup attempts `Runner.close()` → known detach → `backend.close()`. Runner
close finalizes residual tasks/async generators. Failure still attempts later
stages, clears process-only attachment/selection/memory, exits
`2`, and emits at most one best-effort `cleanup_failed`. If Runner close gets a
`BaseException`, later stages run and process scope clears, but its unproved
reference remains before propagation. Durable sessions, selections, threads,
transcripts, debates, and artifacts follow normal lifecycle rules.

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
