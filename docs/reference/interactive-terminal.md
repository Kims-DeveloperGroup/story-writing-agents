# Interactive terminal component

> **Availability:** this package-supported component owns shell input, safe
> records, and bare `/run` composition. Orientation, status/inspection,
> central errors/recovery, and accepted management, advisory, retrieval, and
> consensus output use its semantic renderer. Full/context help, agent roster,
> session/conversation lists, search, transcript, and focused history now do as
> well. Capable main input now provides selectable semantic command completion
> coordinated with bounded process-local history. Line-oriented RUN/CHAT
> composition and yes/no confirmation use semantic input views, and composer
> lifecycle records use semantic notices. A generic single-select picker
> foundation and a cancellable prompt seam are implemented behind unlisted
> package-internal adapters. `/settings` consumes both without changing the
> supported public terminal API. Advisory turns additionally use one unlisted
> terminal-owned rolling indicator on capable raw streams; callback and
> incapable paths remain silent, and advisory turns emit no `[routing]` or
> `[waiting]` record.

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
construction continues to use `read_line` for subjects and as the underlying
callback for composition, confirmation, and the internal picker and prompt
line fallbacks. Each composition or confirmation
attempt first renders its full semantic view to one structured plain prompt and
passes that prompt to `read_line` once. The CLI's package-internal wiring
separates ordinary subject, main, composition, and confirmation reads without
changing this signature or `__all__`. Record width starts at 20.
Callback-constructed terminals own no history, lifecycle, or storage; the
stream driver's process-local history is internal.

The exact `interactive_line_editor.__all__` remains
`SlashCommandSuggestion`, `slash_command_suggestions`, and
`InteractiveMainLineEditor`; its constructor and `read_line(prompt)` are
unchanged. Its public `read_line()` uses one per-editor process-local main
history store and the same supported internal main completion route;
composition-provider selection and the history-free contextual route are
package-internal, not added supported API.

For controller wiring, the package facade also exposes the unlisted canonical
identities `InteractiveSemanticOutput`, `Field`, `Message`, `Notice`, `Panel`,
`Record`, `Section`, `ListView`, `SemanticRole`, and `ViewState`. They are
package-internal, absent from `__all__` and the root package, and are not a
supported public API. The facade also binds the package-internal
`MainInputHistoryState`, `MainInputControlState`,
`append_main_input_history`, and `transition_main_input` identities used by the
compatibility editor; they likewise remain unlisted and unsupported.
`transition_main_command_input` and `read_main_command_line` are additional
unlisted internal bindings for the shared selectable main route. Its completion
models remain package-private and outside the closed `Component` union; only
their projected existing `SuggestionPalette` is rendered.
The facade also exposes the unlisted `Choice`, `Prompt`, `ControlOutcome`,
`ControlOutcomeKind`, `InteractivePickerInput`, and `InteractivePromptInput`
identities for the generic picker and cancellable-line seams.
`PickerControlState` and `PickerViewport` remain package-private; the existing
semantic `Picker` remains a `Component`. None of these additions changes public
`__all__`, root exports, or supported signatures.

The facade also binds unlisted `InteractiveAdvisoryProgress` for the
controller's one-advisory-awaitable lifecycle. It is package-internal and does
not expose the driver, renderer, glyphs, cursor controls, or a public animation
API.

The narrow internal adapters have these signatures:

```python
InteractiveSemanticOutput(terminal: InteractiveTerminal) -> None
write_component(component: Component) -> None

InteractivePickerInput(terminal: InteractiveTerminal) -> None
read_picker(prompt: Prompt, choices: tuple[Choice, ...], *,
            highlighted_id: str | None = None,
            selected_id: str | None = None,
            notice: Notice | None = None,
            state: ViewState = ViewState.OPEN,
            transient: bool = False) -> ControlOutcome

InteractivePromptInput(terminal: InteractiveTerminal) -> None
read_prompt(prompt: Prompt, *,
            context: tuple[Panel | Section | Field | Notice, ...] = (),
            transient: bool = False) -> ControlOutcome

InteractiveAdvisoryProgress(terminal: InteractiveTerminal) -> None
run(operation: Awaitable[object]) -> object  # async, package-internal
```

