# Interactive terminal component

> **Availability:** this module-supported component presents the installed
> session lobby. Its composition API is not dispatched there yet.

Key terms: **callback**—borrowed synchronous input or output; **confirmation**—
one typed yes/no decision; **composition**—a call-local multiline buffer;
**record**—one output-callback string.

## Public API

Import from `story_writing_agents.interactive_terminal`, not the package
facade. Exact `__all__` contains the types and functions below plus
`INTERACTIVE_ERROR_SPECS`.

```python
InteractiveTerminal(read_line: Callable[[str], str], write_text: Callable[[str], None], *, width: int = 80)
read_main(*, session_id: str | None = None, agent_id: str | None = None) -> TerminalAction
request_confirmation(message: str, *, default: bool) -> ConfirmationResult
compose(mode: ComposeMode, *, agent_id: str | None = None) -> TerminalAction
write_lobby_orientation(workspace: str) -> None
write_session_orientation(workspace: str, session_id: str, attachment: AttachmentKind, agent_id: str, history_count: int | None = None) -> None
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
propagate. The component performs no parsing, TTY detection, provider or file
I/O, persistence, readline setup, or durable history.

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `attached`, `resumed` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

`line` carries one line and zero counts. `send` carries nonblank LF-joined text
with exact counts. Discard actions carry counts but no text.

## Prompts, confirmation, and orientation

Prompts are exact, including the final space:

```python
"story[lobby]> "
f"story[{session_id}:{alias}]> "
f"compose[chat:{alias}:{line_number}]> "
f"compose[run:{line_number}]> "
```

Canonical agent IDs are input; display aliases such as `plot` are not valid
`agent_id` values. Attached main prompts need both session and agent IDs.

Confirmation appends `[Y/n] ` when yes is the default or `[y/N] ` when no is
the default. Case-insensitive `y`, `yes`, `n`, and `no` are accepted; blank
input chooses the displayed default. Any other answer writes
`[hint] Enter y, yes, n, or no.` and retries. Ctrl-C and EOF return their typed
results without writing a decision.

Lobby orientation is one record naming the workspace, lobby scope, unavailable
provider calls, and entry commands. Attached orientation names workspace,
session, attachment kind, and target. A nonnegative integer `history_count`
adds the visible-history count and room-sharing notice; `None` omits both. The
installed session lobby always supplies `None` because it has no conversation.

## Scenario: compose one message

For `ComposeMode.CHAT` targeting `plot_architect`:

```text
[compose] Target: plot. Use /send to submit or /cancel to discard.
"compose[chat:plot:1]> "  The bell rings.
"compose[chat:plot:2]> "  Mira waits. 🙂
"compose[chat:plot:3]> "  /send
[compose] Sending 2 lines, 29 characters to plot.
```

The result is `TerminalAction(send, text="The bell rings.\nMira waits. 🙂",
line_count=2, character_count=29)`. Counts include the joining LF and count the
emoji as one Unicode code point.

Only exact `/send` and `/cancel` control composition. `/say /send` and
`/say /cancel` append literal lines; near misses are text. Blank-only `/send`
reports `Nothing to send.` without advancing. Cancel, interrupt, and EOF return
no text, retain discarded counts, and write one discard status.

## Help, output, and errors

Help comes only from parser-owned `COMMANDS`; see the
[command parser](interactive-command-parser.md). Output forms are orientation
or help blocks, `[KIND]`, `[run ID]`, `alias> `, and `[error CODE]` plus
`[hint]`. Every write is one sanitized, LF-ended record; see
[Terminal safety](../operations/terminal-safety.md).

The immutable error registry maps 25 codes by area:

| Area | Codes |
| --- | --- |
| Terminal/workspace | `tty_required`, `workspace_not_initialized`, `workspace_invalid`, `workspace_unsafe`, `configuration_invalid`, `command_invalid`, `agent_invalid` |
| Session/transcript | `session_invalid`, `session_exists`, `session_not_found`, `session_in_use`, `session_inactive`, `transcript_invalid` |
| Provider | `provider_preflight_failed`, `provider_exited`, `provider_timed_out`, `provider_protocol_error` |
| Consensus/artifact | `draft_input_invalid`, `consensus_deadlock`, `consensus_failed`, `artifact_conflict` |
| Lifecycle/internal | `operation_cancelled`, `operation_interrupted`, `cleanup_failed`, `internal_error` |

Trusted controller code chooses the code and optional summary, hint, and
secrets. The terminal never classifies exceptions.
