# Interactive command parser

> **Availability:** this provider-free module owns command/route metadata and
> parsing. The installed [shell](../guides/story-sessions.md) dispatches its
> documented management, conversation, retrieval, and consensus actions.

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

Actions, metadata, and parser are package exports; routes are module-supported.
Management dispatch changes no parser, terminal, or facade surface.

`ActionKind` values:

```text
ignored message routed_message set_target list_agents compose send cancel
run run_fresh draft_file search transcript inspect history status set_language
set_persona reset session_list session_new session_resume session_end
conversation_new conversation_list conversation_list_all conversation_use
conversation_subject conversation_archive help quit parse_error
```

## Registry

`AGENT_ROUTES` follows roster order and owns IDs, aliases, and domain copy.

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

`/agent`, `/?`, and `/exit` alias `/to`, `/help`, and `/quit`. Flags describe
possible provider/artifact use, not shell gates. Parsing performs no I/O.

Registry membership is not main-prompt availability: the installed shell
rejects standalone `/compose`, `/send`, and `/cancel`. Bare `/run` composition
recognizes `/send` and `/cancel`; `/say /send` and `/say /cancel` are exact
aliases.

## Line contract

One final `LF` or `CRLF` is framing; any other line break returns
`parse_error`. Blank Unicode whitespace is `ignored`. At character zero, `/`
means command, `@` means route, and anything else is a verbatim message.

Keywords are case-sensitive; ASCII space/tab delimit grammar. Fixed commands
reject arguments; tokens reject Unicode whitespace and `Cc` controls. There is
no quoting, expansion, shell, provider, or file access.

## Search and transcript grammar

`/search QUERY` removes exactly one ASCII delimiter and preserves the remaining
opaque query. See [Conversation search](conversation-search.md).

`/transcript ID [COUNT] [--from SEQUENCE]` follows this exact order:

1. `ID` is the first control-free token and must precede flags.
2. `COUNT`, when present, must precede `--from`.
3. `--from` may appear once, last, with exactly one `SEQUENCE`.
4. `COUNT` and `SEQUENCE` are positive ASCII digits; leading zeros are valid.

Omitted values stay `None`. Transcript counts above 100 parse because the
memory service owns that maximum; the shell reports `transcript_invalid`.
Identity resolution, archive status, and storage are not parser concerns.

## Management grammar

`/help [COMMAND]` and `/? [COMMAND]` accept a registered command or alias, with
optional leading slash. Omission requests full grouped help; otherwise the
action carries the canonical command.

`/status` and `/reset` take no argument. `/language ARG` takes one control-free
token; `/persona AGENT ARG` takes a route alias and token, returning the
canonical ID. `/history [COUNT]` takes an optional positive ASCII integer;
omission means all. `/inspect [ID|last]` takes at most one control-free token.
Runtime [gates](cli.md#interactive-streams) are not parser concerns.

## Other grammar and failures

Conversation `new` and `subject` preserve their remainder. `list [--all]
[QUERY]`, `use ID`, and `archive [ID]` follow the
[catalog contract](conversation-catalog.md).

`/to` and `/agent` return `set_target`; `@ALIAS TEXT` returns
`routed_message`; `/say TEXT` returns `message`. The parser does not enforce
attachment or selection; the [CLI contract](cli.md#interactive-streams) does.

Invalid/non-string input returns `parse_error`, never provider input. A
suggestion requires one unique case-sensitive Levenshtein match within two.
