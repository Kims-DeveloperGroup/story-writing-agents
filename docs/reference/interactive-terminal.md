# Interactive terminal component

> **Availability:** this module-supported component owns shell input, safe
> records, and bare `/run` composition.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the facade. Its exact `__all__` is `AttachmentKind`, `ComposeMode`, `ConfirmationResult`, `TerminalActionKind`, `TerminalMessageKind`, `InteractiveErrorCode`, `TerminalAction`, `InteractiveErrorSpec`, `INTERACTIVE_ERROR_SPECS`, `InteractiveError`, `sanitize_terminal_text`, and `InteractiveTerminal`.

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
write_conversation_list_entry(conversation_handle, conversation_subject, *, archived, selected) -> None
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

Optional `main_read_line` defaults to `read_line` and serves only `read_main`;
existing positional calls and other input are unchanged. Record width starts at
20. No dispatch, provider, storage, or lifecycle is owned.

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `attached`, `resumed` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

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

The CLI injects it only at main prompts. On capable POSIX, buffer `/` displays
`[suggestions]` followed by one token per row. Compact
registry order (not output layout):

```text
/say /to /agent /agents /language /persona /reset /run /draft-file /session
/conversation /search /transcript /inspect /history /status /help /? /quit /exit
```

Case-sensitive whole-buffer prefixes filter; whitespace, no match, submission,
or control clears it. Tokens neither complete nor reflect scope gates; `/help`
remains authoritative.
The main-invalid `/compose`, `/send`, and `/cancel` are excluded. Other prompts
and unsupported terminals keep ordinary line reads. Editing, control keys, and
restoration are in [Terminal safety](../operations/terminal-safety.md#live-editor-boundary).

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
| Main prompt / end confirmation | `[cancelled] No action was taken.` / `[cancelled] Session end cancelled.` |
| Recoverable advisory | `[waiting] {DISPLAY_NAME} is responding; Ctrl-C cancels this turn.`<br>`[cancelled] {DISPLAY_NAME} turn was interrupted.`<br>`[hint] The story session remains attached; the affected provider thread may restart on the next message.` |
| Consensus before entry | `[cancelled] Consensus run was cancelled before start.` |
| Consensus with trusted durable proof | `[run {DEBATE_ID}] Run interrupted after durable state transition.`<br>`[cancelled] Debate {DEBATE_ID} was marked interrupted.`<br>`[hint] It cannot resume. Use /run to create a new debate.`<br>`[session] "{SESSION_ID}" remains active and attached.` |
| Partial local render | `[cancelled] Search cancelled; attached state is unchanged.` / `[cancelled] Transcript read cancelled; attached state is unchanged.` / `[cancelled] History cancelled; attached state is unchanged.` |

## Errors and other output

The controller classifies 33 codes; the terminal accepts no exceptions.
`write_error()` redacts secrets, caps summary/hint at 512/256 characters, and
writes sanitized records. See
[recovery mappings](../operations/recovery-and-limitations.md) and
[retrieval copy](conversation-retrieval.md). Composition retains exact LF;
discards expose only counts. Routed replies are never reparsed.
