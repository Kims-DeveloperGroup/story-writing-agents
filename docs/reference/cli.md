# CLI reference

> **Availability:** bare `story` and `story chat` open the TTY shell for session
> and conversation management, persistent settings, advisory/local reads, and
> focused-context consensus.

`story` and `story-writing-agents` are aliases for
`story_writing_agents.cli:main`.

## Top-level syntax

| Command | Exact syntax |
| --- | --- |
| Interactive | `story` or `story chat [SESSION] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--codex-timeout SECONDS] [PROMPT | --prompt TEXT | --draft TEXT | --draft-file PATH] [--output PATH] [--debate-id ID] [--max-debate-rounds N]` |
| Inspect | `story inspect [--workspace PATH] [--session ID] [--debate ID]` |
| Session create | `story session create SESSION [--workspace PATH]` |
| Session reopen | `story session reopen [SESSION] [--workspace PATH]` |
| Session pause | `story session pause [SESSION] [--workspace PATH]` |
| Smoke | `story smoke [--workspace PATH]` |

Lifecycle commands have no `--session`. Omitted reopen/pause uses the default;
create requires a positional name. Create/reopen validate provider configuration,
transition to ready, save the default only after success, and release access.
Pause retains the default. Success prints sorted, indented JSON with exactly:

```json
{
  "access_state": "claimable",
  "durable_state": "ready",
  "session_id": "demo"
}
```

Pause prints `paused` instead of `ready`. Access is always `claimable` after a
successful lifecycle process exits.

<a id="smoke-report"></a>

## Smoke report

`story smoke` writes compact key-sorted JSON. Success keys are exactly
`checks`, `command`, `debate_id`, `debate_status`, `note`, `provider_tested`,
`revision_id`, `rounds_completed`, `session_id`, `status`, and `workspace`; a
named invariant failure adds only `failed_check`.

On a pass, `provider_tested:false`; checks, in order, are
`workspace_initialized`, `directive_persisted`,
`conversation_catalog_persisted`, `conversation_routing`,
`conversation_search`, `conversation_continuity`, `conversation_retrieval`,
`consensus_context`, `revision_workflow`, `consensus_persisted`,
`manuscript_committed`, and `core_documents_unchanged`.

Implicit smoke owns, initializes, and removes its temporary `Workspace`.
Explicit CLI smoke initializes one `Workspace`, retains it, and passes that
exact instance to the smoke workflow; the CLI does not resolve the path again
for handoff or post-run diagnostics.
Explicit workspaces retain any plaintext diagnostics that were successfully
written.
Exit `0` passes, `1` names a failed invariant, and `2` covers usage/setup or an
unexpected failure. See the [smoke guide](../guides/smoke-test.md).

<a id="runtime-debug-logs"></a>

## Runtime debug logs and failure hints

Installed commands best-effort append fixed operational records to
`debug_logs/runtime.jsonl` only after workspace initialization or discovery
succeeds. This includes successful commands and operations that are read-only
with respect to story/session state, such as inspection, status, help, search,
transcript, history, and lifecycle reads. The bounded log is a separate side
effect; those operations retain their domain-state contract.

| Boundary or result | Internal behavior | What the user sees |
| --- | --- | --- |
| Before a usable workspace | No workspace-bound diagnostic handle is acquired, so this invocation submits no record. | Argparse, TTY, interactive-option, initialization, and discovery errors retain their established output and show no debug-log hint. |
| Resolved or initialized workspace; normal activity | The owning CLI, controller, backend, or runtime attempts fixed trace records. | Normal success and domain-result output is unchanged. When created, the active log may be inspected at the fixed relative path. |
| Handled post-workspace application error | The responsible boundary may attempt a content-safe error record; presentation does not depend on that record succeeding. | Interactive rendering adds the exact third notice below. A caught noninteractive exception preserves its first stderr line and adds the exact second line below. |
| Diagnostic submission fails | Consumer-private guards catch even a diagnostic `BaseException`; the record may be absent. | The operation's result, exception, cancellation, cleanup, output, and exit status remain authoritative. A displayed hint is only an availability notice. |
| Implicit smoke | Fixed records are attempted only inside the temporary workspace; normal return or exception propagation removes that workspace and any records successfully written. | A returned compact report has no debug path or hint; a propagated exception also exposes none, and no workspace remains to inspect. |
| Explicit smoke | The CLI initializes and retains one identity-bearing `Workspace`; smoke borrows that exact instance. CLI and smoke diagnostic-handle acquisition and each submission are guarded separately, and the CLI does not re-resolve the path for handoff or post-run diagnostics. | Reports retain their established compact output. After initialization succeeds, a propagated ordinary `Exception` caught by the CLI keeps its first stderr line and always gets the fixed second line, even when the record is absent. Root replacement can omit records but cannot redirect them or change output or exit status. Initialization failure is hint-free; a genuine process-control exception propagates unchanged. |

