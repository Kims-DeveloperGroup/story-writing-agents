# Terminal display safety

> **Availability:** these rules protect shell live editing, migrated semantic
> output, and established package-supported terminal records. They do not make
> scrollback private.

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

Capability observation evaluates input and output independently. Live raw mode
requires a complete POSIX `termios` interface, a nonempty non-`dumb` `TERM`,
capable input and output TTY descriptors, a readable input, a writable/flushable
output, and reliable size access. Missing, non-TTY, `TERM=dumb`, non-POSIX, or
uncertain capability selects safe ordinary-line fallback without raw mode or
cursor controls. This component fallback does not bypass the installed CLI's
earlier two-stream TTY guard.

A successful ordinary-line fallback main read is eligible for the same
driver-local history store. The fallback itself provides no component-owned
live Up/Down navigation; if a later read on that driver becomes capable, its
live editor can recall the earlier commit.

The output descriptor supplies width. During capability observation, a missing,
failing, nonpositive, or invalid result falls back to 80 columns; a reliable
result is capped at 120. Live-input setup rechecks size before raw activation: a
missing or failing query selects ordinary-line fallback, while a returned
nonpositive or invalid column count uses 80. Capability observation and
live-input setup repeat on the next operation, so resize is noticed without a
signal loop. Semantic rendering also asks for current capabilities on its next
render. Composer/confirmation input and composer lifecycle notices therefore
use the current capability width on their next render. The internal live
picker observes capability and width on every redraw and limits its contiguous
viewport to seven choices; its line fallback renders the complete list.

The internal profiles are fixed ANSI, capable plain, and line fallback. Presence
of `NO_COLOR` disables decorative SGR only; it does not disable safe functional
cursor controls on an otherwise capable terminal. There is no public theme API.

For one advisory turn, the same capability result also gates the narrow
terminal-owned progress indicator. A capable raw stream may show the fixed
four-glyph `◐`/`◓`/`◑`/`◒` cycle; `NO_COLOR` leaves those plain glyphs enabled.
Callback construction and non-TTY, `TERM=dumb`, non-POSIX, missing, or
uncertain-capability paths await the operation silently and emit no indicator.
The installed CLI's earlier TTY guard still rejects non-TTY shell entry before
this component fallback can be used.

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

The live driver separately emits only fixed carriage-return, cursor-up/right,
and clear-to-end controls for functional editing. During the one advisory
indicator lifecycle it may also emit the fixed-cell carriage-return and
clear-to-end sequence, clearing before persistent output and redrawing while
the joined operation is pending. Semantic input context and its focused prompt
arrive as one trusted fixed-theme projection; the dynamic buffer and suggestion
text are sanitized separately before redraw, with embedded LF visible. No
untrusted text becomes ANSI. Emulators, multiplexers, remote sessions, and
scrollback may still retain or transform display.

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
error panel containing the trusted code, summary, and hint. The panel remains
one callback record. Redaction covers supplied variants, not unknown
credentials.

Conversation failures distinguish invalid, missing, ambiguous, archived, and
transcript-invalid input from workspace state. The terminal never classifies
exceptions. Shell and access-claim consequences belong to
[Recovery](recovery-and-limitations.md).

<a id="live-editor-boundary"></a>

## Live-editor boundary

Live mode requires POSIX `termios`, capable TTY streams/descriptors and size,
and nonempty, non-`dumb` `TERM`. Missing capability switches to the existing
reader before editor I/O or terminal mutation. At this component boundary,
that includes `TERM=dumb`, non-TTY, and non-POSIX cases; their ordinary
line-input fallback shows no suggestion panel. The installed CLI's earlier
dual-TTY guard still exits before editor construction. Recoverable activation
failure restores first, then falls back.

One unified driver instance is the sole owner of raw terminal resources. The
terminal supplies the canonical completion provider and semantic palette
renderer to the supported main read, or the composition/empty-confirmation
suggestion provider and semantic input renderer to a history-free read. Its
package-internal picker adapter supplies picker transitions, viewport
projection, and live/fallback renderers to the driver's dedicated picker read;
its cancellable-prompt adapter supplies the settings free-text/numeric route.
Every collaborator is call-local; the driver retains no provider, renderer, or
operation mode. The terminal routes completed main, composition,
confirmation, picker, and settings prompt reads explicitly, without inspecting
prompt text, while subject input remains on the ordinary reader. The standalone
line-editor compatibility facade delegates to this same main reader and driver
implementation.

An active read disables canonical input and echo, keeps a Unicode code-point
buffer, and supports insertion, Left/Right, Home/End, Backspace/Delete, Enter,
and configured VINTR/VEOF. The sole reader incrementally decodes one byte at a
time when a byte buffer is available, so split UTF-8 remains one text event.
After Escape it waits through `select()` for descriptor readiness for a fixed
50 milliseconds, preserves established CSI and SS3 key mappings, and
reprocesses an ordinary following code point after emitting standalone Escape.
It creates no second reader, thread, or sleep loop.

