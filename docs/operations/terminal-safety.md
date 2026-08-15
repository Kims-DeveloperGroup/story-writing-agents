# Terminal display safety

> **Availability:** these rules protect the module-supported terminal component
> and the installed session lobby. They do not make a terminal or its scrollback
> private.

Key terms: **TTY guard**—the check for interactive streams; **typed error**—a
trusted public error code instead of exception prose; **display escaping**—
showing a control code as text; **redaction**—replacing caller-supplied secrets.

## The TTY guard comes first

After `argparse` accepts bare or `chat` entry, both standard input and standard
output must report that they are TTYs. A missing, false, or failing `isatty()`
result writes one sanitized `tty_required` record to standard error and exits
`2`. It reads no input and performs no interactive option validation, workspace
or CLI-context access, backend construction, or controller work.

Help and syntax errors finish in `argparse` before this guard. Standard error
does not itself need to be a TTY. Exact stream and exit-code behavior is in the
[CLI reference](../reference/cli.md#interactive-streams).

## Safe display boundary

`sanitize_terminal_text()` preserves ordinary Unicode, Markdown characters,
and LF. It replaces every other Unicode `Cc`, every `Cf` and `Cs`, and
U+2028/U+2029 with a visible lowercase escape:

| Code point | Display form |
| --- | --- |
| U+0000–U+00FF | `\xhh` |
| U+0100–U+FFFF | `\uhhhh` |
| Above U+FFFF | `\Uhhhhhhhh` |

For example, ANSI input `ESC[31m` displays as `\x1b[31m` and cannot act as a
terminal escape. The result is strict UTF-8 and sanitizing it again changes
nothing.

Every terminal writer sends one sanitized string to its borrowed output
callback and adds one final LF. A block may keep internal LFs. Workspace text
inside orientation is forced inline, so an input LF displays as `\x0a`.

This is plain-text safety, not trusted rendering. Markdown is preserved but not
interpreted. The component does not detect color, width, or other terminal
features. A callback or terminal may still record or transform its input.

## Typed errors, never raw diagnostics

CLI and controller boundaries classify failures by exception type and validated
public session state. They pass `InteractiveError` values to the terminal and
never display exception messages, owner tokens, or other raw diagnostics.
Parser-owned safe errors may supply their own summary and suggestion.

`write_error()` accepts a trusted error and explicit secret strings. It:

1. expands each nonempty secret to raw, JSON-escaped, visibly escaped,
   whitespace-collapsed, and control-normalized variants;
2. replaces longer variants first with `<redacted>` before and after escaping;
3. makes summary and hint one trimmed line, using registry defaults if blank;
4. caps summary at 512 characters and hint at 256, including the marker
   ` ...[truncated]`; and
5. wraps trusted `[error CODE]` and `[hint]` prefixes without splitting words.

Redaction covers supplied secrets and derived forms; it is not a credential
detector. The terminal never accepts an exception or infers a code from its
text. A `provider_preflight_failed` hint may suggest broader troubleshooting,
but it is not a report of checks already performed. See the exact boundary in
[Session lifecycle](../design/session-lifecycle.md#entering-the-lobby).

## Input and process-local history

Display escaping does not change input returned in a `TerminalAction`. Sent
composition keeps exact lines joined by LF. Its buffer exists only for that
`compose()` call; cancel, Ctrl-C, or EOF returns counts but no text. This is not
secure erasure because callbacks, the terminal, or process memory may retain a
copy.

The component opens no file, contacts no provider, and stores no history. The
Issue #10 lobby passes `history_count=None` and therefore makes no room-history
claim. Chat dispatch and conversation history remain unavailable.

Terminal errors are presentation-only, not conversation `runtime_error`
events. Stored runtime diagnostics use a different automatic redaction set and
4096-character cap; see [Privacy and provider exposure](privacy-and-provider.md#runtime-error-diagnostics).
