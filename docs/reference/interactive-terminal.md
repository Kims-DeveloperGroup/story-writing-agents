# Interactive terminal component

> **Availability:** this module-supported component presents shell input,
> registry help, management status/run rows, search/transcript records,
> retrieval progress/sources, and routed replies. Standalone `/compose` is not
> dispatched; bare `/run` uses the existing run composer.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the package facade. Its exact `__all__` is `AttachmentKind`, `ComposeMode`, `ConfirmationResult`, `TerminalActionKind`, `TerminalMessageKind`, `InteractiveErrorCode`,
`TerminalAction`, `InteractiveErrorSpec`, `INTERACTIVE_ERROR_SPECS`, `InteractiveError`, `sanitize_terminal_text`, and `InteractiveTerminal`.

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

Canonical agents are required. Attached scope shows session, optional
conversation, subject, and sticky target. Rows show handle, lifecycle,
selection, and subject. Roster/help derive from parser metadata. Management adds
no terminal API: status/directive/history summaries use `write_status`, safe
debate inspection uses `write_run`, and history events reuse the transcript
renderer. Confirmation uses `[Y/n] ` or `[y/N] `; blank chooses the default.
Ctrl-C/EOF are typed.

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

Transcript content may be whitespace-only. Empty lines and trailing LF remain
in one event record under `[content] `. Controls become visible escapes, such
as tab `\x09` and escape `\x1b`; events are neither trimmed nor combined. See
[Terminal safety](../operations/terminal-safety.md).

## Errors and other output

The immutable 33-code registry covers terminal/workspace, session,
conversation/transcript/retrieval, provider, consensus/artifact, and cleanup.
Retrieval adds `retrieval_invalid` and `retrieval_budget_exhausted`; observer
failure maps to `internal_error`. Progress and source records reuse
`write_status(TerminalMessageKind.CONTEXT, ...)`. Exact copy and recovery live in
[Bounded retrieval](conversation-retrieval.md).

The controller chooses categories; the terminal never accepts or classifies an
exception. `write_error()` redacts supplied secret variants, limits summary and
hint to 512/256 characters, sanitizes, and writes trusted `[error CODE]` and
`[hint]` records. Exact recovery mapping is in
[Recovery and limitations](../operations/recovery-and-limitations.md).

Composition keeps an exact call-local LF-joined buffer. `/send` and `/cancel`
control that buffer only; discard outcomes carry counts but no text. Routed
reply content is sanitized and displayed once, never reparsed.
