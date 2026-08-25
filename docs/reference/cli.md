# CLI reference

> **Availability:** bare `story` and `story chat` open the TTY shell for session
> and conversation management, persistent settings, advisory/local reads, and
> focused-context consensus after native provider authentication succeeds.
> Provider-bound prompt text accepts inline `${path}` and `${path}#Lx-Ly`
> canonical artifact references. In eligible
> interactive prompt spans, `$`, `$${path}`, and `$$` discover and attach them
> without submitting.

`story` and `story-writing-agents` are aliases for
`story_writing_agents.cli:main`.

## Top-level syntax

| Command | Exact syntax |
| --- | --- |
| Interactive | `story` or `story chat [SESSION] [--workspace PATH] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--antigravity-executable EXECUTABLE] [--antigravity-model MODEL] [--antigravity-effort EFFORT] [--codex-timeout SECONDS]` |
| Initialize | `story init [WORKSPACE] [--overwrite-prompts]` |
| Directive | `story directive [--workspace PATH] [--session ID] TEXT` |
| Run | `story run [--workspace PATH] [--session ID] [--backend AGENT=BACKEND]... [--codex-model MODEL] [--antigravity-executable EXECUTABLE] [--antigravity-model MODEL] [--antigravity-effort EFFORT] [--codex-timeout SECONDS] [PROMPT | --prompt TEXT | --draft TEXT | --draft-file PATH] --output PART/PATH.md [--debate-id ID] [--max-debate-rounds N]` |
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

After that successful workspace boundary, the CLI marks later application
errors as hint-eligible before attempting to construct an optional diagnostic
handle. Handle acquisition and record submission are independently guarded, so
neither is required for the command to continue or for an eligible hint to
appear.

| Boundary or result | Internal behavior | What the user sees |
| --- | --- | --- |
| Before a usable workspace | No workspace-bound diagnostic handle is acquired, so this invocation submits no record. | Argparse, TTY, interactive-option, initialization, and discovery errors retain their established output and show no debug-log hint. |
| Resolved or initialized workspace; normal activity | The CLI marks hint eligibility and attempts its optional handle; the owning CLI, controller, backend, or runtime may attempt fixed trace records. | Normal success and domain-result output is unchanged. When created, the active log may be inspected at the fixed relative path. |
| Handled post-workspace application error | The responsible boundary may attempt a content-safe error record; presentation does not depend on a handle or record existing. | Interactive rendering adds the exact third notice below. A caught noninteractive exception preserves its first stderr line and adds the exact second line below. |
| Diagnostic acquisition or submission fails | Consumer-private guards catch even a diagnostic `BaseException`; the handle or record may be absent. | The operation's result, exception, cancellation, cleanup, output, and exit status remain authoritative. A displayed hint is only an availability notice. |
| Implicit smoke | Fixed records are attempted only inside the temporary workspace; normal return or exception propagation removes that workspace and any records successfully written. | A returned compact report has no debug path or hint; a propagated exception also exposes none, and no workspace remains to inspect. |
| Explicit smoke | The CLI initializes and retains one identity-bearing `Workspace`; smoke and every fresh workspace-bound service borrow that exact instance. CLI and smoke diagnostic-handle acquisition and each submission are guarded separately, and the CLI does not re-resolve the path for handoff or post-run diagnostics. | Reports retain their established compact output. After initialization succeeds, a propagated ordinary `Exception` caught by the CLI keeps its first stderr line and always gets the fixed second line, even when the record is absent. Root replacement can omit records but cannot redirect them or change output or exit status. Initialization failure is hint-free; a genuine process-control exception propagates unchanged. |

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

Except for implicit temporary smoke, a caught noninteractive exception after
CLI workspace initialization or discovery succeeds retains its established
first stderr line and adds this exact second line, even when diagnostic-handle
acquisition failed and no log file exists:

```text
Debug log (if available, workspace-relative): debug_logs/runtime.jsonl
```

Returned nonexception outcomes do not use the exception hint: a smoke invariant
exit `1` remains compact JSON, and run deadlock/failed-result exits `3`/`4`
retain their established JSON. Success output also has no hint. “If available”
is literal: handle acquisition, contention, unsafe storage, unavailable safe
descriptor-relative filesystem primitives, or another logging failure can
leave the file or a particular record absent. Successful CLI workspace entry—
not logger acquisition—is the hint boundary for `init`, explicit smoke, and
ordinary resolved-workspace commands. Initialization and discovery failures
remain hint-free.

