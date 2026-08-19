# Interactive terminal component

> **Availability:** this package-supported component owns shell input, safe
> records, and bare `/run` composition. Orientation, status/inspection,
> central errors/recovery, and accepted management, advisory, retrieval, and
> consensus output use its semantic renderer. Full/context help, agent roster,
> session/conversation lists, search, transcript, and focused history now do as
> well. Four composer-only generic status records retain their established
> presentation pending component integration.

## Public API

Import from the package-supported facade
`story_writing_agents.interactive_terminal`. Its exact `__all__` is
`AttachmentKind`, `ComposeMode`, `ConfirmationResult`, `TerminalActionKind`,
`TerminalMessageKind`, `InteractiveErrorCode`, `TerminalAction`,
`InteractiveErrorSpec`, `INTERACTIVE_ERROR_SPECS`, `InteractiveError`,
`sanitize_terminal_text`, and `InteractiveTerminal`. Package leaves and
unlisted factory, model, control, renderer, and driver names are internal, not
additional supported API. The root `story_writing_agents` facade remains
unchanged and does not re-export the terminal.

```python
InteractiveTerminal(read_line, write_text, *, width: int = 80,
                    main_read_line: Callable[[str], str] | None = None)
read_main(*, session_id=None, agent_id=None) -> TerminalAction
request_conversation_subject() -> TerminalAction
request_confirmation(message: str, *, default: bool) -> ConfirmationResult
compose(mode: ComposeMode, *, agent_id=None) -> TerminalAction
write_lobby_orientation(workspace: str) -> None
write_session_orientation(workspace, session_id, attachment, agent_id, history_count=None) -> None
write_conversation_scope(*, session_id, conversation_handle, conversation_subject, agent_id) -> None
write_conversation_list_entry(conversation_handle, conversation_subject, *, archived, focused) -> None
write_conversation_search_hit(conversation_handle, conversation_subject, match_kind, snippet, *, archived, citation_turn_id=None, citation_sequence=None, citation_kind=None) -> None
write_conversation_transcript_event(conversation_handle, *, sequence, turn_id, kind, sender, recipient, content) -> None
write_agent_roster() -> None
write_status(kind: TerminalMessageKind, text: str) -> None
write_run(debate_id: str, text: str) -> None
write_agent(agent_id: str, text: str) -> None
write_help(command: str | None = None) -> None
write_error(error: InteractiveError, *, secrets=()) -> None
sanitize_terminal_text(text: str) -> str
```

`main_read_line` defaults to `read_line` and serves only `read_main`; public
construction continues to use `read_line` for composition, subjects, and
confirmation. The CLI's package-internal wiring separates ordinary, main, and
composition reads without changing this signature or `__all__`. Record width
starts at 20. The terminal owns no lifecycle or storage.

The exact `interactive_line_editor.__all__` remains
`SlashCommandSuggestion`, `slash_command_suggestions`, and
`InteractiveMainLineEditor`; its constructor and `read_line(prompt)` are
unchanged. Composition-provider selection is package-internal, not an added
supported API.

For controller wiring, the package facade also exposes the unlisted canonical
identities `InteractiveSemanticOutput`, `Field`, `Message`, `Notice`, `Panel`,
`Record`, `Section`, `ListView`, `SemanticRole`, and `ViewState`. They are
package-internal, absent from `__all__` and the root package, and are not a
supported public API.
The narrow internal adapter has these signatures:

```python
InteractiveSemanticOutput(terminal: InteractiveTerminal) -> None
write_component(component: Component) -> None
```

Construction validates and retains the terminal's bound component writer but
does not render, read, write, observe a stream, or acquire a resource. Each
accepted component call performs one render and one callback write, including
the final LF; failures propagate without a retry.

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `opened`, `reopened` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

## Implemented semantic output and deferred presentation

The import-compatible package contains private immutable semantic components
and states, pure current one-line transitions and exact slash-prefix
projection, a responsive structured plain/fixed-ANSI renderer, capability
profiles, one raw driver, and one real-CLI stream factory. These internal seams
support architecture and deterministic testing; they add no public theme API
or public component types.

The real CLI factory constructs one `InteractiveTerminal` around one raw
driver. That driver alone owns its streams, capability/width observation, key
decoding, functional cursor sequences, raw-mode activation/restoration, writes,
and flushes. `interactive_line_editor.InteractiveMainLineEditor` remains a
standalone compatibility facade with its exact constructor and `read_line`
surface, delegating to the same driver implementation rather than owning a
second terminal lifecycle. Callback construction remains deterministic plain
and performs no stream observation or resource acquisition.

#83 uses this foundation for:

- lobby, session, and conversation orientation;
- interactive `/status` and `/inspect` projections built from frozen validated
  facts, without raw persisted messages, errors, candidate text, or provider
  state;
