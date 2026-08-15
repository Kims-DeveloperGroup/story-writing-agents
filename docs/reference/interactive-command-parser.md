# Interactive command parser

> **Availability:** this provider-free module owns command and route metadata and
> parsing. The installed [shell](../guides/story-sessions.md) dispatches help,
> routing, turns, sessions, conversations, and quit; `/search` and `/transcript`
> remain unknown.

Key terms: **registry**—immutable command or route metadata; **action**—one
typed parse result; **opaque remainder**—payload preserved after its delimiter;
**parse error**—invalid input returned as a value, not an exception.

```python
AgentRouteSpec(agent_id: str, prompt_alias: str, aliases: tuple[str, ...],
               advisory_domain: str)
AGENT_ROUTES: tuple[AgentRouteSpec, ...]
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

All three dataclasses are frozen and slotted. `AgentRouteSpec` and
`AGENT_ROUTES` are supported from `story_writing_agents.interactive_commands`,
not re-exported by the package facade. `InteractiveAction.kind` is positional;
its payload fields are keyword-only. The exact `ActionKind` values are:

```text
ignored message routed_message set_target list_agents compose send cancel
run run_fresh draft_file inspect history status set_language set_persona reset
session_list session_new session_resume session_end
conversation_new conversation_list conversation_list_all conversation_use
conversation_subject conversation_archive help quit parse_error
```

Payloads follow syntax; unused fields are rejected. Conversation `list`, `new`,
and `archive` allow optional `argument`; `use` and `subject` require it.

## Registry

`AGENT_ROUTES` follows roster order and owns IDs, the preferred prompt alias,
accepted aliases, and advisory-domain copy:

| ID | Aliases | Advisory domain |
| --- | --- | --- |
| `lead_orchestrator` | `lead`, `orchestrator`, `lead_orchestrator` | Moderation, synthesis, and finalization. |
| `plot_architect` | `plot`, `plot_architect` | Plot, structure, flow, and pacing. |
| `character_custodian` | `character`, `character_custodian` | Characters, relationships, world, and continuity. |
| `execution_specialist` | `execution`, `prose`, `execution_specialist` | Drafting, prose, point of view, tone, grammar, and format. |

| Group | Canonical registry rows |
| --- | --- |
| Conversation | `/compose`; `/send`; `/cancel`; `/say TEXT` |
| Agents | `/to NAME`; `/agents`; `/language LANG`; `/persona AGENT NAME`; `/reset` |
| Consensus | `/run [PROMPT] \| /run --fresh PROMPT`; `/draft-file PATH` |
| Sessions | `/session list \| new NAME \| resume [NAME] \| end` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| use ID \| subject SUBJECT \| archive [ID]` |
| Inspection | `/inspect [DEBATE\|last]`; `/history [COUNT]`; `/status`; `/help [COMMAND]` |
| Exit | `/quit` |

`/agent`, `/?`, and `/exit` alias `/to`, `/help`, and `/quit`, respectively.
`/say`, `/run`, and `/draft-file` have the provider flag; the last two also have
the artifact flag. Flags describe capability, not shell availability. Parsing
performs no I/O.

## Parsing contract

One final `LF` or `CRLF` is framing; other line breaks return `parse_error`.
Blank Unicode whitespace is `ignored`. At character zero, `/` means command,
`@` means route, and anything else is a verbatim message.

Keywords are case-sensitive; ASCII space/tab delimit grammar. Fixed commands
reject arguments; tokens reject Unicode whitespace and `Cc` controls. History
counts are positive ASCII digits; `/run` treats only first-token `--fresh` as a
flag.

`/say`, `@` routing, `/run`, and `/draft-file` remove one delimiter and preserve
the rest. There is no quoting, expansion, shell parsing, or file I/O.

## Conversation grammar

`new [SUBJECT]` preserves display text after one delimiter; omission prompts
once with `conversation[subject]> `. `subject SUBJECT` requires content.
Validation normalizes for checks, while storage preserves accepted display text.

`list [--all] [QUERY]` strips delimiter runs. `--all` appears at most once and
first. The preserved query is normalized for substring matching. See the
[catalog contract](conversation-catalog.md).

`use ID` requires one control-free token. `archive [ID]` accepts zero or one.
The controller resolves full IDs or case-sensitive unique prefixes.

`/to` and `/agent` return `set_target`; `@ALIAS TEXT` returns `routed_message`;
`/say TEXT` returns an ordinary `message`. The parser does not enforce session
or conversation state; the [CLI contract](cli.md#interactive-streams) does.
`/help` accepts a leading slash and normalizes command aliases.

Invalid or non-string input returns `parse_error`; it never becomes provider
input. A suggestion appears only for a unique case-sensitive Levenshtein match
within distance two in the applicable command, subcommand, or agent namespace.
