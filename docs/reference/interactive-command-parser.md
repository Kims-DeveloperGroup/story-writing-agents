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
ignored lead_input list_agents search transcript inspect exchanges history status auth
settings set_language set_persona reset session_list session_create
session_open session_pause conversation_new conversation_list
conversation_list_all conversation_use conversation_subject
conversation_archive help quit progress parse_error
```

`lead_input` carries one literal ordinary line. There is no agent selector,
output path, direct RAG query, or legacy direct-run payload.

## Registry

| Group | Canonical rows |
| --- | --- |
| Agents | `/agents`; `/auth`; `/settings` |
| Session overrides | `/override language LANG \| persona AGENT NAME \| reset` |
| Sessions | `/session list \| create NAME \| resume [NAME] \| pause` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| resume [ID] \| subject SUBJECT \| archive [ID]` |
| Inspection | `/search QUERY`; `/transcript [ID [COUNT] [--from SEQUENCE]]`; `/inspect [DEBATE\|last]`; `/exchanges`; `/history [COUNT]`; `/status`; `/help [COMMAND]` |
| Display | `/progress [on\|off\|toggle]` |
| Exit | `/quit` |

`/?` aliases `/help`; `/toggle` aliases `/progress toggle`. Parsing performs no scope check or I/O; the controller
owns attachment, focus, storage, provider-authentication, settings, picker, and
viewer behavior.

The route registry describes the fixed four roles for configuration and
display. Ordinary input always routes to the Lead; specialists are selected by
the lead/runtime workflow rather than command syntax.

## Line behavior

One final LF or CRLF is framing. Blank Unicode whitespace returns `ignored`.
At character zero, `/` begins command parsing. Every other nonblank line returns
`lead_input` with the text unchanged, including leading whitespace and strings
that look like agent mentions. The controller requires an attached session and
focused active conversation before calling `RuntimeSession.lead_turn()`.

The following legacy names remain removed and return fixed unavailable guidance
rather than an unknown-command suggestion:

```text
/agent /cancel /compose /draft-file /rag /say /send /to
```

That guidance applies only to these retired slash forms, not ordinary input:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

`/run` is not a retained legacy route. It follows the ordinary unknown-command
path, is absent from help and completion, and cannot reach a provider or mutate
session state.

Command keywords are case-sensitive. ASCII space/tab delimit grammar. There is
no quoting, shell expansion, provider call, file read, or network access.

## Management grammar

- `/session resume` and `/conversation resume` return argument-free actions;
  the controller may open a dynamic picker. Explicit identities remain direct.
- `/conversation list --all` must place `--all` first and at most once.
- `/search QUERY` requires a nonblank remainder.
- Bare `/transcript` returns an argument-free action for the controller to open
  an attached-session conversation picker. With arguments, the ID comes first;
  optional count precedes one final `--from`; numeric values are positive ASCII
  digits. Count and start sequence cannot be supplied without an ID.
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
`--from`, and `/inspect last`. It returns `None` for ordinary lead text.
Reference composition is a terminal input concern, not parser syntax; the
submitted line contains `${path}` and optional `#Lx-Ly` indicators literally.
