# Interactive terminal component

> **Availability:** this package-supported component owns shell input, safe
> records, and bare `/run` composition. Its internal semantic/rendering
> foundation is implemented, while public view methods retain their exact
> established plain presentation.

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

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `opened`, `reopened` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

## Implemented foundation and current presentation

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

This foundation does not migrate existing shell views. Orientation, help,
lists, search, transcript, history, advisory, retrieval, consensus, and error
methods still emit the exact plain records documented below. Structured
semantic layouts and decorative color for those views remain future migration
work. Contextual composition previews are implemented as documented below.
History navigation, selectable palettes/fixed values, component-integrated
composer/confirmation, and pickers are not currently available.

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

## Exact search and transcript records

These templates are safely wrapped after substitution; each remains one record.

```text
[search {HANDLE}] Status: {active|archived} | Match: {MATCH} | Subject: {SUBJECT} | Citation: conversation {HANDLE}
[snippet] {SNIPPET}

[search {HANDLE}] Status: active | Match: transcript | Subject: {SUBJECT} | Citation: conversation {HANDLE}, turn {TURN}, sequence {SEQUENCE}, kind {EVENT_KIND}
[snippet] {SNIPPET}

[result] Search returned {HITS} of {MATCHES} matching conversations.
[hint] Search was truncated by fixed scan or result limits; refine the query.

[transcript {HANDLE}] Sequence: {SEQUENCE} | Turn: {TURN} | Kind: {KIND} | Sender: {SENDER} | Recipient: {RECIPIENT}
[content] {CONTENT}
[result] Transcript page returned {EVENTS} events.
[hint] Continue with /transcript {HANDLE} {COUNT} --from {NEXT}.
```

Whitespace/LF remain; controls are visible and never joined.

## Exact cancellation records

| Boundary | Exact records in order |
| --- | --- |
| Main prompt / open or pause confirmation | `[cancelled] No action was taken.` / `[cancelled] Session open cancelled.` / `[cancelled] Session pause cancelled.` |
| Recoverable advisory | `[waiting] {DISPLAY_NAME} is responding; Ctrl-C cancels this turn.`<br>`[cancelled] {DISPLAY_NAME} turn was interrupted.`<br>`[hint] The story session remains attached; the affected provider thread may restart on the next message.` |
| Consensus before entry | `[cancelled] Consensus run was cancelled before start.` |
| Consensus with trusted durable proof | `[run {DEBATE_ID}] Run interrupted after durable state transition.`<br>`[cancelled] Debate {DEBATE_ID} was marked interrupted.`<br>`[hint] It cannot resume. Use /run to create a new debate.`<br>`[session] "{SESSION_ID}" remains ready and attached.` |
| Partial local render | `[cancelled] Search cancelled; attached state is unchanged.` / `[cancelled] Transcript read cancelled; attached state is unchanged.` / `[cancelled] History cancelled; attached state is unchanged.` |

## Errors and other output

The controller classifies 36 codes; the terminal accepts no exceptions.
`write_error()` redacts secrets, caps summary/hint at 512/256 characters, and
writes sanitized records. See
[recovery mappings](../operations/recovery-and-limitations.md) and
[retrieval copy](conversation-retrieval.md). Composition retains exact LF;
discards expose only counts. Routed replies are never reparsed.