On main input, closed-palette Up/Down navigates the driver's history. An open
palette instead owns Up/Down highlight movement, Tab completion, and Escape
dismissal/restoration; Shift-Tab has no action. Composition and confirmation
remain history-free, and Up/Down, Tab, Shift-Tab, and Escape are no-ops there.
The separate internal picker uses Up/Down for disabled-skipping clamped
highlight movement, Enter to commit, and Escape to cancel; edit and completion
keys are no-ops. The settings prompt uses ordinary line editing, but Escape has
a distinct nested-cancel outcome. Neither route consults history or completion
state.
Enter submits; VEOF submits nonempty text or returns EOF when empty; VINTR
returns interrupt.
Submission clears the panel; failure attempts it; both restore captured
attributes. A successful live main commit is appended only after display
cleanup and terminal restoration complete. Active failures propagate after
restoration; restoration uncertainty is fatal `internal_error`. See the exact
[main](../reference/interactive-terminal.md#main-prompt-live-suggestions) and
[composition](../reference/interactive-terminal.md#composition-live-suggestions)
tokens.

Main palette projection, filtering, highlight, replacement, snapshot restore,
and dismissal call no parser, story provider, runtime, filesystem, persistence,
subprocess, storage, or network. They change only call-local editor/terminal
state; the exact visible buffer is passed once to the terminal/controller
boundary. Canonical command grammar supplies only static root, subcommand, and
finite fixed-option candidates. Composition remains a preview-only fixed
ordered `/send`, `/cancel` set, and whitespace or a nonmatch clears it.

Before input, composition and confirmation each build one ordered semantic view
whose final control is focused. The renderer is pure and produces equivalent
plain/fixed-theme text at the observed width. Unsupported or safely recoverable
setup passes that plain projection to the fallback reader once. A capable live
read uses the trusted fixed-theme projection and keeps its renderer, buffer,
cursor, and preview state call-local. Confirmation supplies no suggestions.

`/settings` uses the same renderer and sole driver for one top-level or nested
picker at a time and for history-free text/numeric prompts with the current
settings panel as context. Picker Escape returns from a nested editor or
cancels at top level according to controller-owned focus. Prompt Escape returns
from the nested editor; Ctrl-C and EOF cancel the whole workflow. No settings
input is added to main history or command completion. Settings alone opts into
transient picker/prompt reads: capable streams clear the transient rows before
returning, callback and line fallback cannot erase and therefore omit duplicate
synthetic picker final frames, and nested Escape returns silently without a
snapshot or cancellation record. The controller emits one complete
Current/Pending snapshot immediately before each Save attempt or whole-flow
cancellation. The default non-settings picker/prompt lifecycle remains
unchanged.

Capable main reads render the existing semantic `SuggestionPalette` in plain or
fixed ANSI at the active width. `NO_COLOR` removes only decoration. Opening
sets the first highlight; open-palette Up/Down clamps highlight without
history movement, Tab replaces the full cursor-active token without whitespace,
and Escape restores the first pre-completion snapshot and dismisses that exact
line state. Plain fallback bypasses the completion provider, palette projector,
and renderer completely; it receives the prompt once and returns one line.

The internal picker has its own safe fallback grammar: exact `/cancel`, blank
for the current enabled highlight, or an in-range enabled ASCII decimal ordinal
without a leading zero. Every other line is rejected and rerendered with one
exact validation notice. Callback, non-TTY, `TERM=dumb`, non-POSIX, uncertain
capability, and recovered activation paths use this full numbered fallback; it
does not acquire raw input or expose choice identities. See the exact
[picker contract](../reference/interactive-terminal.md#generic-internal-picker-foundation).

The cancellable settings prompt uses ordinary one-line fallback: an exact ESC
line cancels that nested editor, while `/cancel` is literal data. Enter commits
the exact line, KeyboardInterrupt is interrupt, and EOF remains distinct.

A default picker commit or cancel clears/redraws and writes exactly one final
semantic `completed` or `cancelled` frame. Configured VINTR/Ctrl-C and
VEOF/physical EOF return typed internal outcomes after clearing the transient
display, without a final picker frame. Settings opts into the transient
lifecycle: capable reads clear before returning, and fallback readers omit the
synthetic final picker frame they cannot erase. Read, render, write, flush, or
decoding failures likewise attempt display cleanup and restore captured
attributes before propagating; restoration uncertainty remains fatal. No second
reader, thread, sleep loop, history store, or completion store is introduced,
and non-settings calls retain the default behavior.

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
component stores no search index or story transcript. Command-looking content
and replies are never reparsed or executed.

Terminal errors are presentation-only, not transcript `runtime_error` events.
Stored runtime diagnostics use separate redaction and a 4096-character cap;
see [Privacy](privacy-and-provider.md#runtime-error-diagnostics).
