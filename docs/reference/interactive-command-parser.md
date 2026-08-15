# Interactive command parser

> **Availability:** `COMMANDS` and `parse_interactive_line()` are provider-free
> Python APIs. The [terminal component](interactive-terminal.md) presents lines,
> but no installed controller executes commands;
> `/conversation`, `/search`, and `/transcript` remain unknown.

Key terms: **registry**—immutable command-help metadata; **action**—one typed
parse result; **opaque remainder**—payload preserved after its delimiter;
**parse error**—invalid input returned as a value instead of an exception.

```python
InteractiveAction(
    kind: ActionKind,
    *, text: str | None = None,
    agent_id: str | None = None,
    argument: str | None = None,
    count: int | None = None,
    error: str | None = None,
    suggestion: str | None = None,
)
CommandSpec(
    group: str,
    syntax: str,
    aliases: tuple[str, ...],
    may_call_provider: bool,
    may_write_artifacts: bool,
    example: str,
)
COMMANDS: tuple[CommandSpec, ...]
parse_interactive_line(line: str) -> InteractiveAction
```

Both dataclasses are frozen and slotted. `kind` is positional; payload fields
are keyword-only. The exact `ActionKind` values are:

```text
ignored message routed_message set_target list_agents compose send cancel
run run_fresh draft_file inspect history status set_language set_persona reset
session_list session_new session_resume session_end help quit parse_error
```

No-field actions are `ignored`, `list_agents`, `compose`, `send`, `cancel`,
`status`, `reset`, `session_list`, `session_end`, and `quit`. `message` requires
`text`; `routed_message` requires `agent_id` and `text`; `set_target` requires
`agent_id`; `run` has optional `text`; `run_fresh` requires `text`;
`draft_file`, `set_language`, and `session_new` require `argument`; `inspect`,
`session_resume`, and `help` allow optional `argument`; `history` allows an
optional positive `count`; `set_persona` requires `agent_id` and `argument`;
`parse_error` requires nonempty `error` and permits `suggestion`.

## Registry

| Group | Canonical syntax | Aliases | Provider? | Writes? |
| --- | --- | --- | --- | --- |
| Conversation | `/compose` | — | No | No |
| Conversation | `/send` | — | No | No |
| Conversation | `/cancel` | — | No | No |
| Conversation | `/say TEXT` | — | Yes | No |
| Agents | `/to NAME` | `/agent` | No | No |
| Agents | `/agents` | — | No | No |
| Agents | `/language LANG` | — | No | No |
| Agents | `/persona AGENT NAME` | — | No | No |
| Agents | `/reset` | — | No | No |
| Consensus | `/run [PROMPT] \| /run --fresh PROMPT` | — | Yes | Yes |
| Consensus | `/draft-file PATH` | — | Yes | Yes |
| Sessions | `/session list \| new NAME \| resume [NAME] \| end` | — | No | No |
| Inspection | `/inspect [DEBATE\|last]` | — | No | No |
| Inspection | `/history [COUNT]` | — | No | No |
| Inspection | `/status` | — | No | No |
| Inspection | `/help [COMMAND]` | `/?` | No | No |
| Exit | `/quit` | `/exit` | No | No |

The immutable registry also stores the examples shown by the implementation:
`/compose`, `/send`, `/cancel`, `/say /run is literal text`, `/to plot`,
`/agents`, `/language ko`, `/persona plot meticulous`, `/reset`,
`/run Outline chapter one`, `/draft-file drafts/chapter-01.md`,
`/session new chapter-01`, `/inspect last`, `/history 20`, `/status`,
`/help run`, and `/quit`. Capability flags describe what a future dispatcher
could do. Registry and parser perform no I/O or mutation; terminal help only
projects this registry.

## Parsing contract

One terminal `LF` or `CRLF` is removed as framing. Any remaining line break,
including bare terminal `CR`, returns `parse_error`. Blank Unicode-whitespace
input returns `ignored`. Otherwise character zero classifies the untrimmed
line: `/` starts a command, `@` starts routing, and anything else is a verbatim
message. Leading whitespace therefore makes command-looking text a message.

Grammar keywords and aliases are case-sensitive. ASCII space and tab are the
only delimiters. Fixed commands reject arguments; tokens cannot be empty or
contain Unicode whitespace or `Cc` controls. History count accepts ASCII
digits only and must be positive.

`/say`, `@` routing, ordinary `/run`, and `/draft-file` preserve an opaque
remainder after removing exactly one separating space or tab. There is no
quoting, escaping, shell parsing, path expansion, nested command parsing, or
file opening. `/run` treats a first prompt token beginning `--` as a flag; only
exact `--fresh` is valid and requires text.

Agent aliases normalize as follows: `lead`, `orchestrator`, and
`lead_orchestrator`; `plot` and `plot_architect`; `character` and
`character_custodian`; `execution`, `prose`, and `execution_specialist`.
`/help` accepts a leading slash and normalizes aliases.

Invalid input never raises a parse exception or becomes provider input. It
returns `parse_error`; a non-string input does the same. A suggestion appears
only for a uniquely nearest candidate, measured case-sensitively by Levenshtein
distance, when that distance is two or less. Candidates come from the applicable
command, session-subcommand, or agent-alias namespace; command suggestions use
canonical slash names and agent suggestions use canonical `@agent_id` values.
