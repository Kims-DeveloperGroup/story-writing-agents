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
`SETTINGS` and its registry row are additive supported parser surface, including
through the existing root-facade exports. Export names/order and the terminal
supported surface remain unchanged.

`ActionKind` values:

```text
ignored message routed_message set_target list_agents compose send cancel
run run_fresh draft_file search transcript inspect history status set_language
    set_persona reset settings session_list session_create session_open session_pause
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
| Agents | `/to NAME`; `/agents`; `/language LANG`; `/persona AGENT NAME`; `/reset`; `/settings` |
| Consensus | `/run [PROMPT] \| /run --fresh PROMPT`; `/draft-file PATH` |
| Sessions | `/session list \| create NAME \| open [NAME] \| pause` |
| Conversations | `/conversation new [SUBJECT] \| list [--all] [QUERY] \| use ID \| subject SUBJECT \| archive [ID]` |
| Inspection | `/search QUERY`; `/transcript ID [COUNT] [--from SEQUENCE]`; `/inspect [DEBATE\|last]`; `/history [COUNT]`; `/status`; `/help [COMMAND]` |
| Exit | `/quit` |

`/agent`, `/?`, and `/exit` alias `/to`, `/help`, and `/quit`. Flags describe
possible provider/artifact use, not shell gates. Parsing performs no I/O.
`/settings` is provider-free and has `may_write_artifacts=True` because an
explicit Save may replace configuration documents.

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

## Main-command completion projection

The module also supplies a package-internal, pure completion projection for
capable main-prompt editing. It does not extend the supported public contract
above, call `parse_interactive_line()`, or perform fuzzy matching, catalog
lookup, I/O, authorization, or mutation. Given an exact buffer and cursor, it
returns one grammar-position identity, the complete active-token span, and
ordered literal replacements, or `None` for a dynamic/free-value position.

ASCII space and tab alone delimit the cursor-aware span, matching the parser's
command grammar. Matching is exact and case-sensitive. The implemented finite
positions are:

| Position | Ordered replacements |
| --- | --- |
| Root command | The 21 main-available canonical commands and aliases in registry order; standalone `/compose`, `/send`, and `/cancel` are excluded. |
| `/session` | `list`, `create`, `open`, `pause` |
| `/conversation` | `new`, `list`, `use`, `subject`, `archive` |
| `/to`, `/agent`, `/persona` agent | The 10 canonical IDs and accepted aliases in route order. |
| `/run` option | `--fresh` |
| `/conversation list` option | `--all` |
| `/help`, `/?` command | The 21 root replacements plus `/compose`, `/send`, and `/cancel` in registry order. |
| `/inspect` argument | `last` |
| `/transcript ID [COUNT]` option | `--from` |

Fixed-only positions keep their projection when a typed prefix has no match,
allowing the terminal to render an explicit empty palette. Mixed fixed/free
positions project only option-looking or still-matching fixed text; prompt,
query, subject, path, identity, count, sequence, and other runtime values stay
free text with no palette. Help additionally accepts a bare active prefix for
slash-prefixed replacement, so `/help ru` can become `/help /run`. Completion
replaces input only; Enter still sends the final visible buffer to the parser
once.

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

`/status`, `/reset`, and alias-free `/settings` take no argument. `/settings`
returns the additive `settings` action and is global; its interactive editing,
validation, and persistence are controller/settings concerns. `/language ARG`
takes one control-free token; `/persona AGENT ARG` takes a route alias and
token, returning the canonical ID. `/history [COUNT]` takes an optional positive
ASCII integer; omission means all. `/inspect [ID|last]` takes at most one
control-free token. Runtime [gates](cli.md#interactive-streams) are not parser
concerns.

## Other grammar and failures

Conversation `new` and `subject` preserve their remainder. `list [--all]
[QUERY]`, `use ID`, and `archive [ID]` follow the
[catalog contract](conversation-catalog.md).

`/to` and `/agent` return `set_target`; `@ALIAS TEXT` returns
`routed_message`; `/say TEXT` returns `message`. The parser does not enforce
current attachment or conversation focus; the [CLI contract](cli.md#interactive-streams) does.

Invalid/non-string input returns `parse_error`, never provider input. A
suggestion requires one unique case-sensitive Levenshtein match within two.
