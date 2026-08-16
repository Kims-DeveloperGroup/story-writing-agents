# Troubleshooting

> **Availability:** these remedies cover the CLI, conversations, Codex,
> workspace persistence, and Python APIs.

Key terms: **preflight**—pre-debate checks; **strict UTF-8**—rejects invalid
bytes; **immutable ID**—cannot be reused; **lease**—temporary attachment metadata.

## The command is not found

Activate the installed environment, reinstall, and refresh its command cache:

```bash
python -m pip install --editable .
rehash
story --help
```

Editable source changes appear immediately; new console-script names require
reinstalling. Both aliases share a parser.

## Interactive mode requires a terminal

`story` and `story chat` require TTY stdin and stdout. A missing, false, or
failed `isatty()` emits `tty_required` on stderr and exits `2` before input,
option validation, workspace/context access, backend construction, or mutation.
Pipes and scripts must use `story run`.

## No workspace or session is selected

Run from an initialized workspace or descendant, or pass `--workspace PATH`;
discovery chooses the nearest initialized ancestor. Start a session to save its
selection, or pass `--session SESSION`; only successful start changes
`cli_context.json`.

For `session_inactive`, create `/session new NEW_ID` or reactivate with
`/session resume EXISTING_ID`. For `conversation_required`, create
`/conversation new` or select `/conversation use`.

## A shell command is unavailable

`/help [COMMAND]` shows syntax; `/status` shows scope. Management and local read
commands need attachment; advisory lines, `/history`, `/run`, and `/draft-file`
also need an active selection.

The main prompt rejects standalone `/compose`, `/send`, and `/cancel`. Bare
`/run` alone opens the composer, where `/send` submits and `/cancel` discards.
Advisory input remains one line.

## An answer did not search conversation memory

An agent may answer without retrieval. `/search QUERY` is provider-free
inspection and does not force retrieval. Use a hit's handle with
`/transcript ID` for a bounded page. Search covers active conversations; transcript also
reads archives, which agent retrieval excludes.

## Smoke exits with 1 or 2

Code `1` includes `failed_check`; repeat with
`story smoke --workspace ./smoke-debug` to retain artifacts. Code `2` uses the normal error channel;
validate configuration as strict UTF-8 JSON.

## `BackendProtocolError` or session contention

Confirm Codex installation/login or `CODEX_API_KEY`, active session, model, and
timeout. Timeout, cancellation, malformed response, or ambiguous provider
failure may taint a thread; the next compatible call starts fresh.

`SessionInUseError` means a live or unverifiable lease blocks attachment. Stop
its owner normally. Never delete uncertain lease state or `.lease-claims.lock`;
status cannot prove staleness. See the
[lease contract](../reference/backend-and-sessions.md).

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
cleanup. `cleanup_failed` forces exit `2` and means Runner close, detach, or backend close may be
incomplete. Before retrying, inspect retained temporary or lease state, stop a
known owner normally, and never delete uncertain lease state.

Malformed JSON/JSONL raises instead of being skipped or repaired. Preserve it
before manual intervention.
See [Inspection and recovery](inspection-and-recovery.md) and the
[error reference](../reference/configuration/identifiers-and-errors.md).
