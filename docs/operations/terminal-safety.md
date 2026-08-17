# Terminal display safety

> **Availability:** these rules protect shell live editing and
> module-supported terminal records. They do not make scrollback private.

Key terms: **TTY guard**—check for interactive streams; **typed error**—trusted
public code instead of exception prose; **display escaping**—showing a control
as text; **primitive renderer**—a writer that owns presentation, not search or
storage; **redaction**—replacing caller-supplied secrets.

## The TTY guard comes first

After `argparse` accepts bare or `chat`, standard input/output must report TTYs.
Missing, false, or failing `isatty()` writes sanitized `tty_required` to stderr
and exits `2`, before input, interactive option validation, workspace/context,
backend, or controller work.

Help and syntax errors finish in `argparse` before this guard. Standard error
need not be a TTY. See the [CLI stream contract](../reference/cli.md#interactive-streams).

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

Primitive record writers send one sanitized string and final LF. Records may
retain internal LF; inline values display it as `\x0a`. Wrapping never creates
commands.

Transcript content follows a stricter primitive boundary: it must be an exact
nonempty string, but whitespace-only content is valid. Sanitization makes
controls visible while preserving source LFs. Internal empty lines and a
trailing LF remain lines under `[content] `, and the complete event is still
one callback record. Events are never joined, trimmed, or split into storage
records by the terminal.

Search/history/transcript renderers validate IDs, sequences, metadata, and
citations. Controller-validated management summaries reuse primitives; raw
structures never reach the terminal. Renderers open no file and retain no
cursor. Exact templates live in the
[terminal reference](../reference/interactive-terminal.md#exact-search-and-transcript-records).

Record presentation remains feature-blind plain text. The live editor instead
checks terminal capability/width and emits only fixed carriage-return,
cursor-up/right, and clear-to-end controls. Prompt, buffer, and suggestion text
is sanitized first, with embedded LF visible. No untrusted text becomes ANSI.
Emulators, multiplexers, remote sessions, and scrollback may still retain or
transform display.

## Typed errors, never raw diagnostics

Input/action-output `OSError` maps to `operation_interrupted`; input
`RuntimeError` maps to `internal_error`. Both exit `2` after cleanup. Progress
`OSError`/`KeyboardInterrupt` maps to the former; other callback failure to the
latter. Failed diagnostics never skip cleanup.

Other failures use type/public state, never raw exception, token, or path text.

`write_error()` accepts a trusted error and explicit secret strings. It builds
raw, JSON, visible-escape, collapsed, and control-normalized variants; replaces
longer variants first with `<redacted>`; reduces summary and hint to one line;
caps them at 512 and 256 characters; then sanitizes and wraps the record.
Redaction covers supplied variants, not unknown credentials.

Conversation failures distinguish invalid, missing, ambiguous, archived, and
transcript-invalid input from workspace state. The terminal never classifies
exceptions. Shell and lease consequences belong to
[Recovery](recovery-and-limitations.md).

<a id="live-editor-boundary"></a>

## Live-editor boundary

Live mode requires POSIX `termios`, capable TTY streams/descriptors and size,
and nonempty, non-`dumb` `TERM`. Missing capability switches to the existing
reader before editor I/O or terminal mutation. Recoverable activation
failure restores first, then falls back.

An active read disables canonical input and echo, keeps a Unicode
code-point buffer, and supports insertion, Left/Right, Home/End,
Backspace/Delete, Enter, and configured VINTR/VEOF. Enter submits; VEOF submits
nonempty text or returns EOF when empty; VINTR returns interrupt. Submission
clears the panel; failure attempts it; both restore captured attributes.
Active failures propagate after restoration; restoration uncertainty is fatal
`internal_error`. See the [exact tokens](../reference/interactive-terminal.md#main-prompt-live-suggestions).

Preview calls no parser, provider, filesystem, persistence, subprocess, or
network. It changes only call-local editor/terminal state; the exact buffer is
passed once to the terminal/controller boundary.

## Other input and process-local state

Escaping does not change a returned `TerminalAction`. Composition keeps exact
LF-joined lines per call; discard returns counts, not text. This is not secure
erasure because terminal/process memory may retain copies.

The component stores no search index, transcript, or history. Command-looking
content and replies are never reparsed or executed.

Terminal errors are presentation-only, not transcript `runtime_error` events.
Stored runtime diagnostics use separate redaction and a 4096-character cap;
see [Privacy](privacy-and-provider.md#runtime-error-diagnostics).
