# Interactive terminal component

> **Availability:** this module-supported component presents the installed
> story-session and subject-conversation lobby. Its composition API is not
> dispatched there yet.

Key terms: **callback**—borrowed input/output; **confirmation**—a typed yes/no
decision; **composition**—a call-local buffer; **record**—one output string.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the package
facade. Exact `__all__` contains the types and functions below plus
`INTERACTIVE_ERROR_SPECS`.

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

Value objects are frozen and slotted; `width` is at least 20. Callback failures
propagate. The component owns no parsing, TTY detection, provider/file I/O,
persistence, readline setup, or durable history.

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

Canonical agent IDs are input; display aliases such as `plot` are not valid
`agent_id` values. Attached main prompts need both session and agent IDs.
The installed lobby keeps the target at the lead agent, so its stable prompt
alias is `lead`.

Confirmation uses `[Y/n] ` or `[y/N] `. It accepts case-insensitive `y`, `yes`,
`n`, and `no`; blank chooses the shown default. Invalid input prints a fixed
hint and retries. Ctrl-C and EOF return typed results.

Lobby orientation names the workspace, lobby scope, unavailable provider calls,
and entry commands. Attached orientation names workspace, session, attachment
kind, and target. The installed lobby supplies `history_count=None`, because
conversation management does not read transcripts.

<a id="scenario-compose-one-message"></a>

## Conversation display

Immediately before every attached main prompt, the controller writes one scope
record. The unselected form is:

```text
[context] Session: draft | Conversation: none | Agent: lead
```

The selected form adds `Conversation: HANDLE | Subject: DISPLAY SUBJECT` before
`Agent: lead`. A list row reports its handle, `active` or `archived`, `selected`
or `not selected`, and display subject. Wrapping may add lines, but each method
emits one sanitized record.

The subject request reads one unparsed line; Ctrl-C returns `interrupt` and EOF
returns `eof`. Composition remains a module API: exact `/send` and `/cancel`
control its call-local buffer; discard outcomes return counts but no text.

## Help, output, and errors

Help comes only from parser-owned `COMMANDS`; see the
[command parser](interactive-command-parser.md). Output forms are orientation
or help blocks, `[KIND]`, `[run ID]`, `alias> `, and `[error CODE]` plus
`[hint]`. Every write is one sanitized, LF-ended record; see
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

Lookup distinguishes missing, ambiguous, archived, invalid, and required
selection. Storage uses `workspace_invalid` or `workspace_unsafe`; uncertain
cleanup uses `cleanup_failed`. The controller chooses the category and optional
copy; the terminal never classifies exceptions.
