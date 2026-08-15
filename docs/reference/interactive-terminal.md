# Interactive terminal component

> **Availability:** this module-supported component presents shell input,
> search hits, transcript events, roster data, and routed-turn output.
> Composition remains a module API and is not shell-dispatched.

Key terms: **callback**—borrowed synchronous I/O; **record**—one string passed
to the writer; **primitive renderer**—presentation that accepts validated
values but performs no search or storage; **visible escape**—safe text for a
terminal control.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the package
facade. Exact `__all__` also contains the enums and error registry below.

```python
InteractiveTerminal(read_line, write_text, *, width: int = 80)
read_main(*, session_id=None, agent_id=None) -> TerminalAction
request_conversation_subject() -> TerminalAction
request_confirmation(message: str, *, default: bool) -> ConfirmationResult
compose(mode: ComposeMode, *, agent_id=None) -> TerminalAction
write_lobby_orientation(workspace: str) -> None
write_session_orientation(workspace, session_id, attachment, agent_id, history_count=None) -> None
write_conversation_scope(*, session_id, conversation_handle, conversation_subject, agent_id) -> None
write_conversation_list_entry(conversation_handle, conversation_subject, *, archived, selected) -> None
write_conversation_search_hit(conversation_handle, conversation_subject, match_kind, snippet, *, archived,
                              citation_turn_id=None, citation_sequence=None, citation_kind=None) -> None
write_conversation_transcript_event(conversation_handle, *, sequence, turn_id, kind, sender, recipient, content) -> None
write_agent_roster() -> None
write_status(kind: TerminalMessageKind, text: str) -> None
write_run(debate_id: str, text: str) -> None
write_agent(agent_id: str, text: str) -> None
write_help(command: str | None = None) -> None
write_error(error: InteractiveError, *, secrets=()) -> None
sanitize_terminal_text(text: str) -> str
```

Value objects are frozen and slotted; width is at least 20. Callback failures
propagate. The component owns no parser dispatch, TTY detection, provider,
filesystem, catalog, transcript, cursor, or resource lifecycle.

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

Canonical agent IDs are required. Attached scope shows session, optional
conversation handle and subject, and sticky target. Conversation rows show
handle, lifecycle, selection, and subject. The roster derives four entries
from parser-owned route metadata. Help derives from parser-owned `COMMANDS`.
Confirmation uses `[Y/n] ` or `[y/N] ` and accepts `y/yes/n/no`; blank chooses
the default. Ctrl-C and EOF return typed results.

## Exact search and transcript records

The following are unwrapped templates; values replace braces before safe
width-wrapping. Each hit and each event is one callback record.

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

[cancelled] Search cancelled; attached state is unchanged.
[cancelled] Transcript read cancelled; attached state is unchanged.
```

Subject matches use the conversation-only citation; transcript matches require
the complete turn/sequence/kind trio. Primitive renderers require exact safe
IDs, positive integer sequences, nonblank metadata, and nonempty content.

Transcript content may consist only of whitespace. Internal empty lines and a
trailing LF remain visible lines in the same event record; later source lines
use hanging indentation under `[content] `. Controls become visible escapes,
so tab is `\x09` and escape is `\x1b`. The terminal neither trims content nor
combines events. See [Terminal safety](../operations/terminal-safety.md).

## Errors and other output

The immutable 31-code registry covers terminal/workspace, session,
conversation/transcript, provider, consensus/artifact, and cleanup failures.
Search/query parsing uses `command_invalid`; transcript reads can use
`conversation_invalid`, `conversation_not_found`, `conversation_ambiguous`,
`workspace_invalid`, `workspace_unsafe`, or `transcript_invalid`.

The controller chooses categories; the terminal never accepts or classifies an
exception. `write_error()` redacts supplied secret variants, limits summary and
hint to 512/256 characters, sanitizes, and writes trusted `[error CODE]` and
`[hint]` records. Exact recovery mapping is in
[Recovery and limitations](../operations/recovery-and-limitations.md).

Composition keeps an exact call-local LF-joined buffer. `/send` and `/cancel`
control that buffer only; discard outcomes carry counts but no text. Routed
reply content is sanitized and displayed once, never reparsed.
