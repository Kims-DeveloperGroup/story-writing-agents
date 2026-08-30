# Troubleshooting

> **Availability:** these remedies cover the CLI, conversations, Codex,
> Antigravity, workspace persistence, and Python APIs.

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

If the runtime reports `Antigravity CLI executable was not found`, install the
Antigravity CLI and make `agy` visible to the same shell that launches `story`:

```bash
source ~/.zshrc
command -v agy
agy --version
```

If a nonstandard executable path is intentional, pass
`--antigravity-executable PATH`. See the
[Antigravity setup guide](antigravity.md#install-and-authenticate-agy).

## Interactive authentication does not complete

The picker reports only `authenticated`, `unauthenticated`, `unavailable`, or
`indeterminate` from the exact native probes. Codex uses `codex login status`;
Antigravity invokes the resolved executable with `models`. The default vectors
are:

```text
codex login status
agy models
```

An unavailable entry means the matching executable is missing from the
environment that launched `story`; install it or correct `PATH`, source the
applicable shell profile, and retry. An unauthenticated Codex selection runs
`codex login`; an unauthenticated Antigravity selection invokes the resolved
executable with no arguments (bare `agy` by default). A supported
`--antigravity-executable PATH` value on `story chat` changes only that
executable.
Indeterminate means the probe timed out, failed to launch cleanly, or returned
an unrecognized status; verify the native CLI outside the runtime, then rerun
`/auth` or restart `story` to probe again.

The native login may open a provider-controlled browser. On a successful local
flow, no authorization-code paste may be needed. A remote provider flow may
require one provider-controlled code paste; Story does not add a second paste,
exit command, confirmation, extra Enter, Ctrl-C, or EOF step. Once a fresh
silent status probe reports authenticated, Story stops and reaps that exact
login child if it is still open and automatically returns to startup, `/auth`,
or Settings. You do not need to exit the Codex or Antigravity interface.

The status probes are individually bounded and use null input and output, so
they cannot consume pasted input or capture provider output while native login
owns the real terminal. If a fresh probe does not report authenticated, native
interaction continues without an overall Story login deadline. A login exit or
authorization-code text is never proof by itself; fresh status is the sole
authority. Startup Cancel leaves the gate open; Ctrl-C or EOF exits without
starting the story runtime. `/auth` cancellation returns to the existing shell.
In `/settings`, failed or cancelled provider authentication preserves the
pending route, and stale authentication at a full-path Apply Changes prevents
the file update; Message-details-only and Conversation-RAG-only Apply Changes
never authenticate. See
[authentication recovery](../operations/recovery-and-limitations.md#authentication-recovery).

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

The main prompt rejects standalone `/compose`, `/send`, and `/cancel`.
`/run --output PART/PATH.md` without prompt text opens the composer, where
`/send` submits and `/cancel` discards. Bare `/run` first asks for that required
output path.
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
second stderr line after CLI workspace initialization or discovery succeeds.
Handle acquisition, record submission, or root revalidation may still fail, so
the hint can appear when the file does not exist. Initialization and discovery
failures and implicit smoke remain hint-free. Smoke invariant exit `1` and run
deadlock/failed-result exits `3`/`4` retain their JSON output without a hint.
Resolve the fixed path beneath the workspace root; the CLI never prints an
absolute diagnostic path.

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

## `/exchanges` does not open the expected response

Run bare `/exchanges` only after attaching the affected session. The command
accepts no IDs: select the exact Conversation or Debate full ID and subject,
then the canonical agent. It does not require or change conversation focus.

The viewer is part of every normal Story installation. First distinguish an
ordinary version upgrade from damaged same-version files. Pulling updated Story
source does not update installed dependencies. If the active environment still
has viewer 0.1.0 after the checkout is updated, rerun the normal editable
installation:

```bash
python -m pip install --editable .
```

That ordinary reinstall upgrades viewer 0.1.0 to required 0.1.1. It needs no
uninstall, `--force-reinstall`, or `--ignore-installed`. A missing required
viewer should likewise be repaired by rerunning the normal Story installation
first; the `viewer` extra is not a separate fix.

Use a forced reinstall only when viewer 0.1.1 is already selected at the exact
required pin but its installed files or three-name API are genuinely damaged.
Use the applicable Story form so dependency parity is restored together:

```bash
python -m pip install --force-reinstall story-writing-agents
python -m pip install --force-reinstall --editable .
```

The historical `story-writing-agents[viewer]` and editable `'.[viewer]'` forms
remain accepted empty compatibility aliases but add no capability. An
incompatible-viewer result means the installed three-name API does not match
the exact required pin; verify the project requirement and lock identify
`1d4f5f86ccb4ebd2f458f046df7a0b99b9fbeaec`. Checkout maintainers can also
initialize the matching submodule for audit or testing; runtime imports never
come from `vendor/jsonl-viewer`.

`No agent exchanges are recorded` is not proof that no provider call occurred.
Capture and storage are fail-open and never retry. Likewise, a correlated
request plus `provider_failure` outcome with no response means no exact valid
`ProviderResult` crossed Story's adapter boundary; it does not prove that the
native process emitted no stdout, stderr, tool/reasoning, or NDJSON data. A
completed valid result rejected by Story normally has request, response, and
`rejected` outcome under one `exchange_id`.

For `storage is busy`, wait for the current exchange write and retry. Preserve
the workspace on unsafe, corrupt, uncorrelatable, or viewer failures; inspect
only the content-safe `debug_logs/runtime.jsonl` before deciding on recovery.
If the selected stream expired after its picker was built, rerun `/exchanges`
to refresh inventory. Do not invoke or retry provider work merely to populate
diagnostics. See the complete [viewer workflow, privacy boundary, and error
table](agent-exchanges.md).

## Inspect a rejected provider response

For a sanitized `provider_protocol_error`, start with
`debug_logs/runtime.jsonl` for content-safe exception type and frame metadata.
Then use `/exchanges` to select the affected session scope and agent and inspect
the correlated request, optional valid raw `ProviderResult`, and terminal
outcome. This is the primary structured read-only view, but it never captures
provider-native stdout, stderr, NDJSON, or tool/reasoning streams.
If Codex or Antigravity returned a completed body that structured decoding
rejected, a separate record may exist under
`debug_logs/provider_error_responses/`. The terminal does not print that path or
body, and transport, timeout, cancellation, nonzero-exit, stderr, and other
pre-response failures do not produce one.

Use a trusted plain-text editor, not a terminal renderer, Markdown/HTML preview,
shell command, or executable workflow. Match candidate records by provider,
agent, session, and debate or conversation metadata rather than assuming the
newest file belongs to the failure. The body is hostile provider-controlled
plaintext. The directory is lazy, and contention, unsafe existing state, or a
storage failure can leave it absent while the original provider error remains
unchanged. See the safe inspection workflow and exact eligibility in
[Privacy](../operations/privacy-and-provider.md#rejected-provider-response-artifacts).

## Provider configuration or session contention

For Codex, rerun `/auth` and require a fresh `codex login status` result, then
check the ready session, model, and timeout. For Antigravity, require a fresh
`<resolved executable> models` result (`agy models` by default), then
check the configured model/effort and the same legacy-named
`--codex-timeout` bound. The Antigravity capability preflight is a bounded,
provider-free help probe: success verifies required headless flags, not
authentication, connectivity, model access, or response quality. The fixed
failure `Antigravity CLI headless capabilities could not be verified` means
the configured executable did not pass that probe.

The only supported backend names are `codex` and `antigravity`. A legacy
`gemini` value fails with
`backend 'gemini' is no longer supported; use 'antigravity'`.
Ambiguous provider failure may taint a thread; the next call starts fresh
without deleting prior provider-native data.

`provider_preflight_failed` reports readiness `no` and next action
`Repair provider configuration, then retry.`

`session_access_blocked` means a live local process owns access; close it and
retry. For `session_ownership_unknown`, preserve the claim and guard; inspect
[access recovery](../operations/recovery-and-limitations.md#session-access-recovery).

## Conversation storage selection or migration fails

Keep desired storage in `config/system_defaults.json` distinct from the active
backend recorded by `config/conversation_storage_state.json`. `/settings` and
`story storage use` select desired storage for the next shell; they never
migrate data, and startup fails closed instead of silently falling back.

Preserve both stores, the active marker, and both configuration files. Do not
hand-edit them or delete one store to force agreement. For selection,
installation, provisioning, parity, or startup failures, follow
[selection and activation recovery](../operations/recovery-and-limitations.md#conversation-storage-selection-and-activation-recovery).
For migrate or verify failures, also preserve the migration checkpoint and
follow
[migration recovery](../operations/recovery-and-limitations.md#conversation-storage-migration-recovery).

## A prompt or draft file fails

Run `story init WORKSPACE` to add missing role prompts without overwriting.
Prompt and persona files must be readable, strict UTF-8, inside the workspace,
and free of symlink components. Core documents are optional. Present safe
current bytes enter the allowlisted prompt context even when manual or
pre-provenance; this does not create publication authorship or repair catalog
history. Finish concurrent edits before a new-ID retry.

`--draft-file` must exist and decode as strict UTF-8. Its relative path starts
at the shell's current directory even when `--workspace` points elsewhere.

## A directive is rejected

Pass one exact standalone directive with unchanged case and no surrounding
text. Create a referenced persona first. Rejection preserves prior overrides.

## A debate or output already exists

`DebateExistsError` means the ID is reserved; inspect it and choose a new one.
`ManuscriptExistsError` means the target exists; preserve it, then use a new
debate ID and distinct `PART/PATH.md` output. A manuscript revision belongs at
a new path such as `part-01/revisions/chapter-r2.md`. Deletion, overwrite, a
single-component output path, and same-ID resume are unsupported.

## A writing artifact disappeared from eligible context

A manual or tool edit does not delete the file, but its SHA-256 no longer
matches repository provenance, so catalog listing omits it and exact artifact
reads reject it. Make external edits only after every workspace process has
stopped. Restore the exact recorded bytes or ask the agents to republish a core
document through a new unanimous run. Changed manuscripts use a new part-scoped
revision path. Internal transcripts, discussion logs, agent state,
single-component manuscript files, symlinks, hard links, and nonregular files
are never eligible.

Files ending in `.tmp`, `.held`, `.retired`, or `.completed` under the internal
writing-artifact `staging/` directory are retained publication evidence. They
are never eligible or authoritative recovery journals and may accumulate; do
not move them into `transactions/` or present them as story output.

If access reports `writing-artifact recovery is blocked`, stop, preserve a full
backup, and follow [artifact recovery](../operations/recovery-and-limitations.md#writing-artifact-recovery).

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
temporary state and claim state, stop a known owner normally, and preserve
uncertain ownership. Treat Antigravity-native state separately: backend close
retains it by design, so protect or back it up as plaintext session data rather
than deleting it as a cleanup artifact.

Malformed JSON/JSONL raises; preserve it before intervention. See
[Inspection and recovery](inspection-and-recovery.md) and the
[error reference](../reference/configuration/identifiers-and-errors.md). The
[Antigravity guide](antigravity.md#capability-preflight-and-fixed-errors)
collects provider-specific setup, error, and lifecycle boundaries.