Construction validates and retains the terminal's bound component writer but
does not render, read, write, observe a stream, or acquire a resource. Each
accepted component call performs one render and one callback write, including
the final LF; failures propagate without a retry. The picker adapter likewise
binds only the terminal's picker reader without observing capabilities or
acquiring I/O. The settings controller consumes both input adapters through
the facade; neither is public API. `transient` is an opt-in settings lifecycle:
its default `False` preserves the established picker and prompt final-frame
behavior used by every non-settings caller. `InteractiveAdvisoryProgress` is
also unlisted and binds one call-local async operation to the terminal's
advisory indicator without changing the supported public API.

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `opened`, `reopened` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

The established `routing` and `waiting` enum values remain compatibility
identities, but #111's advisory-turn path does not emit records with either
label. Routing acknowledgements outside an advisory turn retain their existing
meaning.

## Implemented semantic output and input

The import-compatible package contains private immutable semantic components,
input views, composer/history/completion/picker states, pure one-line,
selectable main/history, composer, confirmation, and picker transitions,
cursor-aware canonical grammar projection, responsive structured
plain/fixed-ANSI renderers, capability profiles, one raw driver, and one
real-CLI stream factory. These
internal seams support architecture and deterministic testing; they add no
public theme API or public component/control types.

The real CLI factory constructs one `InteractiveTerminal` around one raw
driver. That driver alone owns its streams, capability/width observation, key
decoding, functional cursor sequences, raw-mode activation/restoration, writes,
flushes, and one bounded process-local main history store. Each separate stream
terminal or compatibility editor therefore has an isolated store.
`interactive_line_editor.InteractiveMainLineEditor` remains a
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
- advisory cancellation, context disclosure, and agent replies; #111 adds an
  optional capable-only indicator without an advisory waiting record;
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

### Advisory progress

The controller brackets exactly one advisory runtime awaitable with the
unlisted `InteractiveAdvisoryProgress` adapter. On a capable raw stream, the
sole driver renders the fixed `◐`/`◓`/`◑`/`◒` cycle, clears the active cell around
semantic retrieval/progress/result writes, and clears it before final,
cancellation, or error records. It joins the operation and indicator task on
every exit. `NO_COLOR` leaves the glyphs unchanged because they are not SGR;
the indicator has no dynamic text, alternate screen, background, or general
animation API.

Callback construction and incapable stream paths await the operation without
writing indicator output. This includes non-TTY, `TERM=dumb`, non-POSIX,
missing, and uncertain capability; the installed CLI guard still rejects
non-TTY shell entry before controller work. An underlying operation failure
takes precedence over a simultaneous indicator failure. If only the indicator
fails, the adapter raises its internal `AdvisoryProgressError` after cleanup so
the controller presents the existing `internal_error` path. Advisory turns
emit no `[routing]` or `[waiting]` record; their ordinary result, cancellation,
context, and error records remain unchanged.

The five dense-view terminal writer families now accept the same primitive
arguments and emit the same fact order through semantic `Panel`, `ListView`, or
`Record` values. Controller list commands emit one positioned, one-record
`ListView` for each established row callback. The terminal composer now emits
semantic `open`, `error`, `completed`, and `cancelled` notices instead of its
four generic-status call sites; the controller also has no generic
`write_status` call site. Composition and confirmation build ordered semantic
input views and use pure history-free controls as documented below. Selectable
main-command palettes and finite fixed options are available on capable main
reads. The generic picker and cancellable-prompt foundations remain available
only through unlisted internal seams; `/settings` is their installed consumer.

## Prompts and semantic input presentation

Exact ordinary and focused prompt identities include their final space:

```python
"story[lobby]> "
f"story[{session_id}:{alias}]> "
"conversation[subject]> "
f"compose[chat:{alias}:{line_number}]> "
f"compose[run:{line_number}]> "
"confirm [Y/n]> " or "confirm [y/N]> "
```

