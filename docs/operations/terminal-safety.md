# Terminal display safety

> **Availability:** these rules protect shell live editing, migrated semantic
> output, established package-supported terminal records, and the unlisted
> supplied-text ranged viewer. Provider-bound `$`/`$$` editing now invokes that
> private viewer through the controller, without adding a standalone command or
> public API. These rules do not make scrollback or process memory private.

Key terms: **TTY guard**—check for interactive streams; **typed error**—trusted
public code instead of exception prose; **display escaping**—showing a control
as text; **primitive renderer**—a writer that owns presentation, not search or
storage; **raw driver**—the sole real-CLI owner of streams, terminal mode,
functional controls, and restoration; **redaction**—replacing caller-supplied
secrets.

## The TTY guard comes first

After `argparse` accepts bare or `chat`, standard input/output must report TTYs.
Missing, false, or failing `isatty()` writes sanitized `tty_required` to stderr
and exits `2`, before input, interactive option validation, workspace/context,
backend, or controller work.

Help and syntax errors finish in `argparse` before this guard. Standard error
need not be a TTY. See the [CLI stream contract](../reference/cli.md#interactive-streams).

## Unified driver and capability profiles

After the guard, one package-internal factory constructs one stream-backed
`InteractiveTerminal` around one `RawTerminalDriver`. The CLI does not create
or inject a separate line editor. The public standalone
`InteractiveMainLineEditor` compatibility facade delegates to the same driver
implementation and contains no second raw-mode, key, cursor, width, write,
flush, or restoration implementation. Constructing either callback terminal or
driver retains collaborators without reading, writing, flushing, inspecting a
stream, or changing terminal attributes.

Each stream terminal or compatibility editor constructs one driver, and that
driver owns one isolated bounded main-command history store alongside its raw
resources. The callback-only `InteractiveTerminal` and controller own no
history. Construction still performs no I/O, and the store is never persisted.

Capability observation evaluates input and output independently. While input is
`UNDECIDED`, live raw mode requires a complete POSIX `termios` interface, a
nonempty non-`dumb` `TERM`, capable input and output TTY descriptors, a readable
input, a writable/flushable output, and reliable size access. Missing, non-TTY,
`TERM=dumb`, non-POSIX, or uncertain capability makes that first input use and
permanently select safe ordinary-line `FALLBACK` without raw mode or cursor
controls. This component fallback does not bypass the installed CLI's earlier
two-stream TTY guard.

A successful ordinary-line fallback main read is eligible for the same
driver-local history store. The fallback itself provides no component-owned
live Up/Down navigation, and the same driver never upgrades from `FALLBACK` to
`LIVE`. The entry remains in that process-local store, but users cannot recall
it through that driver's fallback input UI.

The output descriptor supplies width. During capability observation, a missing,
failing, nonpositive, or invalid result falls back to 80 columns; a reliable
result is capped at 120. Live-input setup rechecks size before raw activation: a
missing or failing query selects ordinary-line fallback only from
`UNDECIDED`, while a returned nonpositive or invalid column count uses 80. An
already-`LIVE` driver repeats capability, configuration, and geometry
validation before each new input operation; resize is accepted and observed,
while unsafe drift or setup failure raises rather than downgrading to fallback.
An already-`FALLBACK` driver reuses its retained line-only input capabilities
and does not probe for a live upgrade. Independently, semantic output rendering
asks for current output capabilities on its next render; that can update record
width without changing the input mode. Composer/confirmation live input uses
the revalidated `LIVE` width, fallback input keeps its retained line-only
profile, and composer lifecycle notices use the freshly observed semantic-
output width. The internal live picker observes capability and width on every
redraw and limits its contiguous viewport to seven choices; its line fallback
renders the complete list.

The internal profiles are fixed ANSI, capable plain, and line fallback. The
application color gate keeps ANSI eligible when `NO_COLOR` is unset, empty,
whitespace-only, or whitespace-trimmed exact `0`; `NO_COLOR=0` is therefore an
explicit color-enabled default. Any other value, including a nonzero/truthy
value, non-string value, or uncertain environment read, disables decorative
SGR. The runtime reads this value but does not overwrite or export it. Color
selection does not disable safe functional cursor controls on an otherwise
capable terminal, and there is no public theme API.

The internal viewer uses those same capability facts but has a distinct
interaction. A capable stream uses raw input and one alternate screen; an
incapable or safely unavailable pre-activation setup uses the complete numbered
line fallback. The driver observes bounded output width and height before the
first viewer frame and after every nonexit key. It installs no `SIGWINCH`
handler or background resize loop, and it never hides or shows the cursor.

For one advisory turn, the same capability result also gates the narrow
terminal-owned progress indicator. A capable raw stream may show the fixed
four-glyph `◐`/`◓`/`◑`/`◒` cycle; `NO_COLOR` leaves those plain glyphs enabled.
Callback construction and non-TTY, `TERM=dumb`, non-POSIX, missing, or
uncertain-capability paths await the operation silently and emit no indicator.
The installed CLI's earlier TTY guard still rejects non-TTY shell entry before
this component fallback can be used.

## Startup presentation and native sign-in handoff

Startup uses a separate bounded policy from the advisory indicator. Only a
capable POSIX ANSI terminal with decorative SGR and no
`STORY_REDUCED_MOTION` entry receives the four animated welcome frames. Physical
width selects the projection: 48+ columns use the clean five-row ASCII logo,
32–47 use the compact spaced identity, and below 32 use the short identity.
The completed wide projection is nine rows with a 44-cell maximum logo width;
it has no bracketed semantic labels or wall-to-wall rules. The three changes
are exactly 0.4 seconds apart. The final frame remains in scrollback before
native provider authentication. There is no alternate screen, cursor hiding,
general animation service, signal loop, or unbounded repetition.

The live driver reserves the rightmost physical cell and reobserves width for
each frame; static stream output uses its configured safe width, while callback
construction uses its configured width. A present `STORY_REDUCED_MOTION` entry
is independent of color: on an otherwise ANSI-eligible TTY it emits one final
colored frame without animation, sleep, raw-mode activation, cursor control,
or clearing. A nonzero/truthy `NO_COLOR`, non-TTY, `TERM=dumb`, non-POSIX,
callback, or uncertain-output path emits one width-appropriate plain final
frame without delay or ANSI.

Native sign-in guidance and result notices each use one fixed 0.8-second
capable frame. Capable startup preserves exact terminal restoration and
type-ahead discard safety; static and callback paths perform no terminal
mutation. No provider output, account facts, URLs, tokens, credentials, or
executable diagnostics enter the welcome.

Every capable frame reobserves width and reserves one right-margin column. The
driver disables echo and canonical input only while it owns the frame, accepts
no input, clears only confirmed rows, resets SGR, discards type-ahead, and
restores the exact captured terminal attributes before launching `codex login`
or the resolved Antigravity executable with no arguments (bare `agy` by
default). Native login then inherits the normal terminal directly. A
failure or process control still attempts frame cleanup and restoration;
uncertain restoration remains fatal rather than handing a damaged terminal to
the provider.

All welcome and authentication copy is closed, semantic, and sanitized. The
wordmark uses fixed bold 16-color foreground spans—cyan `S`, magenta `T`,
yellow `O`, blue `R`, and green `Y`—with local resets only. Color is
decorative, and removing SGR preserves the exact ASCII text topology. The
wordmark is original; Oh My Zsh and FIGlet are research inspiration only, not
runtime dependencies or copied branding. See the
[terminal reference](../reference/interactive-terminal.md#startup-welcome-and-authentication)
and [native authentication privacy boundary](privacy-and-provider.md#native-authentication-boundary).

## Safe display boundary

`sanitize_terminal_text()` preserves ordinary Unicode, Markdown characters,
and LF. It replaces every other Unicode `Cc`, every `Cf` and `Cs`, and
U+2028/U+2029 with a visible lowercase escape:

| Code point | Display form |
| --- | --- |
| U+0000–U+00FF | `\xhh` |
| U+0100–U+FFFF | `\uhhhh` |
| Above U+FFFF | `\Uhhhhhhhh` |

For example, `ESC[31m` displays as `\x1b[31m` and cannot act as an ANSI escape.
The result is strict UTF-8, and sanitizing it again changes nothing.

Established plain record writers and the semantic component adapter each send
one sanitized rendered string and final LF. Records may retain internal LF;
inline values display it as `\x0a`. Wrapping never creates commands.
Semantic composition and confirmation input frames are distinct: their plain
and fixed-theme projections share line topology, end in the focused prompt's
required space, and contain no final LF.
Picker projections sanitize prompt, labels, descriptions, disabled reasons,
and notices before measurement or styling. Opaque choice identities never
enter rendered text. A selected choice outside the live viewport is summarized
only by its ordinal and sanitized label; line fallback renders every numbered
choice and ends with its focused prompt space without a final LF.

Viewer validation precedes capability inspection and terminal I/O. Supplied
text and its one-line display name must be exact strict-UTF-8 `str` values; text
is limited by a private terminal-local 8 MiB UTF-8 capacity and 65,536
LF-derived logical lines, and an optional initial line must identify a derived
line. The line count is rejected before splitting or materializing the line
tuple. A separate complete sanitized fallback projection is limited to 16 MiB
UTF-8, including display metadata, numbered records, separators, notices, and
prompt; exact limits are accepted. Escape expansion can therefore reject an
otherwise valid source. All of these checks precede callbacks, reads, writes,
capability inspection, and raw-terminal work. LF alone separates logical lines,
empty text has zero, and one final LF adds no extra line. CR, U+2028, and other
controls remain content and render as visible escapes.

In live mode, each logical line uses one physical row and is clipped with a
Unicode-cell-aware ellipsis rather than wrapped. Sanitization and measurement
scan only the width-relevant prefix plus a fixed 256-source-code-point
allowance, so an unseen multi-MiB suffix or a leading zero-width combining run
cannot impose unbounded work on every redraw. In line fallback, every accepted
sanitized character is emitted without width clipping; long records wrap
naturally in terminal scrollback.

Transcript content follows a stricter primitive boundary: it must be an exact
nonempty string, but whitespace-only content is valid. Sanitization makes
controls visible while preserving source LFs. Internal empty lines and a
trailing LF remain lines under the `Content` field, and the complete event is
still one callback record. Events are never joined, trimmed, or split into
storage records by the terminal. When an `agent_reply` is displayed, a
presentation-only formatter recognizes only a complete strict JSON object or
array. It rejects duplicate object keys and non-finite constants, emits
accepted values with two-space indentation, preserves object-key order, and
does not ASCII-escape Unicode. Malformed JSON, embedded prose or extra data,
scalar JSON values, duplicate keys, non-finite values, and serialization
failures return the original content exactly; no formatter result is written
back to the transcript or provider result.

Search/history/transcript renderers validate IDs, sequences, metadata, and
citations. Controller views construct typed semantic components from validated
facts. Raw structures never reach the terminal. Renderers open no file and
retain no cursor. Exact dense-view fields and callback behavior live in the
[terminal reference](../reference/interactive-terminal.md#exact-search-and-transcript-records).

#83 routes orientation, interactive status/inspection, central errors/recovery,
and accepted management, advisory, retrieval, and consensus output through the
semantic renderer. #84 does the same for full/context help, roster,
session/conversation lists, search, transcript, and focused history. #86 routes
composer lifecycle notices plus composition and confirmation input through the
same foundation; no internal generic composer-status call remains. #87 uses
that safe renderer for bounded live and complete numbered picker projections
without installing a shell consumer. The semantic renderer produces the same
structured text in plain and fixed ANSI profiles: it
sanitizes dynamic content before wrapping or styling, counts ANSI as zero
display cells, uses local resets, and permits only reset, bold emphasis, and
foreground SGR 31 through 37. The `AGENT` role uses bold with the terminal's
default foreground and does not inspect or select a background. The renderer
emits no background, dim, blink, OSC, query, or cursor sequence. Removing those
allowed decorative sequences yields the exact
plain structure.

The separate viewer renderer keeps the same safety order and may additionally
use fixed inverse SGR 7 for selected rows, including a fixed bold/inverse/cyan
combination for the active selected row. Its plain `>` and `*` markers retain
the same current/selection meaning when all viewer decoration is removed.

The controller constructs semantic components only from trusted roles/states
and validated facts. Its unlisted `InteractiveSemanticOutput` adapter acquires
no resource when constructed and performs exactly one render and one callback
write for each established component record. It neither reads input nor owns a
second stream, raw mode, cursor, width, or restoration lifecycle.

Dense list commands preserve callback granularity by rendering each row as one
positioned, one-record `ListView`. If search, transcript, or history rendering
is interrupted after completed rows, those callbacks remain visible, one
cancellation record follows, and no unrendered row or trailing result, hint, or
context component is written.

For ordinary live editors, pickers, prompts, and the advisory indicator, the
driver emits only fixed carriage-return, cursor-up/right, and clear-to-end
controls for functional editing. During the one advisory
indicator lifecycle it may also emit the fixed-cell carriage-return and
clear-to-end sequence, clearing before persistent output and redrawing while
the joined operation is pending. Semantic input context and its focused prompt
arrive as one trusted fixed-theme projection; the dynamic buffer and suggestion
text are sanitized separately before redraw, with embedded LF visible. No
untrusted text becomes ANSI. Emulators, multiplexers, remote sessions, and
scrollback may still retain or transform display.

The ranged viewer is the sole separate control vocabulary: fixed
alternate-screen enter, home-and-clear redraw, and alternate-screen exit
sequences surround only sanitized, bounded renderer output. Caller text and
display metadata can never supply these functional sequences.

## Advisory progress boundary

The indicator is advisory-only: it wraps exactly one runtime awaitable for one
advisory turn and is not a semantic record, callback, status event, or general
animation service. The raw driver owns the active glyph, advances the fixed
four-frame cycle, serializes clear/redraw around semantic retrieval/progress or
result writes, and creates no OS thread or import-time task. It joins both the
operation and indicator tasks, then clears the cell before final, cancellation,
or error presentation; the wrapper returns after this cleanup.

An underlying operation failure takes precedence over a simultaneous indicator
failure. If only the indicator or its final clear fails, the driver raises the
internal advisory-progress error after cleanup and the controller maps it to
the existing `internal_error` path. Callback and incapable paths have no
indicator failure surface because they await silently. Advisory turns no longer
write `[routing]` or `[waiting]` notices; their established result, cancellation,
context, and error records remain the authoritative output.

## Typed errors, never raw diagnostics

Input/action-output `OSError` maps to `operation_interrupted`; input
`RuntimeError` maps to `internal_error`. Both exit `2` after cleanup. Observer
progress `OSError`/`KeyboardInterrupt` maps to the former; other callback
failure to the latter. Advisory indicator failures use the internal-error path
described above. Failed diagnostics never skip cleanup.

Other failures use type/public state, never raw exception, token, or path text.

`write_error()` accepts a trusted error and explicit secret strings. It builds
raw, JSON, visible-escape, collapsed, and control-normalized variants; replaces
longer variants first with `<redacted>`; reduces summary and hint to one line;
caps them at 512 and 256 characters; then sanitizes and renders one semantic
error panel containing the trusted code, summary, and hint. An additive
`debug_log_path` keyword accepts only nonblank, one-line, strict-UTF-8 display
text and adds one sanitized `[debug log]` notice. `None` preserves the exact
two-notice panel. The value is call-local presentation input: the terminal does
not resolve, read, write, or retain a filesystem path. The panel remains one
callback record. Redaction covers supplied variants, not unknown credentials.
After workspace resolution, installed CLI and controller errors supply the
fixed text `If available, workspace-relative: debug_logs/runtime.jsonl`.
Pre-workspace errors and direct callers that omit the keyword retain the exact
two-notice panel.

Conversation failures distinguish invalid, missing, ambiguous, archived, and
transcript-invalid input from workspace state. The terminal never classifies
exceptions. Shell and access-claim consequences belong to
[Recovery](recovery-and-limitations.md).

<a id="permanent-input-ownership"></a>

## Permanent input ownership

Each stream-backed driver owns one resource-free input latch. The input-side
state and its external meaning are:

| State | Internal boundary | User-visible behavior |
| --- | --- | --- |
| `UNDECIDED` | Construction has not consumed input or committed a source. Inspection alone does not read, wait, print, or mutate the terminal. | No terminal change. |
| `LIVE` | The first actual read admitted one exact byte source, decoder, controls, and terminal configuration. The same source-correlated decoder serves all later input routes. | Capable main, subject, composition, confirmation, prompt, and picker reads use transient cleared frames; the viewer uses an alternate screen. |
| `FALLBACK` | The first actual read entered the ordinary callback because live input could not be safely established. | All later input remains ordinary line-oriented output, so prompts, typed input, and viewer attempts stay visible. |

The first transition is irreversible. `LIVE` cannot downgrade after setup,
capability, decoder, source, or configuration failure, and `FALLBACK` cannot
upgrade if the environment later becomes capable. This makes one component the
only byte consumer and prevents bytes already decoded into a `TextIOWrapper`
queue from disappearing when a later interaction reads its raw buffer.
External concurrent reads, or mixing another wrapper/raw reader with this
driver, are unsupported.

Before selecting `LIVE`, decoder preflight admits only registry-canonical
ASCII, UTF-8, or ISO-8859-1 with the resolved built-in `strict` or
`surrogateescape` handler. The retained incremental reader consumes exactly one
byte at a time, preserves fragmented code points across interaction boundaries,
and holds at most four decoded code points. Pending decoded input is consumed
before the fixed 50 ms Escape readiness check. Clean EOF remains stable;
partial/malformed EOF and read, decode, or result-shape failures poison later
reads. `KeyboardInterrupt` alone resets incomplete decoder/pending state while
leaving `LIVE` selected. Synchronous reentry is rejected and poisons the
reader.

<a id="live-editor-boundary"></a>

## Live-editor boundary

Live mode requires POSIX `termios`, capable TTY streams/descriptors and size,
and nonempty, non-`dumb` `TERM`. Missing capability switches to the existing
reader only while the latch is `UNDECIDED`, before editor I/O or terminal
mutation. At this component boundary, that includes `TERM=dumb`, non-TTY, and
non-POSIX cases; their ordinary line-input fallback shows no suggestion panel.
The installed CLI's earlier dual-TTY guard still exits before editor
construction. Once `LIVE` is selected, activation failure is cleaned up and
raised rather than starting a fallback read.

One unified driver instance is the sole owner of raw terminal resources. The
terminal supplies canonical static completion, a call-local controller choice
provider, and the semantic palette renderer to the supported main read. The
composition/empty-confirmation routes supply their own semantic input renderer
to history-free reads. Package-internal picker and cancellable-prompt adapters
supply pure transitions and live/fallback renderers to the same driver. Every
collaborator is call-local; the driver retains no provider, renderer, catalog,
or operation mode. Main, composition, confirmation, picker, and prompt reads are
routed explicitly without inspecting prompt text. Conversation-subject input
remains history- and completion-free but uses a cleared capable frame after
`LIVE` selection and the ordinary callback after `FALLBACK`. The standalone
line-editor compatibility facade delegates to this same main reader and driver
implementation.

An active read disables canonical input and echo, keeps a Unicode code-point
buffer, and supports insertion, Left/Right, Home/End, Backspace/Delete, Enter,
and configured VINTR/VEOF. Its persistent exact-byte reader keeps split UTF-8
as one text event even when bytes arrive across calls. After Escape it consumes
any pending decoded suffix first, then waits through `select()` for descriptor
readiness for a fixed 50 milliseconds, preserves established CSI and SS3 key
mappings, and reprocesses an ordinary following code point after emitting
standalone Escape. It creates no second reader, thread, or sleep loop.

Malformed CSI (`Escape` + `[`) and SS3 (`Escape` + `O`) input is also bounded.
If a sequence reaches exactly 64 decoded code points—the two-code-point
introducer plus 62 non-final code points—the driver replaces the accumulated
sequence with an identity-only discard state. It retains none of the overlong
payload or arbitrary following tail. The discard consumes input until the first
ANSI final code point (`@` through `~`) or an unconfigured C0/DEL control, and
consumes that terminator without reinterpreting it or a valid-looking suffix as
a new key. The next ordinary input starts cleanly and is handled normally.

Configured VINTR and VEOF are classified before pending or overlong escape
input, and physical EOF keeps its typed outcome. For users, the malformed
sequence is inert: it cannot move or anchor a viewer range, recall history,
open or apply completion, delete text, or commit either the viewer selection or
main input. A following genuine navigation, editing, or commit key still works.

On main input, closed-palette Up/Down navigates the driver's history. An open
palette instead owns Up/Down highlight movement, Tab completion, and Escape
dismissal/restoration; Shift-Tab has no action. A controller-owned resume-choice
palette makes Tab a no-op and Enter commit the highlighted complete explicit
command immediately. Static highlighted Enter only completes, dismisses the
palette, and leaves the read active for a later submit. Composition and
confirmation remain history-free, and Up/Down, Tab, Shift-Tab, and Escape are
no-ops there.
The separate internal picker uses Up/Down for disabled-skipping clamped
highlight movement, Enter to commit, and Escape to cancel; edit and completion
keys are no-ops. The settings prompt uses ordinary line editing, but Escape has
a distinct nested-cancel outcome. Neither route consults history or completion
state.
Enter submits when no highlighted static completion is open; VEOF submits
nonempty text or returns EOF when empty; VINTR returns interrupt.

Every input route, including the ranged viewer, uses the same admitted live
source and decoder. On the first input call, every other or unknown codec,
including ISO-2022 variants, HZ, UTF-7, UTF-8-SIG, and
`raw-unicode-escape`, selects permanent ordinary fallback before
`PC_VDISABLE`, control attributes, byte reads, termios activation, screen
entry, or live-terminal output. This is a positive allow-policy, not a deny-list
of those examples.

Within an allowed byte codec, each configured VINTR/VEOF raw byte is first
compared with `PC_VDISABLE`. An enabled byte must then decode to exactly one
code point and encode back to the exact byte under the stream's encoding and
error policy. UTF-8 `surrogateescape` controls can remain live; strict
incomplete bytes, lossy handlers, and incompatible mappings select ordinary
fallback only while the latch is `UNDECIDED`; after `LIVE`, changed mappings
are drift errors. Text-only collaborators keep their supplied one-code-point
control semantics. Within a live viewer event,
configured VINTR has priority over VEOF, and both precede ordinary key
meanings.

Before each later live operation, the driver revalidates the exact byte source,
decoder specification, live capability features, and terminal configuration.
Width and height may change normally, and local-flag drift limited to `PENDIN`
is tolerated because platforms may change that flag asynchronously. Other
source, decoder, capability, or terminal-configuration drift fails the
operation without changing `LIVE` or invoking the fallback callback.

A complete live frame comprises every row and terminal cell drawn for one
capable input read: semantic context and prompt, editable buffer, suggestions or
choices, validation, and redraws. Before any capable main, subject,
composition, confirmation, picker, or prompt read returns on submit/commit, cancel,
interrupt, or EOF—or propagates a read, render, write, flush, or decode
failure—the driver attempts to clear that entire frame and restore all captured
attributes. A successful live main commit is appended only after cleanup and
restoration complete. Active failures propagate after restoration; restoration
uncertainty is fatal `internal_error`. Completion-only Enter has not returned,
so its frame remains active and no history entry exists yet. See the exact
[main](../reference/interactive-terminal.md#main-prompt-live-suggestions) and
[composition](../reference/interactive-terminal.md#composition-live-suggestions)
tokens.

Main palette projection, filtering, highlight, replacement, snapshot restore,
and dismissal call no parser, story provider, runtime, filesystem, persistence,
subprocess, storage, or network. They change only call-local editor/terminal
state; the exact visible buffer is passed once to the terminal/controller
boundary. Canonical command grammar supplies only static root, subcommand, and
finite fixed-option candidates. For exact omitted resume commands, the
controller alone reads catalogs and supplies disposable ordered rows; the
terminal neither authorizes nor mutates them. The controller freshly validates
a committed choice before lifecycle/default/focus/routing changes. Composition
remains a preview-only fixed ordered `/send`, `/cancel` set, and whitespace or a
nonmatch clears it.

Before input, composition and confirmation each build one ordered semantic view
whose final control is focused. The renderer is pure and produces equivalent
plain/fixed-theme text at the observed width. Unsupported or safely recoverable
setup passes that plain projection to the fallback reader once. A capable live
read uses the trusted fixed-theme projection and keeps its renderer, buffer,
cursor, and preview state call-local. Confirmation supplies no suggestions.

Provider authentication and `/settings` use the same renderer and sole driver
for one picker at a time; settings also uses context-free history-free
text/numeric prompts. Picker
Escape returns from a nested editor or
cancels at top level according to controller-owned focus. Prompt Escape returns
from the nested editor; Ctrl-C and EOF cancel the whole workflow. Nested
settings input does not navigate or mutate main history and receives no command
completion. These reads follow the same universal capable cleanup and
honest fallback visibility as every other input route; nested Escape returns
silently without a snapshot or cancellation record. Save and Cancel each open a
transient yes/no confirmation. Its context shows only changed Current/Pending
fields and marks
each displayed value `(Changed)`, and shows `No pending settings changes.` when
there is no diff. Committed YES proceeds; committed NO or nested Escape returns
to the settings picker; invalid fallback input retries with a
trusted notice. Top-level cancellation does not claim a snapshot that was not
rendered.

Capable main reads render the existing semantic `SuggestionPalette` in plain or
fixed ANSI at the active width. `NO_COLOR` removes only decoration. Opening
sets the first highlight; open-palette Up/Down clamps highlight without
history movement. For static suggestions, Tab replaces the full cursor-active
token and adds one ASCII space only when that token ended at the buffer end;
Escape restores the first pre-completion snapshot and dismisses that exact line
state. Dynamic resume choices make Tab a no-op and Escape leave the omitted
buffer unchanged. Plain fallback bypasses the completion/choice providers,
palette projector, and renderer completely; it receives the prompt once and
returns one line.

The internal picker has its own safe fallback grammar: exact `/cancel`, blank
for the current enabled highlight, or an in-range enabled ASCII decimal ordinal
without a leading zero. Every other line is rejected and rerendered with one
exact validation notice. A permanently fallback driver, or a first input call
with non-TTY, `TERM=dumb`, non-POSIX, or uncertain capability, uses this full
numbered fallback; it does not acquire raw input or expose choice identities.
See the exact
[picker contract](../reference/interactive-terminal.md#generic-internal-picker-foundation).
For every submitted omitted resume, the main reader records the omitted command
whether its fresh choice read opens a picker, reports empty, or reports an
error. When eligible rows open a picker, its answer is history-free; the picker
is live and transient on capable streams and numbered in line fallback. Picker
cancellation and rejection before acquisition or default persistence are
mutation-free, but a later
session-transition failure does not promise durable lifecycle/default rollback.
See the [session transition contract](../design/session-lifecycle.md#shell-transitions-and-compensation).
Persistent cancellation/error records are controller output.

The cancellable settings prompt uses ordinary one-line fallback: an exact ESC
line cancels that nested editor, while `/cancel` is literal data. Enter commits
the exact line, KeyboardInterrupt is interrupt, and EOF remains distinct.

Pure picker controls retain internal `completed` or `cancelled` outcome states,
but supported terminal adapters never render a final control frame. Capable
reads clear before every return; fallback readers keep printed prompt/input and
add no synthetic frame. Read, render, write, flush, or decoding failures likewise
attempt complete-frame cleanup and restore captured attributes before
propagating; restoration uncertainty remains fatal. Persistent controller
validation, cancellation, discard, error, orientation, and result records are
separate semantic output and are not erased. No second reader, thread, sleep
loop, history store, or completion store is introduced.

If the output channel itself fails, the driver can only attempt the erase; it
does not claim terminal bytes it could not write. Attribute restoration is
still attempted, and uncertainty about restoration remains fatal.

<a id="artifact-reference-input-boundary"></a>

## Artifact-reference input boundary

Reference discovery is admitted only for the controller-projected
provider-bound span. The private activation layer scans one cursor-active,
unescaped marker and emits only pure completion/request/failure values. It
cannot read a source list or artifact. The controller borrows a fresh sorted
canonical-filesystem-path callback and bounded verified-text callback for that input call;
the driver clears both in `finally`.

Capable picker and viewer input run under the same admitted source, decoder,
raw-mode lifetime, and irreversible `LIVE` latch as their originating input.
They cannot start a parallel read or switch to fallback after live activation.
`FALLBACK` uses numbered ordinary input and a second confirmation before an
amended draft can be submitted. No route treats source completion or viewer
selection as Enter for the outer prompt.

The complete preactivation buffer, cursor, focus, and input mode are immutable
restoration facts. Every cancel, Ctrl-C, EOF, invalid/stale selection, repository
error, or nested operational failure restores them or raises cleanup failure;
none appends history, dispatches a command, calls a provider, or leaves a
marker result behind. A successful replacement changes only the marker span.
Fresh filesystem discovery and verified reads make unsafe listing-to-view drift fail closed.
Later submission independently captures and revalidates the then-current
artifact; it never trusts or reuses displayed bytes. Fixed error output is
content-free and never echoes a path or artifact body.

<a id="ranged-text-viewer-boundary"></a>

## Ranged-text viewer boundary

The package-internal viewer accepts supplied text, not a path or artifact
repository. Its adapter validates exact input types, strict UTF-8, the private
8 MiB source capacity, the 65,536 logical-line capacity, the complete 16 MiB
sanitized-fallback capacity, display metadata, LF-derived line count, and
optional one-based initial line before the terminal can be inspected. It has no
workspace, writing-artifact, repository, runtime, backend, provider,
controller, parser, or composer dependency. The separate artifact-reference
limit remains authoritative; the controller caller applies it before handing
verified text to this viewer.

Up/Down and Shift+Up/Down are pure operations over immutable active/anchor
state. Enter returns only normalized inclusive line numbers. Escape, Ctrl-C,
and EOF return separate content-free noncommit kinds. No result contains the
source, display name, path, reference, viewport, terminal, or composer state.
An empty document cannot commit.

The capable lifecycle has one resource owner and one cleanup order:

1. The driver revalidates the retained `LIVE` source, decoder, controls, and
   terminal configuration, then activates raw mode.
2. It enters the alternate screen once and draws only fixed controls plus safe
   bounded renderer output.
3. A typed outcome or operational failure ends the interaction.
4. It attempts alternate-screen exit once, then termios restoration once.
5. The adapter drops every viewer-local value in `finally`.

Once `LIVE` is selected, setup, read, render, write, flush, decode, or
collaborator failure never opens a second line-fallback interaction. Raw mode
and alternate-screen entry/exit are exactly paired, including partial setup,
and captured termios is restored exactly once. A termios restoration failure
takes precedence over screen-exit failure; screen-exit failure takes precedence
over the active failure. Cursor visibility is untouched, so cleanup neither
emits cursor-hide nor cursor-show controls. The viewer installs no signal
handler or retained timer; resize is observed on the next input key and the
50 ms Escape wait is one descriptor check. If an output failure prevents an
exit sequence from reaching the terminal, cleanup can only report the failure;
it cannot claim that the earlier screen bytes were reversed.

An already selected `FALLBACK` driver, or an `UNDECIDED` driver's first input
when capability or safe preflight is unavailable, prints the complete numbered
sanitized document without width clipping and accepts only exact ASCII positive
`START-END` or `/cancel`. Long lines wrap naturally; accepted fallback content
is never silently truncated. Reversed endpoints normalize. Invalid, empty,
spaced, signed, leading-zero, non-ASCII, or out-of-range input gets a visible
fixed retry. It uses no ANSI, raw mode, cursor movement, or alternate screen,
and cannot erase printed input.

Every open receives a new caller document and optional initial line; no viewer
state can resume. Dropping the call-local immutable values is lifecycle
disposal only. Python objects, streams, emulators, multiplexers, scrollback,
and process memory may retain copies, so this is not secure erasure. Selection
and cancellation both close the independent viewer after resource restoration,
so the user is not left in viewer mode. #162 returns only the content-free
typed result; the #164 controller caller restores the exact originating
buffer/cursor and attaches the path/range indicator. See the
[exact viewer contract](../reference/interactive-terminal.md#independent-ranged-text-viewer)
and [viewer recovery guidance](recovery-and-limitations.md#ranged-text-viewer-exits-and-recovery).

## Other input and process-local state

Escaping does not change a returned `TerminalAction`. Composition keeps exact
LF-joined lines per call; discard returns counts, not text. This is not secure
erasure because terminal/process memory may retain copies.

Composer state is immutable and call-local. Its entry, empty-send error,
successful send, and discard are semantic `open`, `error`, `completed`, and
`cancelled` notices, each with one render and one callback write. Confirmation
invalidity writes one semantic error hint, marks the next choices field as an
error, and retries; a valid/default/interrupt/EOF result writes no final
confirmation event.

The driver stores at most 100 exact nonblank completed main buffers. Invalid
commands, duplicates, `/quit`, fallback results, and nonempty configured VEOF
commits are eligible because storage occurs at the completed read boundary
before the parser. Empty/blank input, Ctrl-C, empty VEOF, physical stream EOF,
invalid fallback framing, and failed operations are excluded. Composition,
subject, confirmation, picker, and settings prompt input never navigate or
mutate the store.
The store
has no filesystem, environment, transcript, configuration, provider-session,
or user-history-file representation and ends with its driver instance. The
component stores no search index or story transcript. Viewer text, display
metadata, selection, viewport, and geometry are likewise never added to this
store and are dropped after each viewer call. Command-looking content and
replies are never reparsed or executed.

Terminal errors are presentation-only, not transcript `runtime_error` events.
Those transcript events use separate redaction and a 4,096-character cap. The
bounded workspace debug log is a third, content-safe operational channel that
does not store exception prose. See
[Privacy](privacy-and-provider.md#runtime-error-diagnostics) and
[workspace debug-log privacy](privacy-and-provider.md#workspace-debug-log-privacy).
