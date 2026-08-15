# Interactive terminal component

> **Availability:** this module-supported component presents shell input,
> roster, and routed-turn output. Composition is not dispatched yet.

Key terms: **callback**—borrowed I/O; **confirmation**—typed yes/no;
**composition**—a call-local buffer; **record**—one output string.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the facade. Exact
`__all__` also contains the enums below and `INTERACTIVE_ERROR_SPECS`.

```python
InteractiveTerminal(read_line: Callable[[str], str], write_text: Callable[[str], None], *, width: int = 80)
read_main(*, session_id: str | None = None, agent_id: str | None = None) -> TerminalAction
request_conversation_subject() -> TerminalAction
request_confirmation(message: str, *, default: bool) -> ConfirmationResult
compose(mode: ComposeMode, *, agent_id: str | None = None) -> TerminalAction
write_lobby_orientation(workspace: str) -> None
write_session_orientation(workspace: str, session_id: str, attachment: AttachmentKind, agent_id: str, history_count: int | None = None) -> None
write_conversation_scope(*, session_id: str, conversation_handle: str | None, conversation_subject: str | None, agent_id: str) -> None
write_conversation_list_entry(conversation_handle: str, conversation_subject: str, *, archived: bool, selected: bool) -> None
write_agent_roster() -> None
write_status(kind: TerminalMessageKind, text: str) -> None
write_run(debate_id: str, text: str) -> None
write_agent(agent_id: str, text: str) -> None
write_help(command: str | None = None) -> None
write_error(error: InteractiveError, *, secrets: Iterable[str] = ()) -> None
sanitize_terminal_text(text: str) -> str
TerminalAction(kind: TerminalActionKind, *, text: str | None = None, line_count: int = 0, character_count: int = 0)
InteractiveErrorSpec(summary: str, hint: str)
InteractiveError(code: InteractiveErrorCode, *, summary: str | None = None, hint: str | None = None)
```

Value objects are frozen/slotted; `width` is at least 20. Callback failures
propagate. The component owns no parsing, TTY detection, provider or file I/O,
persistence, or history.

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `attached`, `resumed` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

## Prompts, confirmation, and orientation

Prompts are exact, including the final space:

```python
"story[lobby]> "
f"story[{session_id}:{alias}]> "
"conversation[subject]> "
f"compose[chat:{alias}:{line_number}]> "
f"compose[run:{line_number}]> "
```

Methods accept canonical agent IDs, not aliases such as `plot`. Attached prompts
need session and agent IDs. The controller defaults to `lead`; `/to` or `/agent`
changes the sticky alias, while `@AGENT` leaves it unchanged.

Confirmation uses `[Y/n] ` or `[y/N] ` and accepts `y/yes/n/no`; blank chooses
the default. Invalid input retries; Ctrl-C and EOF return typed results.

Orientation names workspace and scope; attached output adds session, attachment
kind, and target. The shell supplies `history_count=None` because it has no
history-view command.

<a id="scenario-compose-one-message"></a>

## Conversation display

Before each attached prompt, the controller writes scope. Unselected form:

```text
[context] Session: draft | Conversation: none | Agent: lead
```

The selected form adds handle and subject before `Agent: ALIAS`. List rows show
handle, lifecycle, selection, and subject. Each method emits one sanitized
record, possibly wrapped.

The subject request reads one unparsed line; Ctrl-C/EOF return `interrupt`/`eof`.
Composition remains a module API: exact `/send` and `/cancel` control its
call-local buffer, and discard outcomes carry counts but no text.

## Roster, turn output, and errors

Help comes from parser-owned `COMMANDS`. The roster is one block of four
`[agent]` entries containing display name, route ID, aliases, and advisory domain.

Turn output is `[routing]`, `[waiting]`, optional `[context]` only when result
context is truncated, then `alias> CONTENT`. Content is sanitized, never
reparsed. Other forms include orientation/help, `[KIND]`, `[run ID]`, and typed
error/hint records. Every write is sanitized and LF-ended; see
[Terminal safety](../operations/terminal-safety.md).

The immutable error registry maps 31 codes by area:

| Area | Codes |
| --- | --- |
| Terminal/workspace | `tty_required`, `workspace_not_initialized`, `workspace_invalid`, `workspace_unsafe`, `configuration_invalid`, `command_invalid`, `agent_invalid` |
| Session | `session_invalid`, `session_exists`, `session_not_found`, `session_in_use`, `session_inactive` |
| Conversation/transcript | `conversation_required`, `conversation_invalid`, `conversation_not_found`, `conversation_ambiguous`, `conversation_archived`, `conversation_create_failed`, `transcript_invalid` |
| Provider | `provider_preflight_failed`, `provider_exited`, `provider_timed_out`, `provider_protocol_error` |
| Consensus/artifact | `draft_input_invalid`, `consensus_deadlock`, `consensus_failed`, `artifact_conflict` |
| Lifecycle/internal | `operation_cancelled`, `operation_interrupted`, `cleanup_failed`, `internal_error` |

The controller chooses categories and optional copy; the terminal never
classifies exceptions. See the [command parser](interactive-command-parser.md).