Each composition attempt renders, in order, `Mode`, optional `Target`, `Next
line`, `Lines`, `Characters`, `[info] Use /send to submit or /cancel to
discard.`, and the final focused numbered prompt. Each confirmation attempt
renders `Question`, `Choices: y/yes or n/no`, `Default`, and the final focused
`confirm [Y/n]> ` or `confirm [y/N]> ` prompt. Blank selects the displayed
default. Ctrl-C/EOF return typed outcomes.

Plain and fixed-ANSI input projections contain the same semantic text, wrap to
the active width, end in the focused prompt's required space, and have no final
LF. Safe line fallback receives the plain projection once. Capable live input
uses the trusted fixed-theme projection for redraw and sanitizes the dynamic
buffer separately.

Picker input is a separate semantic projection. Capable live input renders a
bounded viewport at the current observed width; plain callback, non-TTY,
`TERM=dumb`, non-POSIX, and safely recovered activation paths render the
complete numbered list through the ordinary line callback. Opaque choice
identities are control state and are never rendered.

Settings free-text and numeric values use a separate context-free semantic
`Prompt`. That route is history- and completion-free. Enter commits the exact
line, Escape cancels only the nested editor, and Ctrl-C or EOF cancels the whole
settings workflow. In fallback an exact ESC line cancels, while `/cancel`
remains literal input. The same sole driver owns raw input, redraw, cleanup,
and restoration.

When a capable raw main read commits `/settings`, the controller consumes one
correlated committed-display token and clears only those live rows before
opening the settings view. The already accepted line remains in the driver's
main-history store. Callback and ordinary-line fallback cannot erase committed
rows, so the line remains visible and no cleanup is claimed; history is
unchanged in every profile.

Save and Cancel each use a transient yes/no confirmation prompt. Its context
contains only fields whose Current and Pending values differ, and both displayed
values carry the `(Changed)` cue; an empty diff shows `No pending settings
changes.`. `yes`/`y` commits the requested action, while `no`/`n`, blank, or
nested Escape returns to the settings picker. Invalid input retries with a
trusted notice. Top-level Escape, Ctrl-C, and EOF cancel without writing and do
not imply a snapshot was rendered.

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

Agent replies receive a presentation-only JSON projection before the semantic
message or record is rendered. The projection runs only when the entire
content string parses as strict JSON whose top-level value is an object or
array. Duplicate object keys and non-finite constants (`NaN`, `Infinity`, and
`-Infinity`) reject the projection. Accepted values are re-emitted with two
spaces per indentation level, preserved object-key order, and Unicode emitted
without ASCII escaping. Malformed JSON, embedded prose or extra data, scalar
JSON values (`null`, booleans, numbers, or strings), duplicate keys,
non-finite values, and any serialization failure return the original string
unchanged. The provider result and any stored transcript bytes are never
rewritten.

An `AGENT` semantic role is bold with the terminal's default foreground color;
it does not inspect or choose a terminal background. ANSI-capable output wraps
that emphasis in a local reset, while plain and `NO_COLOR` output retain the
same unstyled text.

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

The unified driver provides a selectable semantic `Suggestions [open]` palette
at capable POSIX main prompts. A root `/` opens with its first candidate
highlighted. Compact registry order (not output layout):

```text
/say /to /agent /agents /language /persona /reset /settings /run /draft-file /session
/conversation /search /transcript /inspect /history /status /help /? /quit /exit
```

Exact case-sensitive prefixes filter in stable order. Root provides the 21
tokens above; help provides 24 including `/compose`, `/send`, and `/cancel`;
agent-valued positions provide the four preferred prompt aliases—`lead`,
`plot`, `character`, and `execution`—in route order. The parser still accepts
all ten declared route aliases; completion does not duplicate those aliases.
Static finite positions also provide session and conversation subcommands,
`--fresh`, `--all`, `last`, and `--from`. Dynamic session, conversation,
debate, count, sequence, query, prompt, subject, and path values are not
synthesized. Fixed-only positions remain open with an explicit empty palette on
no match; mixed fixed/free positions close when the active slot is free text.
Tokens do not reflect runtime scope gates; `/help` remains authoritative.

