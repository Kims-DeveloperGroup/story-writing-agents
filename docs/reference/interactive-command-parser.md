# Interactive command parser

> **Availability:** this provider-free module owns command and route metadata and
> parsing. The installed [shell](../guides/story-sessions.md) dispatches
> `/search` and `/transcript`; `/history` is parsed but remains unavailable.

Key terms: **registry**—immutable metadata; **action**—one typed parse result;
**opaque remainder**—text preserved after one delimiter; **parse error**—an
invalid result value, not an exception.

## Public contract

```python
AgentRouteSpec(agent_id: str, prompt_alias: str, aliases: tuple[str, ...],
               advisory_domain: str)
AGENT_ROUTES: tuple[AgentRouteSpec, ...]
InteractiveAction(kind: ActionKind, *, text: str | None = None,
                  agent_id: str | None = None,
                  argument: str | None = None, count: int | None = None,
                  start_sequence: int | None = None,
                  error: str | None = None, suggestion: str | None = None)
CommandSpec(group: str, syntax: str, aliases: tuple[str, ...],
            may_call_provider: bool, may_write_artifacts: bool, example: str)
COMMANDS: tuple[CommandSpec, ...]
parse_interactive_line(line: str) -> InteractiveAction
```

Actions, command metadata, and the parser are package exports; route metadata
is supported only from
`story_writing_agents.interactive_commands`. Search and transcript are additive
cases, so established import paths and payloads remain compatible.

`ActionKind` values:

```text
ignored message routed_message set_target list_agents compose send cancel
run run_fresh draft_file search transcript inspect history status set_language
set_persona reset session_list session_new session_resume session_end
conversation_new conversation_list conversation_list_all conversation_use
conversation_subject conversation_archive help quit parse_error
```

Unused payload fields are rejected. `search` requires `text`; `transcript`
requires `argument` and optionally carries positive `count` and
`start_sequence` values.

## Registry

`AGENT_ROUTES` follows roster order and owns IDs, prompt aliases, accepted
aliases, and advisory-domain copy.

| ID | Accepted aliases |
| --- | --- |
| `lead_orchestrator` | `lead`, `orchestrator`, `lead_orchestrator` |
| `plot_architect` | `plot`, `plot_architect` |
| `character_custodian` | `character`, `character_custodian` |
| `execution_specialist` | `execution`, `prose`, `execution_specialist` |

| Group | Canonical registry rows |
| --- | --- |
| Conversation | `/compose`; `/send`; `/cancel`; `/say TEXT` |
| Agents | `/to NAME`; `/agents`; `/language LANG`; `/persona AGENT NAME`; `/reset` |
| Consensus | `/run [PROMPT] \| /run --fresh PROMPT`; `/draft-file PATH` |
| Sessions | `/session list \| new NAME \| resume [NAME] \| end` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| use ID \| subject SUBJECT \| archive [ID]` |
| Inspection | `/search QUERY`; `/transcript ID [COUNT] [--from SEQUENCE]`; `/inspect [DEBATE\|last]`; `/history [COUNT]`; `/status`; `/help [COMMAND]` |
| Exit | `/quit` |

`/agent`, `/?`, and `/exit` alias `/to`, `/help`, and `/quit`. Capability flags
describe possible provider or artifact use, not shell availability. Parsing
performs no I/O.

## Line contract

One final `LF` or `CRLF` is framing; any other line break returns
`parse_error`. Blank Unicode whitespace is `ignored`. At character zero, `/`
means command, `@` means route, and anything else is a verbatim message.

Keywords are case-sensitive. ASCII space and tab delimit grammar. Fixed
commands reject arguments; tokens reject Unicode whitespace and `Cc` controls.
There is no quoting, expansion, shell parsing, provider call, or file access.

## Search and transcript grammar

`/search QUERY` requires an opaque query. The parser removes exactly one ASCII
space or tab after `/search` and preserves every later character, including
extra delimiters and trailing whitespace. Service-level query safety and
normalization are defined by [Conversation search](conversation-search.md).

`/transcript ID [COUNT] [--from SEQUENCE]` follows this exact order:

1. `ID` is the first control-free token and must precede flags.
2. `COUNT`, when present, must precede `--from`.
3. `--from` may appear once, last, with exactly one `SEQUENCE`.
4. `COUNT` and `SEQUENCE` are positive ASCII digits; leading zeros are valid.

Omitted values remain `None`; the shell applies the read defaults. The parser
accepts counts above 100 because the memory-service policy owns that maximum;
the shell reports such a request as `transcript_invalid`. Identifier length,
prefix uniqueness, archive status, and storage are not parser concerns.

## Other grammar and failures

Conversation `new` and `subject` preserve text after one delimiter. `list
[--all] [QUERY]` strips delimiter runs; `--all` appears once at most and first.
`use ID` takes one token; `archive [ID]` takes zero or one. See the
[catalog contract](conversation-catalog.md).

`/to` and `/agent` return `set_target`; `@ALIAS TEXT` returns
`routed_message`; `/say TEXT` returns `message`. The parser does not enforce
attachment or selection; the [CLI contract](cli.md#interactive-streams) does.

Invalid or non-string input returns `parse_error` and never becomes provider
input. A suggestion appears only for one unique case-sensitive Levenshtein
match within distance two in the applicable namespace.
