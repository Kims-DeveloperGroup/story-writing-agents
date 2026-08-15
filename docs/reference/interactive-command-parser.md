# Interactive command parser

> **Availability:** `COMMANDS` and `parse_interactive_line()` are provider-free
> Python APIs. The installed [lobby](../guides/story-sessions.md) dispatches
> help, story-session commands, subject-conversation commands, and quit.
> `/search` and `/transcript` remain unknown.

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
session_list session_new session_resume session_end
conversation_new conversation_list conversation_list_all conversation_use
conversation_subject conversation_archive help quit parse_error
```

Conversation `list`, `new`, and `archive` allow an optional `argument`; `use`
and `subject` require one. Other payloads follow their syntax: message content
uses `text`, routing uses `agent_id`, history uses positive `count`, and
`parse_error` uses nonempty `error` plus optional `suggestion`. Unused fields
are rejected.

## Registry

| Group | Canonical registry rows |
| --- | --- |
| Conversation | `/compose`; `/send`; `/cancel`; `/say TEXT` |
| Agents | `/to NAME`; `/agents`; `/language LANG`; `/persona AGENT NAME`; `/reset` |
| Consensus | `/run [PROMPT] \| /run --fresh PROMPT`; `/draft-file PATH` |
| Sessions | `/session list \| new NAME \| resume [NAME] \| end` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| use ID \| subject SUBJECT \| archive [ID]` |
| Inspection | `/inspect [DEBATE\|last]`; `/history [COUNT]`; `/status`; `/help [COMMAND]` |
| Exit | `/quit` |

Aliases are `/agent` for `/to`, `/?` for `/help`, and `/exit` for `/quit`.
Only `/say`, `/run`, and `/draft-file` have the provider flag; only the last two
have the artifact-write flag. Each row stores a help example. Flags describe a
full dispatcher, not current lobby availability. Parsing performs no I/O.

## Parsing contract

One terminal `LF` or `CRLF` is framing; any other line break returns
`parse_error`. Blank Unicode whitespace returns `ignored`. Otherwise untrimmed
character zero classifies `/` as command, `@` as routing, and all else as a
verbatim message. Leading whitespace therefore prevents command parsing.

Keywords and aliases are case-sensitive; only ASCII space and tab delimit
grammar. Fixed commands reject arguments. Tokens reject Unicode whitespace and
`Cc` controls. History count is positive ASCII digits.

`/say`, `@` routing, ordinary `/run`, and `/draft-file` remove one delimiter and
preserve the rest. There is no quoting, shell parsing, expansion, or file I/O.
`/run` recognizes only first-token `--fresh`, followed by text.

## Conversation grammar

`new [SUBJECT]` preserves display text after one delimiter; when omitted, the
controller prompts once with `conversation[subject]> `. `subject SUBJECT` uses
the same rule but requires content. Validation checks a normalized form while
the catalog stores accepted display text unchanged.

`list [--all] [QUERY]` strips delimiter runs. `--all` must appear once and
first; another leading `--` token is invalid. The action preserves the query;
the controller normalizes it for substring matching. See the
[catalog contract](conversation-catalog.md).

`use ID` requires one control-free token. `archive [ID]` accepts zero or one.
The parser does not validate filesystem-safe IDs; the controller resolves a
full ID or a case-sensitive unique prefix as described by the catalog reference.

Agent aliases normalize as follows: `lead`, `orchestrator`, and
`lead_orchestrator`; `plot` and `plot_architect`; `character` and
`character_custodian`; `execution`, `prose`, and `execution_specialist`.
`/help` accepts a leading slash and normalizes aliases.

Invalid or non-string input returns `parse_error`; it never becomes provider
input. A suggestion appears only for a unique case-sensitive Levenshtein match
within distance two in the applicable command, subcommand, or agent namespace.