Users can inspect the fixed path beneath the workspace root, but cannot
configure another destination or recover provider stderr through this feature.
Neither a hint nor a record proves that an operation completed. Content and
retention limits are in
[Privacy](../operations/privacy-and-provider.md#workspace-debug-log-privacy)
and [Session storage](configuration/session-storage.md#runtime-debug-log-storage).

<a id="interactive-provider-authentication"></a>

## Interactive provider authentication

Bare `story` and `story chat` authenticate before constructing the CLI context,
`AgentBackend`, controller, or session lifecycle. After workspace resolution
and the welcome, the runtime launches only these native commands without a
shell:

| Provider | Silent bounded status probe | Inherited-terminal sign-in |
| --- | --- | --- |
| Codex | `codex login status` | `codex login` |
| Antigravity | `EXECUTABLE models` | `EXECUTABLE` with no arguments |

For Antigravity, `EXECUTABLE` is `agy` by default or the resolved
`--antigravity-executable PATH` value on `story chat`.

Status probes run at the resolved workspace root with null stdin/stdout/stderr.
Exit `0` is authenticated, exit `1` is unauthenticated, a missing executable is
unavailable, and timeout or any other result is indeterminate. Unavailable
choices are disabled; authenticated choices need no sign-in; unauthenticated
choices start native sign-in; indeterminate choices trigger another status
check. While a login child remains open, Story waits in one-second bounded
intervals and runs the selected provider's existing silent, bounded status
probe after each interval. The probe's null streams keep it unable to consume
pasted input or capture provider output. A non-authenticated probe leaves the
native interaction in control; there is no overall login deadline.

A fresh exact authenticated result is the sole authority. When it arrives,
Story boundedly terminates and reaps only that exact login child if still open,
then refreshes both providers and returns automatically to the calling startup,
`/auth`, or Settings flow. The user does not need an exit command, Ctrl-C, EOF,
extra Enter, confirmation, or second code paste. If the login child exits
first, Story still requires a fresh probe. Prior state, code text, child exit,
and return code never authorize use.

If neither provider is authenticated, the startup picker remains the hard gate.
Cancel returns to that gate; Ctrl-C or EOF exits normally without constructing
the story runtime. Once at least one provider authenticates, a missing workspace
`default_backend` is persisted from the provider authenticated by that immediate
interaction, otherwise the sole authenticated provider, otherwise Codex when
both are authenticated. An existing default is never replaced.

Effective routes then resolve in this order: repeatable CLI override, explicit
per-agent route, workspace default, Codex. Each distinct effective provider
must pass a fresh gate before startup continues; explicit routes are never
rewritten. `init`, `run`, `smoke`, help, malformed invocation, standalone
lifecycle commands, and non-TTY rejection do not open this interactive gate.

Inside the shell, global no-argument `/auth` refreshes both states and opens the
same picker without story-provider work. Cancellation returns to the shell and
keeps any already established default. A newly completed authentication can
durably fill a missing default, but never replaces an existing default or an
explicit route.

Native sign-in inherits stdin, stdout, stderr, environment, process group, and
the resolved workspace root and may open a provider-controlled browser. The
runtime neither synthesizes nor captures URLs, provider output, account facts,
tokens, credentials, authorization codes, or native credential persistence.
Story never supplies its own code prompt: a successful flow has at most one
provider-controlled authorization-code paste, and local browser-capable flows
may need none. A remote provider can still require its own copy/paste step. See
[privacy](../operations/privacy-and-provider.md#native-authentication-boundary)
and [recovery](../operations/recovery-and-limitations.md#authentication-recovery).

## Provider routes and invocation settings

Every installed command that constructs an `AgentBackend` validates and
snapshots both the workspace system defaults and per-agent configuration. The
repeatable `--backend AGENT=BACKEND` accepts canonical
agent IDs and exactly `codex` or `antigravity`; it changes only that backend
instance. Duplicate agents are rejected. Legacy `gemini` is rejected with
fixed migration guidance to use `antigravity`.

For Codex, a non-null `--codex-model` on `story chat` or `story run` overrides
all persisted Codex model values; otherwise each agent inherits its
`codex_model`, workspace `codex_model`, then the provider default. Codex
reasoning resolves from the agent value, workspace value, then provider default.

For Antigravity, `--antigravity-executable` defaults to `agy`.
`--antigravity-model` and `--antigravity-effort` override every
Antigravity-routed agent in that backend instance. Otherwise model and effort
resolve per agent, then from workspace Antigravity defaults, then the provider
default. Effort accepts `low`, `medium`, or `high`. The legacy option name
`--codex-timeout` supplies the positive timeout for both provider adapters.

One mixed-provider shell can be opened with:

```bash
story chat draft \
  --backend plot_architect=antigravity \
  --antigravity-effort medium
```

Other agents retain their persisted routes. Bare `story` has none of these
options and uses the persisted configuration, workspace default, `codex`
fallback, and `agy`
executable default. Standalone lifecycle commands likewise have no provider
options, so a configured Antigravity route requires `agy` on `PATH`.

There is no `--codex-reasoning-effort` option or supported public
system-default save API. Global `/settings` provides the installed interactive
edit/save workflow; choose `Agents`, then an agent, then Provider, Model, or
Effort. It supports Codex and Antigravity and edits only the active
provider's per-agent model/effort while retaining inactive-provider values.
Entering Model or Effort discovers the pending effective provider afresh;
opening Settings and provider navigation do not launch catalog commands.
Custom models and inheritance remain supported. See the exact
[picker contract](interactive-terminal.md#provider-model-and-effort-pickers).
Direct edits to the validated JSON documents remain possible. An active backend
and runtime keep their construction-time configuration snapshot. Existing
three-key system-default files remain valid and provide no provider-specific
fallback until configured. Exact fields, validation, and initialization
compatibility are in
[Agent configuration](configuration/agent-configuration.md#system-defaults),
and invocation precedence is in
[Runtime overrides](configuration/runtime-overrides.md#resolution-precedence).
Antigravity installation, process controls, and verification are in
[Use Antigravity agents](../guides/antigravity.md).

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

Accepted provider-bound content is informative output, not a retained control
frame. On a capable stream, ordinary, `/say`, and `@agent` advisory text is
rendered once after cleanup as `you → ALIAS`, using the resolved preferred
target alias and exact semantic body without the command envelope. Inline,
fresh, and committed composer consensus prompts use `you → consensus`. These
messages appear before progress or provider work and remain through later
success, handled failure, cancellation, or post-acceptance interrupt. In
ordinary-line fallback the submitted input already remains visible, so the
terminal adds no duplicate accepted-input message. This is terminal
presentation only, not a storage, transcript-retention, provider-history, or
audit guarantee.

After workspace resolution, a capable POSIX terminal shows a bounded,
width-aware four-frame welcome before the authentication gate. Physical widths
of 48 columns and wider progressively reveal the original five-letter colored
`STORY` wordmark; 32–47 columns use the compact spaced identity, and narrower
terminals use the short identity. The capable animation has exactly three
0.4-second waits, then leaves the completed frame in scrollback. The wide
completed projection is nine rows with a 44-cell maximum logo width; compact
and narrow projections remain five rows. Sign-in guidance and its sanitized
result are each transient for 0.8 seconds.

The application treats `NO_COLOR` as color-eligible when it is unset, empty,
whitespace-only, or trimmed exact `0`; `NO_COLOR=0` therefore explicitly keeps
eligible ANSI decoration enabled. Any other value disables decorative ANSI,
without the runtime overwriting or exporting the environment. Presence of
`STORY_REDUCED_MOTION` independently selects one final colored frame when the
TTY is otherwise ANSI-eligible, with no animation, sleep, raw mode, cursor
control, or clearing. Nonzero/truthy `NO_COLOR`, non-TTY, `TERM=dumb`,
non-POSIX, callback, and uncertain-output paths render one plain final frame
without delay or ANSI. The welcome does not use the alternate screen or hide
the cursor, and it never includes provider output.
See the exact [terminal startup contract](interactive-terminal.md#startup-welcome-and-authentication).

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
token boundaries. Root offers 22 main-available registry tokens, help offers 25
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

Artifact activation is a separate overlay inside provider-bound text only:

| Input | Capable-terminal result |
| --- | --- |
| `$` | Opens `Writing artifact selection [open]` with `[controls] Up/Down selects; Enter/Tab inserts a whole-file reference.` Enter or Tab inserts `${path}` without submitting. |
| `$${path}` then Enter or Tab | Opens the named artifact in the independent viewer. |
| `$$` | The second `$` immediately opens ranged-source selection before a third key or newline. Up/Down moves the highlight; Enter or Tab opens that source in the viewer. |
| Shift+Up/Down, then Enter in the viewer | Selects a normalized inclusive range and inserts `${path}#Lx-Ly`. |
| Escape, Ctrl-C, EOF, or handled activation failure | Restores the exact buffer/cursor and performs no dispatch or history append. |
| `\$` | Leaves a literal dollar and performs no activation. |

Activation requires an attached session, a focused active conversation, and a
cursor in provider-bound text. The eligible spans are focused ordinary input;
valid `@agent` and `/say` tails; `/run` prompt text; the `/run` composer; and the
internal chat composer. Lobby input, attached input without focus, management
commands and arguments, IDs, subjects, options, output paths, settings,
confirmations, and output are inert. Shell orientation and full `/help` explain
the attachment/focus prerequisite and distinguish `$` whole-file insertion from
`$$` ranged selection. Escape dismisses an immediate ranged-source panel but
leaves `$$` in the buffer; Enter or Tab reopens it in place. Typing `{` after
`$$` instead continues a direct marker, including paths with spaces, Unicode,
`{`, `$`, and `#`.
Multiple inserted indicators retain order. Completion/viewing remains local;
only the later normal submit reaches the parser and runtime, where each
provider invocation must perform its own read.

The text after `$` is a case-sensitive path fragment over the current safe
reference sources. Discovery uses the explicit workspace or the nearest
initialized ancestor selected by normal CLI resolution, returns lexical paths,
and reads metadata rather than file bytes. It includes the four supported core
documents plus every safely representable lowercase-`.md` descendant anywhere
under `manuscript/`, including direct root files, arbitrary nesting, spaces,
Unicode, `{`, `$`, and `#`. This reference-source boundary is broader than the
strict part-scoped grammar for new consensus publications. No match leaves the
labeled panel open with
`[empty] No matching writing artifacts. Change the path fragment.` When the
source list is empty, `$` immediately shows `[empty] No writing artifacts. Add
a supported canonical core document or Markdown file with a lowercase .md
suffix under manuscript/. Consensus can also publish one.` Enter or Tab then writes the existing
`command_invalid` `empty_sources` error and
restores the draft; previewing the empty state starts no provider work.

The inserted `${path}` or `${path}#Lx-Ly` is an indicator, not embedded file
content. Several indicators may be attached to one line. On later normal
submission, the runtime captures and revalidates the ordered batch and requires
every addressed agent invocation to read every indicator through the
authorized runtime before accepting its result.

Line fallback prints a numbered source list and complete numbered viewer. It
accepts exact ASCII `START-END` or `/cancel`, then prints the amended draft and
requires a second Enter before submission. Blank accepts that draft; nonblank
replacement text is reevaluated. Source-list/viewer input is history-free, and
fallback scrollback cannot be erased. Fixed empty, invalid, stale, unreadable,
and repository-unavailable failures use content-free `command_invalid` or
`workspace_invalid` output. Exact text and recovery are in the
[artifact-reference guide](../guides/artifact-references.md#discover-view-and-attach-in-the-shell).

This adds no `/compose`, standalone artifact command, public viewer API, or
noninteractive picker. `story run` scripts must quote literal `${...}` prompt
text so the invoking shell does not expand `$`.

`/auth` and `/settings` are alias-free, take no argument, and are available with
or without an attached session. `/auth` follows the authentication contract
above. `/settings` edits isolated Current and Pending snapshots for
Language, Persona, Maximum debate rounds, and Agents through a six-choice
editor in this order: those four fields, Save, Cancel. `Agents` opens a
nested canonical-agent picker, then a provider-neutral Provider/Model/Effort
picker. Current/Pending confirmations retain provider-qualified rows for both
providers so inactive values remain identifiable.

Every retained Language, Persona, Maximum debate rounds, Provider, Model,
Custom model, and Effort editor always shows `Entered changes remain pending in
memory and are not saved; choose Save in Settings to persist them.` The text is
present on initial entry, after a valid Enter reopens the editor, and during
validation. The established entered-value success result remains unchanged and
is still the only inline completed result.

The top-level Settings picker shows `[save required] Pending changes are in
memory only and are not saved. Choose Save to persist them.` if and only if the
complete pending configuration differs structurally from its opening baseline.
This is recomputed on every top-level read, so the warning is absent initially
and after a complete revert. It is also absent from Agents, Agent settings,
value editors, and Save/Cancel confirmations. Capable, narrow, `NO_COLOR`, and
fallback views expose the complete text. Fallback cannot erase historical
scrollback, but the current prompt reflects the current comparison.

That same comparison labels the top-level choice exact plain `Save` while clean
and exact plain `Save (*Required)` while dirty. When ANSI decoration is
eligible, only `(*Required)` receives warning styling; the preceding separator
space remains unstyled. The existing `[save required]` notice remains present,
including in plain, `NO_COLOR`, narrow, and fallback views.

Only entering Model or Effort runs discovery. Each entry uses the pending
effective provider; Effort also uses the pending effective model. Model order
is Inherit, provider-order discovered models, Current custom when the pending
value is absent, then Custom model. Effort order is Inherit, exact selected-
model Codex efforts or Antigravity's global `low`, `medium`, `high`, then
Current compatibility when a pending effort is unreported.

Codex runs `codex debug models --bundled` and projects list-visible rows with
exact per-model supported/default reasoning. Antigravity runs `agy models` or
the resolved executable's `models` subcommand, preserves aligned opaque
slug/display order, and never infers effort from model text. Each fresh attempt
is limited to five seconds and one MiB of retained stdout; stderr/raw
diagnostics are discarded, with no automatic retry, cache, or catalog
persistence. Fixed failure guidance preserves inherit/current/custom paths.
Only the chosen opaque model/effort value is saved.

Settings pickers and prompts follow the same transient input
lifecycle as all capable input; fallback readers retain printed input and add no
synthetic final picker frame. Nested Escape returns to
the parent silently without a Current/Pending snapshot or cancellation record.
In Language, Persona, Maximum debate rounds, Provider, Model, Custom model, and
Effort, the first valid commit applies the normalized pending value and keeps
the editor active. The next valid commit of that same normalized value returns
one level silently; a different valid value applies, stays active, and re-arms
the comparison. Invalid input, authentication failure, Custom model navigation,
and editor re-entry reset it. This is consecutive semantic commit policy in the
`/settings` controller, not raw-key timing or generic Picker/Prompt behavior.

Save always opens its transient yes/no confirmation. Explicit Cancel and root
Settings Escape share one action: clean settings exit immediately without a
confirmation, while dirty settings open the existing Cancel confirmation. Each
confirmation has changed-only Current/Pending context: only differing fields
appear in both sections, each with a `(Changed)` cue, while no differences in
Save confirmation show `No pending settings changes.` YES commits the requested
action; NO or confirmation Escape returns to the root Settings picker, and
invalid fallback input retries. Ctrl-C or EOF still cancels the whole workflow
without writing. The
submitted `/settings` line has already been cleared with the capable main frame;
its history entry remains. Ordinary-line fallback leaves its printed input
visible, with the same history semantics. `/cancel` is literal model text in the
Custom model prompt.

Choosing a provider starts authentication before changing that agent's pending
route. A failed or cancelled authentication leaves the route candidate
unchanged. Successful authentication is subordinate and returns to Provider;
the route and any missing workspace default remain pending in memory. Escape or
cancellation before confirmed Save leaves configuration bytes unchanged. No
configuration value is persisted before Save is confirmed.

After Save confirmation, the controller freshly gates each distinct effective
provider once before entering the configuration transaction. A stale,
unavailable, unauthenticated, indeterminate, failed, cancelled, or interrupted
status prevents the settings file transaction. Save-time authentication may
durably establish a missing workspace default before that transaction; the
completed post-confirmation default is not rolled back if later Save gating or
transaction work stops. Save then validates the complete pending pair and rejects any
exact-byte change to either configuration document or any persona-catalog
change since opening.
Only changed documents are canonically replaced, in system-defaults then
agent-configs order. A write failure attempts reverse-order restoration of
every attempted target, including the failing target; rollback uncertainty is
reported separately. Success returns to the main prompt and applies to the next
shell/backend construction only. It does not refresh the active backend or
runtime, invoke a story-model turn, alter session/explicit overrides, or add
credentials, persisted catalog data, or automatic save.

Entering or validating a value remains in-memory only; no configuration value
is persisted before confirmed Save. This does not change global `/auth` default
persistence or the separately documented post-confirmation Save-time default
persistence. It adds no terminal primitive, public API, or global picker rule;
the #270 one-level Escape and one-shot Save, Cancel, and resume contracts remain
unchanged.

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
contract for a caught exception after CLI workspace initialization or discovery
succeeds, independently of whether a diagnostic handle or file is available.

After attachment, `Conversation scope` appears before the first prompt and is
then repeated only when its session ID, visible conversation handle, subject,
or sticky agent changes. An unchanged panel is suppressed, but the controller
still revalidates any current focus against a fresh complete-catalog read before
every attached prompt. External rename, archive, deletion, and prefix-collision
changes are therefore detected at the same boundary. Detaching resets only
this presentation snapshot, so a later attachment presents scope again. This
controller-local cadence changes no command grammar, terminal API, persisted
selection, conversation storage, or capable/fallback semantics.

Before local result work, accepted request context keeps output interpretable.
`/search` shows its normalized query and active scope; a filtered
`/conversation list` shows its normalized query and active/all scope, while an
unfiltered list adds no request record. After transcript identity resolution,
`/transcript` shows the visible conversation handle and effective start/count
before reading the page. These safe semantic records remain visible when later
work returns empty, fails safely, or is cancelled; fallback may therefore show
both its original command and the normalized result context without repeating
the same verbatim snapshot.

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

`/help [COMMAND]` (`/?`), `/status`, `/auth`, `/settings`, and `/session list` are global.
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
| `/run --output PART/PATH.md PROMPT` or `/run --output PART/PATH.md` then `/send` | One bounded snapshot of current focus. |
| `/run --fresh --output PART/PATH.md PROMPT` | Records focused identity; sends no transcript. |
| `/draft-file PATH` | Strict-UTF-8 draft plus one focused snapshot; prompts for `PART/PATH.md`. |

After prompt and path validation, inline, fresh, and committed composer runs
write one exact-body `you → consensus` message before the `Consensus request`
record and before progress. Every request record names the validated `Output`.
`/draft-file` instead adds only its validated `Source` path: loaded draft bytes
are never echoed as a terminal message. Prompt messages and request context
remain visible through later debate success, deadlock, handled failure,
cancellation, or post-acceptance interrupt. Ordinary-line fallback retains its
already printed prompt text and receives no duplicate message; the distinct
output/source context is still written. None of this changes `story run` or
other noninteractive output.

In both `/run --output ...` and `/run --fresh --output ...`, option boundaries
use ASCII space or tab only. Unicode whitespace cannot separate `--output` from
its path or the path from its prompt. Attached to the flag it causes a parse
error; captured in the path token it is not silently normalized and fails path
validation. Both fail before provider or artifact work.

After leading `--fresh`, the only accepted second leading long option is exact
`--output`. Another `--...` token fails parsing; a single-hyphen word or
ordinary text begins the prompt, and later `--...` text remains literal prompt
content.

When interactive `--output` is omitted, the controller first prompts for a safe
create-only `PART/PATH.md` beneath `manuscript/`; invalid input retries, cancel
returns to the main prompt, and EOF exits normally. The noninteractive
`--output` option is required by argument parsing. Runs revalidate scope and may
write artifacts, never transcripts. Main Ctrl-C
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
It requires `--output PART/PATH.md`; it never generates an output name, and a
single-component filename is invalid. Exact path and provenance rules are in
[Canonical writing artifacts](writing-artifacts.md).

Exits: `0` success or normal shell exit; `1` failed smoke invariant; `2`
usage/setup/fatal shell; `3` noninteractive deadlock; `4` noninteractive
failure. See [Consensus runs](../guides/consensus-runs.md) and
[Terminal safety](../operations/terminal-safety.md).
