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
render. The four deferred composer status records retain their established
construction width until #86.

The internal profiles are fixed ANSI, capable plain, and line fallback. Presence
of `NO_COLOR` disables decorative SGR only; it does not disable safe functional
cursor controls on an otherwise capable terminal. There is no public theme API.

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

Transcript content follows a stricter primitive boundary: it must be an exact
nonempty string, but whitespace-only content is valid. Sanitization makes
controls visible while preserving source LFs. Internal empty lines and a
trailing LF remain lines under the `Content` field, and the complete event is
still one callback record. Events are never joined, trimmed, or split into
storage records by the terminal.

Search/history/transcript renderers validate IDs, sequences, metadata, and
citations. Controller views construct typed semantic components from validated
facts. Raw structures never reach the terminal. Renderers open no file and
retain no cursor. Exact dense-view fields and callback behavior live in the
[terminal reference](../reference/interactive-terminal.md#exact-search-and-transcript-records).

#83 routes orientation, interactive status/inspection, central errors/recovery,
and accepted management, advisory, retrieval, and consensus output through the
semantic renderer. #84 does the same for full/context help, roster,
session/conversation lists, search, transcript, and focused history. The only
generic status records still deferred are four composer calls owned by #86.
The semantic renderer produces the same structured text in plain and fixed ANSI
profiles: it sanitizes dynamic content before wrapping or styling, counts ANSI
as zero display cells, uses local resets, and permits only reset, bold emphasis,
and foreground SGR 31 through 37. It emits no background, dim, blink, OSC,
query, or cursor sequence. Removing those allowed decorative sequences yields
the exact plain structure.

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
and clear-to-end controls for functional editing. Prompt, buffer, and suggestion
text is sanitized first, with embedded LF visible. No untrusted text becomes
ANSI. Emulators, multiplexers, remote sessions, and scrollback may still retain
or transform display.

## Typed errors, never raw diagnostics

Input/action-output `OSError` maps to `operation_interrupted`; input
`RuntimeError` maps to `internal_error`. Both exit `2` after cleanup. Progress
`OSError`/`KeyboardInterrupt` maps to the former; other callback failure to the
latter. Failed diagnostics never skip cleanup.

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
terminal selects the main or composition suggestion provider for each read;
the driver retains no provider or operation mode. The terminal routes completed
main and composition reads explicitly, without inspecting prompt text, while
subject and confirmation input remains on the ordinary reader. The standalone
line-editor compatibility facade delegates to this same driver implementation.

An active read disables canonical input and echo, keeps a Unicode
code-point buffer, and supports insertion, Left/Right, Home/End,
Backspace/Delete, Enter, and configured VINTR/VEOF. On main input, Up/Down also
navigate the driver's history; on composition they remain history-free. Enter
submits; VEOF submits nonempty text or returns EOF when empty; VINTR returns
interrupt. Submission clears the panel; failure attempts it; both restore
captured attributes. A successful live main commit is appended only after
display cleanup and terminal restoration complete. Active failures propagate
after restoration; restoration uncertainty is fatal `internal_error`. See the
exact
[main](../reference/interactive-terminal.md#main-prompt-live-suggestions) and
[composition](../reference/interactive-terminal.md#composition-live-suggestions)
tokens.

Preview neither completes, selects, nor dispatches. It calls no parser, story
provider, runtime, filesystem, persistence, subprocess, storage, or network.
It changes only call-local editor/terminal state; the exact buffer is passed
once to the terminal/controller boundary. Main command previews remain
registry-backed. Composition preview is the fixed ordered `/send`, `/cancel`
set, and whitespace or a nonmatch clears it.

The current main preview is not an open selectable overlay. Up/Down therefore
navigate history even while `[suggestions]` is visible. Tab, Shift-Tab, and
Escape have no current completion action. The pure overlay-open transition seam
suppresses history arrows for future #79 integration but is not connected to
the current preview.

## Other input and process-local state

Escaping does not change a returned `TerminalAction`. Composition keeps exact
LF-joined lines per call; discard returns counts, not text. This is not secure
erasure because terminal/process memory may retain copies.

The driver stores at most 100 exact nonblank completed main buffers. Invalid
commands, duplicates, `/quit`, fallback results, and nonempty configured VEOF
commits are eligible because storage occurs at the completed read boundary
before the parser. Empty/blank input, Ctrl-C, empty VEOF, physical stream EOF,
invalid fallback framing, and failed operations are excluded. Composition,
subject, and confirmation input never navigate or mutate the store. The store
has no filesystem, environment, transcript, configuration, provider-session,
or user-history-file representation and ends with its driver instance. The
component stores no search index or story transcript. Command-looking content
and replies are never reparsed or executed.

Terminal errors are presentation-only, not transcript `runtime_error` events.
Stored runtime diagnostics use separate redaction and a 4096-character cap;
see [Privacy](privacy-and-provider.md#runtime-error-diagnostics).
