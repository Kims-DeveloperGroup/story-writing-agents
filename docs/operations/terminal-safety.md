# Terminal display safety

> **Availability:** these rules protect the module-supported terminal and the
> installed shell, including search hits and transcript events. They do not
> make a terminal, callback, or scrollback private.

Key terms: **TTY guard**—check for interactive streams; **typed error**—trusted
public code instead of exception prose; **display escaping**—showing a control
as text; **primitive renderer**—a writer that owns presentation, not search or
storage; **redaction**—replacing caller-supplied secrets.

## The TTY guard comes first

After `argparse` accepts bare or `chat` entry, standard input and output must
both report that they are TTYs. A missing, false, or failing `isatty()` result
writes one sanitized `tty_required` record to standard error and exits `2`.
It reads no input and performs no interactive option validation, workspace or
CLI-context access, backend construction, or controller work.

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

Every terminal writer sends one sanitized string to its borrowed callback and
adds one final LF. A record may retain internal LFs. Workspace names and
conversation subjects are forced inline, so their input LF displays as
`\x0a`. Search snippets are also inline. Width wrapping uses stable hanging
indentation and never turns content into a command.

Transcript content follows a stricter primitive boundary: it must be an exact
nonempty string, but whitespace-only content is valid. Sanitization makes
controls visible while preserving source LFs. Internal empty lines and a
trailing LF remain lines under `[content] `, and the complete event is still
one callback record. Events are never joined, trimmed, or split into storage
records by the terminal.

Search and transcript renderers validate exact primitive types, safe handles
and turn IDs, positive sequences, nonblank metadata, and complete optional
citation fields. They do not receive service objects, open files, retain a
cursor, or infer trust from text. Exact templates live in the
[terminal reference](../reference/interactive-terminal.md#exact-search-and-transcript-records).

This is plain-text safety, not trusted rendering. Markdown is preserved but
not interpreted. The component does not detect color, width, or terminal
features. The callback, emulator, multiplexer, remote session, or scrollback
may retain or transform everything displayed.

## Typed errors, never raw diagnostics

CLI and controller boundaries classify failures by exception type and public
session state. They never display exception messages, owner tokens, paths, or
other raw diagnostics. Parser errors may supply parser-owned command-token copy
and suggestions.

`write_error()` accepts a trusted error and explicit secret strings. It builds
raw, JSON, visible-escape, collapsed, and control-normalized variants; replaces
longer variants first with `<redacted>`; reduces summary and hint to one line;
caps them at 512 and 256 characters; then sanitizes and wraps the record.
Redaction covers supplied variants, not unknown credentials.

Conversation failures distinguish invalid, missing, ambiguous, archived, and
transcript-invalid input from unsafe or invalid workspace state. The terminal
never accepts an exception or chooses among those categories. Shell mapping
and lease-confirmation consequences belong to
[Recovery](recovery-and-limitations.md).

## Input and process-local state

Display escaping does not change text returned in a `TerminalAction`.
Composition keeps exact LF-joined lines only for one `compose()` call; cancel,
Ctrl-C, or EOF returns counts but no text. This is not secure erasure because
callbacks, the terminal, or process memory may retain copies.

The component contacts no provider and stores no search index, transcript, or
history. The controller passes validated results once to primitive writers.
Command-looking snippets, event content, and provider replies are displayed as
content and never reparsed or executed.

Terminal errors are presentation-only, not transcript `runtime_error` events.
Stored runtime diagnostics use separate redaction and a 4096-character cap;
see [Privacy](privacy-and-provider.md#runtime-error-diagnostics).
