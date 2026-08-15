# Terminal display safety

> **Availability:** these rules apply to the module-supported interactive
> terminal component. No installed interactive TTY or controller uses it yet.

Key terms: **display escaping**—showing a control code as text; **redaction**—
replacing caller-identified secrets; **plain terminal**—an output stream with no
trusted rich rendering; **process-local buffer**—unsent lines held for one call.

## Safe display boundary

`sanitize_terminal_text()` preserves ordinary Unicode, Markdown characters,
and LF. It replaces every other Unicode `Cc` character, all `Cf` and `Cs`
characters, and U+2028/U+2029 with a visible lowercase escape:

| Code point | Display form |
| --- | --- |
| U+0000–U+00FF | `\xhh` |
| U+0100–U+FFFF | `\uhhhh` |
| Above U+FFFF | `\Uhhhhhhhh` |

For example, ANSI color input `ESC[31m` displays as `\x1b[31m`; it cannot act
as a terminal escape. The result is strict UTF-8 and sanitizing it again makes
no change.

Every `InteractiveTerminal` writer sends exactly one sanitized string to its
borrowed output callback and adds one final LF. A block may contain preserved
internal LFs. Workspace text inside orientation is forced inline, so an input
LF displays as `\x0a` instead.

This is plain-text safety, not trusted rendering. The component preserves
Markdown syntax without interpreting it and does not detect terminal features,
colors, width, or TTY presence. The callback or surrounding terminal can still
record, transform, or expose the text it receives.

## Typed error redaction

`write_error()` accepts an `InteractiveError` chosen by trusted controller code
and an explicit iterable of secret strings. It does not accept an exception or
infer a code from exception text.

Before display, it:

1. expands each nonempty secret to raw, JSON-escaped Unicode/ASCII, visibly
   escaped, whitespace-collapsed, and control-normalized variants;
2. replaces longer variants first with `<redacted>`, both before and after
   display escaping;
3. converts summary and hint to one trimmed, whitespace-collapsed line, falling
   back to the registry default when custom copy is blank after processing;
4. truncates the summary to 512 characters and hint to 256, ending truncated
   text with ` ...[truncated]` inside the cap; and
5. wraps the trusted `[error CODE]` and `[hint]` prefixes to the configured
   width without splitting long words.

Redaction only covers secrets supplied by the caller and their derived forms.
It is not a general credential detector. Default summaries and hints are fixed,
single-line strict-UTF-8 values in the immutable registry. Exact codes and API
shapes live in [Interactive terminal](../reference/interactive-terminal.md#help-output-and-errors).

## Input and history consequences

Display escaping does not alter input returned in a `TerminalAction`. A sent
composition preserves its exact lines joined by LF; the future controller must
decide whether to parse, send, or persist it.

Composition history exists only in a list owned by that `compose()` call.
Cancel, keyboard interrupt, or EOF returns counts but no text and releases the
buffer when the call ends. This is not secure erasure: the input callback,
terminal emulator, process memory, or surrounding controller may retain copies.

The component itself opens no file, contacts no provider, and stores no
history. Its output callback may do any of those things, so callers must choose
callbacks appropriate for their privacy requirements.

## Different from stored runtime diagnostics

Terminal errors are presentation-only, use caller-supplied secrets and the caps
above, and are not conversation `runtime_error` events.

`StoryRuntime` diagnostics are stored transcript content. Their sanitizer
automatically redacts the current message, context, and workspace roots and
uses a different cap. See [Privacy and provider exposure](privacy-and-provider.md#runtime-error-diagnostics)
for that persistence contract.