Each submitted interactive command is classified independently when its final
trace can be written. A normally returned command that successfully rendered a
typed handled error ends `failed`/`rejected`; a normally returned command that
rendered no typed error ends `succeeded`/`completed`. This records whether that
command displayed a handled error, not whether it mutated story/session state or
retried provider work. An earlier command's error cannot reclassify a later
command, and neither classification changes terminal output or domain results.

Every rendered interactive application error after workspace resolution adds
this exact third notice to its existing error panel:

```text
[debug log] If available, workspace-relative: debug_logs/runtime.jsonl
```

Outside explicit smoke, a caught noninteractive exception after a
workspace-bound logger is available retains its established first stderr line
and adds this exact second line:

```text
Debug log (if available, workspace-relative): debug_logs/runtime.jsonl
```

Returned nonexception outcomes do not use the exception hint: a smoke invariant
exit `1` remains compact JSON, and run deadlock/failed-result exits `3`/`4`
retain their established JSON. Success output also has no hint. “If available”
is literal: contention, unsafe storage, unavailable safe descriptor-relative
filesystem primitives, or another logging failure can drop a record.
For explicit smoke, successful initialization—not logger acquisition—is the
hint boundary; later ordinary failure still prints the line even if diagnostics
are unavailable.

Users can inspect the fixed path beneath the workspace root, but cannot
configure another destination or recover provider stderr through this feature.
Neither a hint nor a record proves that an operation completed. Content and
retention limits are in
[Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy)
and [Session storage](configuration/session-storage.md#runtime-debug-log-storage).

## Codex defaults and model override

Every installed command that constructs an `AgentBackend` validates and
snapshots both the workspace system defaults and the per-agent configuration.
For each agent, a non-null `--codex-model` on `story chat` or `story run`
overrides all persisted model values for that backend; otherwise the agent's
`codex_model` inherits the workspace `codex_model`, then the Codex provider
default. Reasoning resolves from the agent's `codex_reasoning_effort`, then the
workspace system default, then the provider default. Existing three-key
system-default files remain valid and provide no model/reasoning fallback until
one is configured.

There is no `--codex-reasoning-effort` option or supported public
system-default save API. Global `/settings` provides the installed interactive
edit/save workflow; choose `Providers`, then an agent, then `Model` or
`Reasoning effort` to edit per-agent Codex settings. Direct edits to the
validated JSON documents remain possible. An active backend and runtime keep
their construction-time configuration snapshot. Exact fields, validation, and
initialization compatibility are in
[Agent configuration](configuration/agent-configuration.md#system-defaults),
and invocation precedence is in
[Runtime overrides](configuration/runtime-overrides.md#resolution-precedence).

<a id="interactive-streams"></a>

## Interactive management, scope, and status

Both streams must be TTYs before workspace/backend access or exit `2`. Capable
POSIX main, composition, confirmation, picker, and settings-prompt reads use the
terminal-owned live editor. The complete live frame—context, prompt, editable
buffer, suggestions or choices, validation, and redraws—is cleared before a
submit, commit, cancel, interrupt, or EOF returns; an exceptional failure
attempts the same cleanup before propagating. Persistent semantic
orientation, validation, cancellation, discard, error, and result records are
written after that boundary and remain visible. Conversation-subject input is
always an ordinary read.

Main prompts project canonical root, subcommand, and finite fixed-option
candidates into a selectable semantic palette. Opening highlights the first
match; Up/Down moves that highlight instead of history. Tab replaces the full
active token, adding exactly one ASCII space only when the replaced token ended
at the buffer end. Enter on a highlighted static suggestion performs the same
completion without submitting, closes the palette, and requires a later Enter
to submit. Escape restores the first pre-completion snapshot when present and
dismisses that exact state. Closed-palette Up/Down uses process-local command
history; Shift-Tab has no action.

Composition shows only the preview-only `/send` then `/cancel`, filtered by an
exact, case-sensitive whole-buffer slash prefix. Confirmation has no
suggestions. `TERM=dumb`, unavailable capabilities, and non-POSIX editing use
ordinary line input with no pre-Enter palette or cursor cleanup. Printed prompt
and input text remain visible, and no synthetic final control frame is added;
composition and confirmation pass their structured plain semantic prompt once.

The one stream terminal retains at most 100 exact nonblank main submissions for
that process. Up recalls older entries, Down recalls newer entries, and Down
past newest restores the exact pre-navigation draft and cursor. Recalls are
editable copies with the cursor at the end. Invalid commands, duplicates,
`/quit`, fallback commits, and nonempty VEOF commits are eligible; whitespace
is preserved. Empty/blank input, Ctrl-C, empty VEOF, physical EOF, failures,
composition, subjects, confirmations, and nested settings input are excluded.
The 101st entry evicts only the oldest. Nothing is persisted.

A capable live resume choice records the full explicit command after frame
cleanup and terminal restoration. In ordinary-line fallback, a submitted
omitted command is the history entry whether its fresh choice read opens a
picker, reports no eligible rows, or reports an error. When a picker opens, the
following numbered selection is history-free. Navigation, static
completion-only Enter, Escape dismissal, cancel, interrupt, EOF, and input
failure add nothing.

Main matching is exact and case-sensitive at the cursor, with ASCII space/tab
token boundaries. Root offers 21 main-available registry tokens, help offers 24
including the composition-only commands, and agent-valued positions offer the
four preferred prompt aliases—`lead`, `plot`, `character`, and `execution`—in
route order. The parser still accepts all ten declared route aliases; runtime
values are not synthesized by the static grammar projection. Fixed-only
no-match positions show an empty open
palette, while mixed free-value slots close it. Filtering retains an eligible
highlight. The first changing Tab saves the full buffer/cursor snapshot; edits
in the same grammar position keep it, and a grammar-position change resets it.
Static completion-only Enter is not a submission or history entry; a later
submit sends the visible buffer to the parser once. The generic single-select
picker and cancellable-prompt seams remain package-internal. `/settings`
consumes both. A submitted omitted resume command uses the picker only when its
fresh lifecycle/catalog read succeeds with eligible rows; an empty set or read
failure reports the applicable result or error without a picker. The picker's
capable form is live and transient; line fallback is numbered. Exact capable
omitted resume commands also receive controller-owned pre-Enter choices through
the existing main input; dynamic-choice Enter submits an explicit identity and
bypasses the picker.

`/settings` is alias-free, takes no argument, and is available with or without
an attached session. It edits isolated Current and Pending snapshots for
Language, Persona, Maximum debate rounds, and Providers through a six-choice
editor in this order: those four fields, Save, Cancel. `Providers` opens a
nested canonical-agent picker, then a Provider/Model/Reasoning effort picker;
Model and Reasoning edit only that agent and `null`/Inherit falls back to the
system defaults. Settings pickers and prompts follow the same transient input
lifecycle as all capable input; fallback readers retain printed input and add no
synthetic final picker frame. Nested Escape returns to
the parent silently without a Current/Pending snapshot or cancellation record.
Save and Cancel each open a transient yes/no confirmation with a changed-only
Current/Pending context: only differing fields appear in both sections, each
with a `(Changed)` cue, while no differences show `No pending settings changes.`
`yes`/`y` commits the requested action; `no`/`n`, blank, or nested Escape
returns to the settings picker, and invalid confirmation input retries. Top-
level Escape, Ctrl-C, or EOF cancels the whole workflow without writing. The
submitted `/settings` line has already been cleared with the capable main frame;
its history entry remains. Ordinary-line fallback leaves its printed input
visible, with the same history semantics. `/cancel` is literal model text in the
free-text prompt.

Save validates the complete pending pair, then rejects any exact-byte change to
either configuration document or any persona-catalog change since opening.
Only changed documents are canonically replaced, in system-defaults then
agent-configs order. A write failure attempts reverse-order restoration of
every attempted target, including the failing target; rollback uncertainty is
reported separately. Success returns to the main prompt and applies to the next
shell/backend construction only. It does not refresh the active backend or
runtime, call a provider, alter session/explicit overrides, or add credentials,
model discovery, or automatic save.

On capable real-CLI streams, orientation/current context, `/status`, `/inspect`,
central errors/recovery, and accepted management, advisory, retrieval, and
consensus output use responsive semantic layout with fixed decorative ANSI.
Full/context help, the agent roster, session/conversation catalogs, search,
transcript, and focused history use that layout too. `NO_COLOR` removes only
decoration and leaves the same structured plain text; narrow output stacks and
wraps labels without dropping facts. Composition and confirmation now use
ordered semantic input views, and composer entry/error/send/discard records use
semantic notices; no internal generic composer-status site remains. These
presentation changes do not alter command grammar, established callback/read
order, or noninteractive success stdout, JSON, ordering, or exit contracts. The
documented availability line is the sole additive noninteractive stderr
contract for a caught exception after a workspace-bound logger is available,
or after an explicit smoke workspace initializes successfully.

During one advisory turn, a capable raw stream may show the terminal-owned
four-glyph rolling indicator `◐`, `◓`, `◑`, `◒`. The driver clears the active
cell before each semantic/progress/result write, redraws while the operation is
pending, and clears and joins both operation and indicator tasks before the
final, cancelled, or error record. `NO_COLOR` keeps the glyph cycle because it
removes decorative SGR only. Advisory turns emit no `[routing]` or `[waiting]`
notice. Callback construction and non-TTY, `TERM=dumb`, non-POSIX, missing, or
uncertain-capability paths await the operation silently; the installed CLI's
TTY guard still rejects non-TTY shell entry before this point. An underlying
operation failure takes precedence over a simultaneous indicator failure;
indicator-only failures use the existing internal-error path after cleanup.

`/help [COMMAND]` (`/?`), `/status`, `/settings`, and `/session list` are global.
Session grammar is `/session list | create NAME | resume [NAME] | pause`. Explicit
`story chat SESSION` creates an absent session or resumes a persisted session.
Bare entry resumes a ready default and asks for confirmation before resuming a
paused default.

On a capable main prompt, exact omitted `/session resume` displays all persisted
sessions before submission, ordered by newest durable update then ID. Rows show
session ID plus durable and process-access state. Current attachment is
highlighted first, otherwise the saved default, otherwise the first row.
Up/Down previews, Tab is a no-op, and Enter submits the highlighted identity as
one complete explicit resume command. Escape only dismisses the rows and leaves
the omitted command in the buffer. If that omitted command is submitted instead,
including after Escape or through nonempty VEOF, the controller rereads the
lifecycle list. A successful nonempty read opens a history-free picker, live on
a capable stream; ordinary-line fallback shows the numbered form and accepts
blank for the highlight, an in-range ASCII decimal ordinal, or exact `/cancel`.

Picker cancellation or interrupt and rejection before acquisition are
mutation-free; EOF performs normal shell cleanup without selecting. An empty
list emits `[session] No story sessions found.` without opening the picker; a
lifecycle-read failure reports the applicable error without opening it.
Choosing the current attachment reports that it is already attached. Every
other choice is freshly
validated before acquisition or durable reopen. Later steps load destination
focus, persist the destination default, release the source when switching, and
only then commit in-memory scope. A successful paused-to-ready reopen is not
rolled back by later failure; newly acquired access is released where possible,
but durable lifecycle/default rollback is not promised. A reported default
write failure may follow atomic replacement, and source-release failure after
default persistence is fatal `cleanup_failed` with the new default retained.
Inspect persisted state after either error. Explicit
`/session resume NAME`, startup default handling, `/session list`, and standalone
`story session reopen` remain direct and unchanged.

Conversation selection grammar is exactly `/conversation resume [ID]`. With
`ID`, it directly resolves the established exact full ID or accepted
case-sensitive unique prefix of at least 12 characters across the complete
session catalog, then rejects an archived result. With `ID` omitted, capable
main input displays active conversations before submission in catalog order.
Rows use subject
labels and show a stable handle: the first 12 ID characters when unique across
the complete catalog, otherwise the full ID.
Current active focus is highlighted first, then an active saved default, then
the first row. Capable Up/Down previews, Tab does nothing, Enter submits the full
immutable ID through one explicit command, and Escape only dismisses the rows.
Submitting the omitted command instead performs a fresh catalog read and, when
needed for highlighting, a saved-default read. A successful read with active
rows opens the generic picker: live and transient on a capable stream, or
numbered blank/ordinal/`/cancel` input in fallback. Cancel and interrupt are
mutation-free; EOF performs normal shell cleanup without selecting. No active
rows emits
`[conversation] No active conversations found.` without opening the picker;
a catalog or saved-default read failure reports the applicable error without
opening it.

The committed full ID is freshly resolved against the complete catalog before
selection. A now-missing or archived choice is rejected, refreshed metadata is
used, and the saved default is committed before current focus changes. A
reported save failure leaves in-memory focus unchanged but may follow atomic
replacement of the persisted default; inspect the saved selection. The
existing explicit-ID path, `/conversation list` active-only default and
`--all`, startup restoration, agent routing, noninteractive behavior, schemas,
and package/public terminal APIs are unchanged.

Full help is one grouped semantic `Panel` in command-registry order; contextual
help is one command record with its group, syntax, aliases, provider/artifact
effects, and example. `/agents` is one canonical roster list. Session and
conversation catalogs, search hits, transcript events, and focused history
emit one positioned, one-record list per established row callback. Empty
states are explicit. If Ctrl-C interrupts search, transcript, or history after
some rows, those completed rows remain, one cancellation record follows, and
no trailing result, hint, or history-context record is emitted.

`/status` emits one read-only semantic lifecycle record with fields in this
exact order:

```text
Session | Durable state | Process access | Default session | Current attachment
Default conversation | Current focus | Agent focus | Provider readiness | Next action
```

`/session list` emits one row per persisted session, newest update then ID:

```text
Session | Durable state | Process access | Default session
Provider readiness | Next action
```

Neither command acquires access or displays owner hostname, PID, or claim token.
A default is not necessarily the current attachment; a default conversation is
not necessarily current focus. Readiness requires valid configuration, `ready`,
and `held_here`.

Conversation management, local reads, directives, and inspection need current
attachment. Advisory turns, `/history`, `/run`, and `/draft-file` also need
current focus on an active conversation. Search is active-only; transcript
reads can include archives. See [grammar](interactive-command-parser.md) and
[lifecycle mapping](../design/session-lifecycle.md).

<a id="interactive-consensus"></a>

## Interactive consensus and cancellation

| Syntax | Context behavior |
| --- | --- |
| `/run PROMPT` or bare `/run` then `/send` | One bounded snapshot of current focus. |
| `/run --fresh PROMPT` | Records focused identity; sends no transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft plus one focused snapshot. |

Runs revalidate scope and may write artifacts, never transcripts. Main Ctrl-C
takes no action; composer Ctrl-C or `/cancel` discards its buffer; EOF cleans up.
Missing cancellation proof is fatal. Exact terminal records are in the
[terminal reference](interactive-terminal.md#exact-cancellation-records).

Each composition attempt shows `Mode`, `Next line`, `Lines`, `Characters`,
guidance, then the focused numbered prompt. The `/send`, `/cancel`
`SuggestionPalette` is a preview, not completion or dispatch. `/say` is not
suggested; `/say /send` and
`/say /cancel` remain literal escapes. Typing, editing, or clearing the preview
has no provider, runtime, filesystem, or storage effect. Up/Down, Tab,
Shift-Tab, and Escape are no-ops in composition and confirmation; those routes
remain history-free.

## Workspace, noninteractive behavior, and exits

Except `init`/`smoke`, commands need the nearest initialized workspace. For
`directive`, `run`, and `inspect`, explicit `--session` wins over the default.
`story run` receives no conversation focus and does not reopen a paused session.

Exits: `0` success or normal shell exit; `1` failed smoke invariant; `2`
usage/setup/fatal shell; `3` noninteractive deadlock; `4` noninteractive
failure. See [Consensus runs](../guides/consensus-runs.md) and
[Terminal safety](../operations/terminal-safety.md).
