# Troubleshooting

> **Availability:** these remedies cover the implemented CLI, Codex backend,
> workspace persistence, and Python foundations. There is no interactive shell
> or transcript/search command to repair.

Key terms: **preflight**—checks before debate execution; **strict UTF-8**—text
that rejects invalid byte sequences; **immutable ID**—a reservation that cannot
be reused; **lease**—temporary provider-session attachment metadata.

## The command is not found

Activate the environment where the repository was installed, reinstall, and
refresh the shell command cache:

```bash
python -m pip install --editable .
rehash
story --help
```

Source changes appear through an editable install, but new console-script names
require reinstalling. `story` and `story-writing-agents` reach the same parser.

## No workspace or session is selected

Run from an initialized workspace or its descendant, or pass
`--workspace PATH`. Discovery chooses the nearest initialized ancestor. Start a
session to persist a selection, or pass `--session SESSION`; only a successful
start changes `cli_context.json`.

## Smoke exits with 1 or 2

Code `1` includes `failed_check` in compact JSON. Repeat with
`story smoke --workspace ./smoke-debug` to retain artifacts. Code `2` uses the
normal error channel; validate existing configuration as strict UTF-8 JSON.

## `BackendProtocolError` or session contention

Confirm Codex is installed, `codex login` succeeded or `CODEX_API_KEY` exists,
the session is active, the selected model exists, and the timeout is adequate.
A timeout, cancellation, malformed response, or ambiguous provider failure can
taint a thread; the next compatible invocation starts a fresh thread.

`SessionInUseError` means a live or unverifiable lease blocks attachment. Stop
the owning process normally. Do not delete uncertain lease state or
`.lease-claims.lock`; status alone cannot prove staleness. See the exact
[lease-state contract](../reference/backend-and-sessions.md).

## A prompt or draft file fails

Run `story init WORKSPACE` to add missing canonical role prompts without
overwriting custom ones. Prompt, persona, and core files must be readable,
strict UTF-8, inside the workspace, and free of symlink components. If one
changed during binding, finish the edit and retry with a new debate ID.

`--draft-file` must exist and decode as strict UTF-8. Its relative path starts
at the shell's current directory even when `--workspace` points elsewhere.

## A directive is rejected

Pass one exact standalone directive with unchanged case and no surrounding
spaces, newline, or commentary. Create a referenced persona template before
applying it. A rejected directive preserves the previous override state.

## A debate or output already exists

`DebateExistsError` means the session/debate ID was already reserved. Inspect
it and choose a new ID; deletion or same-ID resume is unsupported.
`ManuscriptExistsError` means the output target exists. Preserve it, then use a
new debate ID and new relative output path.

## The result is deadlocked, active, or interrupted

For `deadlock`, read `action_required` and critiques, make a user ruling, update
core documents if appropriate, and start a new debate. `active` proves only a
reservation, not process liveness. `interrupted` records best-effort external
cancellation. Neither can resume under the old ID.

Malformed persisted JSON or JSONL raises a format error rather than being
silently skipped or repaired. Preserve evidence before manual intervention.
See [Inspection and recovery](inspection-and-recovery.md) and the
[error reference](../reference/configuration/identifiers-and-errors.md).
