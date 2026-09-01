# Interactive command parser

The parser is provider-, filesystem-, subprocess-, and network-free. It
recognizes the current management/inspection language and returns immutable
`InteractiveAction` values.

## Public contract

```python
AgentRouteSpec(agent_id: str, prompt_alias: str,
               aliases: tuple[str, ...], advisory_domain: str)
AGENT_ROUTES: tuple[AgentRouteSpec, ...]
InteractiveAction(kind: ActionKind, *, text: str | None = None,
                  agent_id: str | None = None,
                  argument: str | None = None,
                  count: int | None = None,
                  start_sequence: int | None = None,
                  error: str | None = None,
                  suggestion: str | None = None)
CommandSpec(group: str, syntax: str, aliases: tuple[str, ...],
            may_call_provider: bool, may_write_artifacts: bool, example: str)
COMMANDS: tuple[CommandSpec, ...]
parse_interactive_line(line: str) -> InteractiveAction
```

Current `ActionKind` values are:

```text
ignored list_agents search transcript inspect exchanges history status auth
settings set_language set_persona reset session_list session_create
session_open session_pause conversation_new conversation_list
conversation_list_all conversation_use conversation_subject
conversation_archive help quit parse_error
```

There is no action payload for provider text, output paths, direct RAG queries,
or story execution.

## Registry

| Group | Canonical rows |
| --- | --- |
| Agents | `/agents`; `/auth`; `/settings` |
| Session overrides | `/override language LANG \| persona AGENT NAME \| reset` |
| Sessions | `/session list \| create NAME \| resume [NAME] \| pause` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| resume [ID] \| subject SUBJECT \| archive [ID]` |
| Inspection | `/search QUERY`; `/transcript ID [COUNT] [--from SEQUENCE]`; `/inspect [DEBATE\|last]`; `/exchanges`; `/history [COUNT]`; `/status`; `/help [COMMAND]` |
| Exit | `/quit` |

`/?` aliases `/help`. Parsing performs no scope check or I/O; the controller
owns attachment, focus, storage, provider-authentication, settings, picker, and
viewer behavior.

The route registry still describes the fixed four agent roles for configuration
and display. It is not a current message-routing surface.

## Line behavior

One final LF or CRLF is framing. Blank Unicode whitespace returns `ignored`.
At character zero, `/` begins command parsing. Other nonblank input returns a
`parse_error` with the direct-execution-unavailable guidance; a leading agent
route is treated the same way.

The following names are recognized specifically as removed execution input and
return the same fixed guidance rather than an unknown-command suggestion:

```text
/agent /cancel /compose /draft-file /rag /run /say /send /to
```

Exact guidance:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

Command keywords are case-sensitive. ASCII space/tab delimit grammar. There is
no quoting, shell expansion, provider call, file read, or network access.

## Management grammar

- `/session resume` and `/conversation resume` return argument-free actions;
  the controller may open a dynamic picker. Explicit identities remain direct.
- `/conversation list --all` must place `--all` first and at most once.
- `/search QUERY` requires a nonblank remainder.
- `/transcript ID [COUNT] [--from SEQUENCE]` requires the ID first; optional
  count precedes one final `--from`; numeric values are positive ASCII digits.
- `/history [COUNT]` accepts one positive bounded ASCII count.
- `/inspect` accepts no argument or one control-free debate ID/`last`.
- `/auth`, `/settings`, `/agents`, `/exchanges`, `/status`, `/quit`, and
  `/session pause` reject arguments.
- `/override persona` resolves a configured agent alias to its canonical ID.

Invalid input returns `parse_error`; parsing never raises a provider operation.

## Completion

The pure command-completion projection exposes only retained registry commands
and their finite subcommands/options. It provides session/conversation resume
positions, `/override` subcommands, `/conversation list --all`, transcript
`--from`, and `/inspect last`. It returns `None` for ordinary text and leading
agent routes.

The parser no longer projects a provider-text span. Consequently the current
controller cannot activate `$`/`$$` artifact selection from main input. Generic
terminal viewer components remain separate and are not parser commands.