- typed interactive errors and recovery hints;
- accepted management acknowledgements and routing/current-context notices;
- advisory waiting, cancellation, context disclosure, and agent replies;
- retrieval progress and cited-source records; and
- consensus progress, inspection, cancellation, and terminal summaries.

#84 uses the same foundation for:

- full registry-backed help and one-command contextual help;
- the canonical four-agent roster;
- positioned session and conversation catalog rows plus explicit empty lists;
- positioned search hits followed by their result and optional truncation hint;
- positioned transcript events followed by their result and optional
  continuation hint; and
- positioned focused-history events followed by one context-accounting record.

The controller composes typed components and sends one component through the
adapter for each established callback record. The plain form preserves the
established facts, copy, order, and callback boundaries while adding explicit
structure and applicable `loading`, `empty`, `error`, `cancelled`, and
`completed` state markers. Capable real-CLI output may add fixed decorative
ANSI; `NO_COLOR` removes only that decoration. Callback construction renders
the same structure in plain text at its fixed width.

The five dense-view terminal writer families now accept the same primitive
arguments and emit the same fact order through semantic `Panel`, `ListView`, or
`Record` values. Controller list commands emit one positioned, one-record
`ListView` for each established row callback. The four `write_status` calls in
the terminal composer remain established plain records pending #86; the
controller has no generic `write_status` call site. Contextual composition
previews are implemented as documented below. History navigation, selectable
palettes/fixed values, component-integrated composer/confirmation, pickers, and
`/settings` are not currently available.

## Prompts and ordinary presentation

Exact prompts include their final space:

```python
"story[lobby]> "
f"story[{session_id}:{alias}]> "
"conversation[subject]> "
f"compose[chat:{alias}:{line_number}]> "
f"compose[run:{line_number}]> "
```

Confirmation shows `[Y/n] ` or `[y/N] `; blank selects its default. Ctrl-C/EOF
return typed outcomes.

## Current semantic plain shapes

Orientation uses titled `Story chat` or `Conversation scope` panels with
indented `Workspace`, `Session`, `Target`, optional `History`, `Conversation`,
`Subject`, and `Agent` fields in the established order. Status and inspection
use records headed by stable bracketed identities such as `[session]`,
`[conversation]`, `[context]`, and `[run DEBATE_ID]`. Values stack under their
labels at narrow widths instead of being dropped.

Agent and run output preserve content boundaries:

```text
ALIAS [completed]> FIRST LINE
                   CONTINUATION

[run DEBATE_ID]
  CONTENT
```

Status roles and terminal states are textual. For example, consensus inspection
uses `[run DEBATE_ID] [completed] [success]`, interrupted inspection uses
`[cancelled] [warning]`, failed/deadlocked inspection uses `[error]`, active
inspection uses `[loading] [info]`, and a missing debate uses `[empty]`. These
markers are present in plain and `NO_COLOR` output; color is redundant.

Full help is one completed `Help` panel. It retains registry group order; each
completed group section contains one completed `Commands` list whose records
retain syntax, aliases, provider-call, and artifact-write facts. Context help is
one completed `Help` panel containing one command record in `Group`, `Syntax`,
`Aliases`, `Calls provider`, `May write artifacts`, `Example` order. Agent
roster is one completed `Agents` list in canonical route order; each record
retains display name, `ID`, `Aliases`, and `Advisory domain`.

Session, conversation, search, transcript, and focused-history rows use this
positioned form, with one row per callback:

```text
TITLE (POSITION/TOTAL) [completed]
  [RECORD] [completed]
    LABEL: VALUE
```

Wide output keeps label/value pairs inline; narrow output stacks them instead
of dropping facts. Empty session and conversation catalogs are one explicit
empty `ListView`. Empty search and transcript results emit their exact empty
result notice without a synthetic row. Empty focused history emits no History
row and still emits the context record with `History displayed: 0/0 events`.

## Main-prompt live suggestions

The unified driver provides the existing preview at main prompts. On capable
POSIX, buffer `/` displays `[suggestions]` followed by one token per row. Compact
registry order (not output layout):

```text
/say /to /agent /agents /language /persona /reset /run /draft-file /session
/conversation /search /transcript /inspect /history /status /help /? /quit /exit
```

