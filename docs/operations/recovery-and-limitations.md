# Recovery and limitations

> **Available:** cancellation, inspection, consensus, management, retrieval,
> and local reads. Automatic retry, stale-owner cleanup, event repair/deletion,
> and PTY signal delivery are not.

Key terms: **terminal**—nonresumable; **lease proof**—same session active and
`OWNED`; **taint**—discard incomplete provider history; **durable proof**—trusted
post-persistence progress.

## Debate and shell-run recovery

A debate ID reserves before provider work and cannot resume/reuse. `active` is
not liveness. Inspect, fix, and choose a new ID; deadlock also needs revised
input. Context sidecars evidence included/fresh/legacy mode, never replay.

Expected outcomes return fixed codes, never candidate, critique,
result-error/action, model, or exception prose. A workspace failure returns
safely only with the same active `OWNED` session; failed proof clears uncertain
state and exits. See the
[CLI mappings](../reference/cli.md#interactive-consensus).

Runtime observer failure raises `ProgressObserverError`; before durability it
best-effort marks `interrupted`, while later failure cannot rewrite results.
Python cancellation remains `asyncio.CancelledError`, joins work, and emits
`INTERRUPTED` only after confirming persistence. Observer failure is only its
cause.

## Controller cancellation proof

One lifecycle `asyncio.Runner` serves all advisory and consensus coroutines and
survives confirmed session end until controller cleanup.
These rules apply after `KeyboardInterrupt` reaches the controller; end-to-end
PTY operating-system signal delivery remains Issue #15.

Advisory Ctrl-C is friendly only after entry, `CancelledError`, and join. Scope
stays attached/selected with its target. After user append, runtime best-effort stores
`CancelledError: conversation turn was cancelled`; failed storage leaves an
unmatched user. Incomplete provider history is tainted, so the next compatible
turn starts fresh. Bare cancellation or missing join proof is fatal
`operation_interrupted`. Exact waiting/cancelled/hint records are in the
[terminal reference](../reference/interactive-terminal.md#exact-cancellation-records).

Consensus Ctrl-C before entry is provider-free/nonmutating when the Runner is
quiescent. After entry, recovery requires joined cancellation and trusted
terminal `INTERRUPTED`; retry gets a new ID. `INTERRUPTED` may precede `STARTED`
and need not update `/inspect last`. Missing proof writes best-effort
`operation_interrupted`, abandons attachment bookkeeping, then exits `2` with
`cleanup_failed` through backend-close cleanup.

Cancelling `/search`, `/transcript`, or `/history` is provider-free/read-only.
Completed rows remain; final result/hint is omitted, with no second read/write.

## Input, EOF, terminal, and cleanup

Main-prompt Ctrl-C reports no action and continues. Composer Ctrl-C or `/cancel`
discards only its local buffer. A no/blank/Ctrl-C end confirmation remains
attached; confirmation EOF requests normal exit without ending. Confirmed end
deactivates/releases the lease, retains saved selections, clears in-process
scope, resets target to lead, and returns to the outer lobby. Main/compose EOF,
`/quit`, and `/exit` also request normal cleanup without deactivation.

Terminal input/output `OSError` maps to `operation_interrupted`, exit `2`, and
cleanup; input `RuntimeError` maps to `internal_error`. Progress-output
`OSError` or `KeyboardInterrupt` maps to `operation_interrupted`; other callback
failure maps to `internal_error`. Diagnostic output is best effort and never
skips cleanup.

Cleanup always attempts `Runner.close()` → known detach → `backend.close()` in
that order. Runner close first finalizes residual tasks/async generators.
Ordinary stage failure still attempts later stages, clears process-only
attachment/selection/memory state, exits `2`, and emits at most one best-effort
`cleanup_failed`. If Runner close is interrupted by a `BaseException`, later
stages still run and process-only scope clears, but the unproved Runner reference
is retained before propagation. Durable sessions, selections, threads,
transcripts, debates, and artifacts remain according to normal lifecycle rules.

## Session, management, and current limits

A lease proves ownership, not remote health. Another or uncertain owner blocks
attachment regardless of age; never delete coordination files. Restart restores
only an active saved conversation. Missing/archived selections open unselected
without rewriting. Selected archive recovery uses
[bounded compensation](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).

History is one snapshot; later appends appear next time. Corrupt transcript,
catalog, or inspection data fails closed. Explicit missing inspect is
`not_found`; absent `last` is normal, and restart forgets its process-local map.
Failed directives preserve prior override bytes; successful management changes
only overrides.

- Codex is the only provider; reserved `gemini` fails preflight.
- Provider calls have a timeout but no retry or rate-limit manager.
- Owner history has no compaction, expiry, or retention policy.
- Artifact rollback is best effort across files, not one transaction.
- Advisory input is single-line; bare `/run` alone has multiline composition.
- Search is active-only; transcript reads retain no cursor.
- Standalone compose/artifact commands are unavailable.

For provider failure, check executable, login/key, model, timeout, session, and
lease. Never skip damaged records. See [Inspection and recovery](../guides/inspection-and-recovery.md)
and [Filesystem safety](filesystem-safety.md).