The active token is the maximal ASCII-space/tab-delimited span around the
cursor. Tab replaces that full span with the highlighted candidate and adds no
whitespace. Help permits a bare prefix to select a slash replacement, for
example `/help ru` to `/help /run`. Up/Down moves the highlight and clamps at
the ends without replacing text or traversing history. Filtering keeps the
highlight when its identity remains eligible; otherwise the first match is
highlighted. Tab is a no-op for an exact candidate or no highlight, and
Shift-Tab is always a no-op.

The first Tab that changes text retains the complete pre-completion buffer and
cursor. Edits in the same grammar position keep that snapshot; a position
change resets it. Escape restores the snapshot when present, otherwise leaves
the buffer unchanged, then closes the palette and latches that exact line state
dismissed. An edit or history change clears the latch and permits reopening.
Closed-palette Up/Down resumes command-history navigation. Enter commits only
the visible buffer to the established parser once, never an unapplied
highlight.

Plain, fixed-ANSI, `NO_COLOR`, narrow, and empty palette rendering preserve the
same semantic candidate order and highlight. Ordinary-line fallback receives
no palette projection and owns none of these keys. Composition uses the
separate preview-only fixed suggestions below; confirmation has no suggestions,
and the subject keeps its ordinary line read. Editing, decoding, fallback, and
restoration details are in
[Terminal safety](../operations/terminal-safety.md#live-editor-boundary); the
pure control contract is in the
[design contract](../design/interactive-cli-design-system.md#main-command-palette-and-fixed-choices).

## Main-command history

Each real stream terminal and each `InteractiveMainLineEditor` owns one
driver-local store of at most 100 main-prompt entries. Up recalls older entries;
Down recalls newer entries. The first Up saves the exact current draft and
cursor, and Down past the newest entry restores both. Recalled text is an
editable copy with the cursor at its end. Further navigation can replace that
edited copy without changing the stored entry.

The driver records each successfully completed exact nonblank main buffer once,
before the existing parser runs. Leading and trailing whitespace are preserved;
invalid command text, duplicates, `/quit`, ordinary-line fallback results, and
a nonempty configured VEOF commit are therefore eligible. The 101st entry
evicts only the oldest. The exact visible submitted buffer still reaches the
parser once.

Empty or whitespace-only input, Ctrl-C, empty configured VEOF, physical stream
EOF, incomplete input, invalid fallback framing, and operations that fail
without returning an accepted line are not recorded. Composition and its
contextual suggestions, conversation subject, confirmation, previews, and
uncommitted edits neither navigate nor mutate the store. A line-fallback main
read can add an entry, but the component supplies no live arrow navigation in
that fallback; the same entry can be recalled on a later capable read from the
same driver.

History lasts only for that driver instance and is never persisted to the
workspace, transcripts, configuration, environment, provider sessions, or a
user history file. An open main palette consumes Up/Down for clamped highlight
navigation; closed-palette Up/Down retains the history behavior above.

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
with the structured plain semantic prompt once and without a suggestion panel;
the installed CLI's earlier dual-TTY guard still applies.

Composition remains line-oriented and history-free. Up/Down, Tab, Shift-Tab,
and Escape are no-ops. An ordinary line updates the next view's exact line and
character counts. `/send` commits only nonblank LF-joined content; an empty
send writes one semantic error notice and retries. `/cancel`, Ctrl-C, and EOF
discard with counts only. `/say /send` and `/say /cancel` append literal
`/send` and `/cancel` lines. Entry, empty-send, send, and discard each retain
one semantic notice and one callback write.

## Confirmation input

Confirmation uses the same history-free semantic input route with an empty
suggestion set. Up/Down, Tab, Shift-Tab, and Escape are no-ops. The exact
accepted values remain case-insensitive `y`, `yes`, `n`, and `no` after
surrounding whitespace is ignored; blank selects the displayed default.
Invalid input writes one `[hint] Enter y, yes, n, or no.` semantic error notice,
marks `Choices` with error state in the next view, and reads again. A valid
answer produces `yes` or `no`; Ctrl-C and EOF produce `interrupt` or `eof`.
None of those terminal results adds a final confirmation notice.

<a id="generic-internal-picker-foundation"></a>

## Generic internal picker foundation

This foundation is package-internal; `/settings` consumes it through the
controller and unlisted terminal facade adapter. A caller supplies one semantic
prompt and immutable choices whose opaque identities are returned on commit but
never rendered.
Initialization highlights an explicit enabled choice, otherwise an enabled
selection, otherwise the first enabled choice. Empty and all-disabled choice
sets remain explicit `empty` and `disabled` states.

Highlight and selection are independent until commit, so a frame can show one
selected choice and another highlighted choice. Up/Down moves the highlight
among enabled choices, skips disabled choices, and clamps at the first or last
enabled choice without consulting main history. Enter commits the highlighted
identity and makes it selected; Escape returns `cancel` while preserving the
prior selection. Configured VINTR/Ctrl-C and VEOF/physical EOF return the typed
`interrupt` and `eof` outcomes. Other edit and completion keys do nothing and
the picker never owns main completion state.

Live rendering shows a contiguous viewport of at most seven choices and
re-observes capability and width on each redraw. Selection and highlight may
appear simultaneously on one row; a selected choice outside the viewport is
summarized by ordinal and label. Choice-set reconciliation preserves enabled
selection and highlight by identity when possible and deterministically finds
the next enabled highlight when not. Opaque identities remain absent from
plain, fixed-ANSI, `NO_COLOR`, narrow, and fallback text.

Line fallback renders every choice as a numbered row and accepts only:

- exact `/cancel`, which cancels;
- a blank line when an enabled choice is highlighted, which commits that
  highlight; or
- an ASCII decimal ordinal with no leading zero that is in range and enabled.

Zero, leading-zero, signed, spaced, non-ASCII, out-of-range, disabled-choice,
name, label, identity, and other command input is rejected without changing
selection or highlight. The retry adds exactly:

```text
Enter an enabled choice number, press Enter for the highlighted choice, or enter /cancel.
```

The default picker lifecycle renders one final `completed` or `cancelled` frame
after commit or cancel. Settings opts into a transient lifecycle for its
pickers and prompts. Capable terminal reads clear the transient rows before
returning the typed outcome, so no duplicate final frame is emitted. Callback
and line-fallback readers cannot erase prior output; transient picker fallback
therefore omits its synthetic final frame rather than duplicating it. Nested
settings Escape returns to the parent silently. Save and Cancel confirmations
show the changed-only Current/Pending context; top-level cancellation does not
claim a snapshot that was not rendered. Non-settings calls retain the default
final-frame behavior.

For the default lifecycle, live and fallback readers emit exactly one final
semantic `completed` or `cancelled` frame after commit or cancel. Interrupt,
EOF, and failures clear any transient live display and emit no final picker
state; restoration failure remains fatal. Callback, non-TTY, `TERM=dumb`,
non-POSIX, uncertain-capability, and safely recovered activation paths use the
numbered fallback without acquiring raw input. The unified driver remains the
only raw resource owner and picker reads do not inspect or mutate main history,
completion, parser, provider, runtime, filesystem, storage, or network state.

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
callback record. An `agent_reply` content value receives the same strict
object/array presentation projection as a direct reply; all other event kinds
remain unchanged.

If Ctrl-C interrupts search, transcript, or history row delivery, all already
completed row callbacks remain visible. The controller writes exactly one
matching cancellation record and omits every unrendered row plus the trailing
result, hint, or context record. Attached state remains unchanged.

## Exact cancellation records

| Boundary | Exact records in order |
| --- | --- |
| Main prompt / open or pause confirmation | `[cancelled] No action was taken.` / `[cancelled] Session open cancelled.` / `[cancelled] Session pause cancelled.` |
| Recoverable advisory | `[cancelled] {DISPLAY_NAME} turn was interrupted.`<br>`[hint] The story session remains attached; the affected provider thread may restart on the next message.` |
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