Case-sensitive prefixes filter; whitespace, no match, submission,
or control clears it. Tokens neither complete nor reflect scope gates; `/help`
remains authoritative.
The main-invalid `/compose`, `/send`, and `/cancel` are excluded. Composition
uses the separate fixed preview below; subject and confirmation prompts keep
ordinary line reads. Editing, control keys, fallbacks, and restoration are in
[Terminal safety](../operations/terminal-safety.md#live-editor-boundary).
The lists are not selectable: Tab does not apply a suggestion, Shift-Tab has no
palette action, and Up/Down history is unavailable in the current foundation.
Future palette/history coordination is specified in the
[design contract](../design/interactive-cli-design-system.md#main-command-palette-and-fixed-choices).

## Composition live suggestions

On capable POSIX live editing, RUN and module-supported CHAT composition reads
routed through the unified stream terminal's explicit composition route show
`[suggestions]` with only these tokens, in this order:

```text
/send
/cancel
```

Matching is an exact, case-sensitive prefix of the whole current buffer. `/`
shows both entries, `/s` narrows to `/send`, and `/c` narrows to `/cancel`.
Whitespace, an empty buffer, or any nonmatching text clears the panel. `/say`
is not suggested; `/say /send` and `/say /cancel` keep their established
literal-escape meaning and show no panel because the buffers contain
whitespace.

This is preview only: it does not complete, replace, select, parse, or dispatch
the buffer and does not call a story provider, runtime, filesystem, or storage.
Enter submits the unchanged buffer to the existing composition rules. The
installed shell enters RUN composition through bare `/run`; CHAT composition
remains a module-supported terminal operation. At the editor boundary,
unsupported, `TERM=dumb`, non-TTY, and non-POSIX reads use ordinary line input
without a panel; the installed CLI's earlier dual-TTY guard still applies.

<a id="exact-search-and-transcript-records"></a>

## Dense search, transcript, and history records

Search rows retain these fields in exact order: `Status`, `Match`, `Subject`,
`Citation`, `Snippet`. A conversation-level citation is
`conversation HANDLE`; a transcript citation adds `turn TURN, sequence
SEQUENCE, kind EVENT_KIND`. Each hit is one completed positioned `Search
results` list and one callback. Rows are followed, in separate callbacks, by:

```text
[result] Search returned {HITS} of {MATCHES} matching conversations.
[hint] Search was truncated by fixed scan or result limits; refine the query.
```

The hint appears only when the established limits truncate the result.

Transcript and focused-history rows retain `Sequence`, `Turn`, `Kind`,
`Sender`, `Recipient`, then `Content`, in that order. Each event is one
completed positioned `Transcript` or `History` list and one callback. A
transcript page then emits:

```text
[result] Transcript page returned {EVENTS} events.
[hint] Continue with /transcript {HANDLE} {COUNT} --from {NEXT}.
```

The continuation hint appears only when the page has a next cursor. Focused
history instead emits one `[context]` record with `History displayed`,
`Complete turns`, `Context retained`, `Omitted turns`, `UTF-8 bytes`, `Turn
cap`, and `Truncated` in that order. Source whitespace and LF remain; controls
are visible, events are never joined, and content remains inside its one
callback record.

If Ctrl-C interrupts search, transcript, or history row delivery, all already
completed row callbacks remain visible. The controller writes exactly one
matching cancellation record and omits every unrendered row plus the trailing
result, hint, or context record. Attached state remains unchanged.

## Exact cancellation records

| Boundary | Exact records in order |
| --- | --- |
| Main prompt / open or pause confirmation | `[cancelled] No action was taken.` / `[cancelled] Session open cancelled.` / `[cancelled] Session pause cancelled.` |
| Recoverable advisory | `[waiting] {DISPLAY_NAME} is responding; Ctrl-C cancels this turn.`<br>`[cancelled] {DISPLAY_NAME} turn was interrupted.`<br>`[hint] The story session remains attached; the affected provider thread may restart on the next message.` |
| Consensus before entry | `[cancelled] Consensus run was cancelled before start.` |
| Consensus with trusted durable proof | `[run {DEBATE_ID}] [cancelled] [warning]` then an indented `Run interrupted after durable state transition.` in that same callback record;<br>`[cancelled] Debate {DEBATE_ID} was marked interrupted.`<br>`[hint] It cannot resume. Use /run to create a new debate.`<br>`[session] "{SESSION_ID}" remains ready and attached.` |
| Partial local render | `[cancelled] Search cancelled; attached state is unchanged.` / `[cancelled] Transcript read cancelled; attached state is unchanged.` / `[cancelled] History cancelled; attached state is unchanged.` |

## Errors and other output

The controller classifies 36 codes; the terminal accepts no exceptions.
`write_error()` redacts secrets, caps summary/hint at 512/256 characters, and
writes one semantic panel and one callback record:

```text
Interactive error [error]
  [error CODE] SUMMARY
  [hint] HINT
```

It preserves the trusted code, summary, hint, order, limits, redaction, and
failure behavior while allowing responsive wrapping and fixed decorative ANSI.
See
[recovery mappings](../operations/recovery-and-limitations.md) and
[retrieval copy](conversation-retrieval.md). Composition retains exact LF;
discards expose only counts. Routed replies are never reparsed.
