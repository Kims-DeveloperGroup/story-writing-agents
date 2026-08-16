# Interactive terminal component

> **Availability:** this module-supported component presents shell input,
> help, management, search/transcript, retrieval, and routed-reply records;
> bare `/run` uses its composer.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the facade. Its exact `__all__` is `AttachmentKind`, `ComposeMode`, `ConfirmationResult`, `TerminalActionKind`, `TerminalMessageKind`, `InteractiveErrorCode`, `TerminalAction`, `InteractiveErrorSpec`, `INTERACTIVE_ERROR_SPECS`, `InteractiveError`, `sanitize_terminal_text`, and `InteractiveTerminal`.

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

Values are frozen/slotted; minimum width is 20. Callback failures propagate.
It owns no dispatch, provider, storage, or lifecycle.

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

Canonical agents are required. Scope shows session, optional conversation,
subject, and target; rows show handle/lifecycle/selection/subject. Roster/help
derive from parser metadata. Management reuses status, run, and transcript
writers. Confirmation is `[Y/n] ` or `[y/N] `; blank chooses the default.
Ctrl-C/EOF return typed outcomes.

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
```

Subject hits cite conversations; transcript hits require turn/sequence/kind.
Renderers validate IDs, sequences, metadata, and content.

Whitespace content, empty lines, and trailing LF remain in one record. Controls
become visible; events are not trimmed or joined. See
[Terminal safety](../operations/terminal-safety.md).

## Exact cancellation records

| Boundary | Exact records in order |
| --- | --- |
| Main prompt / end confirmation | `[cancelled] No action was taken.` / `[cancelled] Session end cancelled.` |
| Joined advisory | `[waiting] {DISPLAY_NAME} is responding; Ctrl-C cancels this turn.`<br>`[cancelled] {DISPLAY_NAME} turn was interrupted.`<br>`[hint] The story session remains attached; the affected provider thread may restart on the next message.` |
| Consensus before entry | `[cancelled] Consensus run was cancelled before start.` |
| Consensus with trusted durable proof | `[run {DEBATE_ID}] Run interrupted after durable state transition.`<br>`[cancelled] Debate {DEBATE_ID} was marked interrupted.`<br>`[hint] It cannot resume. Use /run to create a new debate.`<br>`[session] "{SESSION_ID}" remains active and attached.` |
| Partial local render | `[cancelled] Search cancelled; attached state is unchanged.` / `[cancelled] Transcript read cancelled; attached state is unchanged.` / `[cancelled] History cancelled; attached state is unchanged.` |

These controller records use validated placeholders. Composer cancellation is
a typed buffer discard.

## Errors and other output

The 33-code registry spans terminal/workspace, session, conversation/retrieval,
provider, consensus/artifact, and cleanup. Retrieval
uses `retrieval_invalid`/`retrieval_budget_exhausted`; observer failure is
`internal_error`. Retrieval copy is in [Bounded retrieval](conversation-retrieval.md).

The controller classifies; the terminal never accepts exceptions. `write_error()`
redacts supplied secrets, caps summary/hint at 512/256 characters, sanitizes,
and writes trusted `[error CODE]`/`[hint]` records. Mapping is in
[Recovery and limitations](../operations/recovery-and-limitations.md).

Composition keeps one exact LF-joined buffer; discard outcomes contain counts,
not text. Routed replies are sanitized once and never reparsed.
