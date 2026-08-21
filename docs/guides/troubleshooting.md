# Troubleshooting

> **Availability:** these remedies cover the CLI, conversations, Codex,
> workspace persistence, and Python APIs.

Key terms: **preflight**—checks before provider work; **strict UTF-8**—rejects
invalid bytes; **immutable ID**—cannot be reused; **access claim**—temporary
exclusive process ownership.

## The command is not found

Activate the environment, reinstall, and refresh the command cache:

```bash
python -m pip install --editable .
rehash
story --help
```

Editable changes appear immediately; new script names require reinstalling.

## Interactive mode requires a terminal

`story` and `story chat` require TTY stdin/stdout. Failed `isatty()` emits
`tty_required` and exits `2` before workspace access. Scripts use `story run`.

## Slash suggestions do not appear

The selectable palette appears only at main prompts on capable POSIX TTYs.
Otherwise, commands still use plain line input with no palette or completion
keys. Composition has its separate preview-only `/send`, `/cancel` panel;
confirmation and subject input have none. At the main prompt, candidates filter
by exact case-sensitive prefixes: Up/Down moves the highlight, Tab completes
the active token, and Escape restores/dismisses the current completion cycle.
Tab or static completion-only Enter adds one trailing ASCII space only at the
buffer end; completion-only Enter needs another Enter to submit. Exact omitted
`/session resume` and `/conversation resume` additionally show controller-owned
choices; their highlighted Enter submits immediately, while Tab does nothing.
Escape followed by Enter, or nonempty VEOF, submits the omitted command and
starts a fresh choice read. When that read succeeds with eligible choices, the
generic picker is live on a capable terminal and numbered in ordinary-line
fallback. An empty result or read failure reports the applicable result or
error without opening a picker.
Completion does not enforce runtime scope; use `/help`. See
[terminal safety](../operations/terminal-safety.md#live-editor-boundary).

Capable submitted input disappears by design after submit, cancel, interrupt,
or EOF because the terminal clears the complete live frame before return; a
failure attempts the same cleanup before propagating. Persistent validation,
cancellation, discard, error, orientation, and result records remain.
Ordinary-line fallback cannot clear printed input and
adds no synthetic final control frame.

## No workspace, default session, or current focus

Run from an initialized workspace or pass `--workspace PATH`. Use `story session
create SESSION` to save a default, or pass `--session SESSION` to `run`,
`directive`, or `inspect`. Successful create/reopen saves it; pause retains it.

For `session_paused`, use `/session resume NAME` or `story session reopen NAME`.
For `session_not_attached`, create/resume in the shell. For
`conversation_required`, create `/conversation new` or focus one with
`/conversation resume`.

## A shell command is unavailable

`/help [COMMAND]` shows syntax; `/status` shows scope. Management/reads need an
attachment; advisory, `/history`, `/run`, and `/draft-file` also need focus.

The main prompt rejects standalone `/compose`, `/send`, and `/cancel`. Bare
`/run` alone opens the composer, where `/send` submits and `/cancel` discards.
Advisory input remains one line.

## An answer did not search conversation memory

An agent may answer without retrieval. `/search QUERY` is provider-free. Use a
hit's handle with `/transcript ID`; it can also read archives.

## Smoke exits with 1 or 2

Code `1` includes `failed_check`; repeat with `story smoke --workspace
./smoke-debug` to retain artifacts. Code `2` uses the normal error channel.

## Use the bounded runtime debug log

After workspace resolution, interactive errors show
`[debug log] If available, workspace-relative: debug_logs/runtime.jsonl`.
A caught noninteractive exception adds
`Debug log (if available, workspace-relative): debug_logs/runtime.jsonl` as its
second stderr line only after a workspace-bound logger is available. Explicit
smoke uses successful workspace initialization as the boundary, so a later
ordinary failure still prints the hint when logger acquisition, submission, or
root revalidation fails; initialization failure is hint-free. Smoke invariant
exit `1` and run deadlock/failed-result exits `3`/`4` retain their JSON output
without a hint. Resolve the fixed path beneath the workspace root; the CLI never
prints an absolute diagnostic path.

The file may be absent even when a hint appears because logging is fail-open.
Do not treat it as retry, telemetry, or proof that an operation completed. It
contains bounded safe operational facts and exception type/frame structure,
not story prose, arbitrary payloads, exception messages, or provider stderr.
Read-only story/session operations can append diagnostics. Users cannot choose
another diagnostic destination or recover discarded provider stderr from this
feature. See
[privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy),
[retention](../reference/configuration/session-storage.md#runtime-debug-log-storage),
and [filesystem safety](../operations/filesystem-safety.md#debug-log-filesystem-safety).

## Provider configuration or session contention

Confirm Codex login or `CODEX_API_KEY`, ready session, model, and timeout.
Ambiguous provider failure may taint a thread; the next call starts fresh.

`provider_preflight_failed` reports readiness `no` and next action
`Repair provider configuration, then retry.`

`session_access_blocked` means a live local process owns access; close it and
retry. For `session_ownership_unknown`, preserve the claim and guard; inspect
[access recovery](../operations/recovery-and-limitations.md#session-access-recovery).

## A prompt or draft file fails

Run `story init WORKSPACE` to add missing role prompts without overwriting.
Prompt, persona, and core files must be readable, strict UTF-8, inside the
workspace, and free of symlink components. Finish concurrent edits before a
new-ID retry.

`--draft-file` must exist and decode as strict UTF-8. Its relative path starts
at the shell's current directory even when `--workspace` points elsewhere.

## A directive is rejected

Pass one exact standalone directive with unchanged case and no surrounding
text. Create a referenced persona first. Rejection preserves prior overrides.

## A debate or output already exists

`DebateExistsError` means the ID is reserved; inspect it and choose a new one.
`ManuscriptExistsError` means the target exists; preserve it, then use a new
debate ID and relative output path. Deletion/same-ID resume is unsupported.

## The result is deadlocked, active, or interrupted

For `deadlock`, read `action_required` and critiques, make a ruling, update core
documents if needed, and start a new debate. `active` proves only reservation;
`interrupted` records best-effort external cancellation. Neither can resume.

A recoverable advisory Ctrl-C reports `[cancelled]` and keeps attachment; an
entered turn must cancel and join, and its provider thread may restart. Fatal
`operation_interrupted` means proof or terminal I/O failed and exits `2` through
cleanup. Cleanup still attempts Runner close, known-access release, backend
close, and state clearing in order. An uncaught body process control wins;
otherwise the first cleanup process control propagates after the later stages.
`cleanup_failed` forces exit `2` and means an ordinary Runner close, access
release, or backend close may be incomplete. Before retrying, inspect retained
temporary or claim state, stop a known owner normally, and preserve uncertain
ownership.

Malformed JSON/JSONL raises; preserve it before intervention. See
[Inspection and recovery](inspection-and-recovery.md) and the
[error reference](../reference/configuration/identifiers-and-errors.md).
