# Story sessions and conversations

> **Availability:** bare `story` and `story chat` open the TTY shell. After
> attachment, a selected active conversation supports retrieval-enabled turns
> and consensus runs. Global status/help, attached management, and local
> conversation reads are also available.

Key terms: **story session**—durable provider-work scope; **conversation**—one
subject and visible transcript; **selection**—the active conversation used by
writes and runs; **sticky target**—the responder for ordinary advisory lines;
**source**—the stable conversation or event cited beneath an answer.

## Scenario: discuss, then run

Attach the `draft` session and create a subject conversation:

```console
$ cd river-story
$ story chat draft
```

```text
/conversation new Opening scene
/to plot
Should the reveal happen before the midpoint?
```

The responder may answer directly or retrieve active same-session conversations
and cited complete turns. The shell shows safe `[context]` progress, reply, then
ordered sources; only the user and final outcome enter the transcript.
Now compose a consensus prompt:

```text
/run
Draft the opening using the decision we just discussed.
/send
```

Bare `/run` opens the existing multiline run composer. Only `/send` starts the
debate; `/cancel` or an input interrupt discards the local composition. The
runtime reads the selected conversation once and gives that same bounded
snapshot to every debate phase.

Compare a fresh prompt:

```text
/run --fresh Draft an unrelated opening in a train station.
```

This records which conversation was selected but reads and sends none of its
events. To audit a complete local draft with selected context:

```text
/draft-file ./working/opening.md
```

The file is read exactly as strict UTF-8 from the shell process's current
directory. Path and error details are in the [CLI reference](../reference/cli.md#interactive-consensus).

## Outcomes and unchanged conversation state

The shell shows typed progress, then one of three expected outcomes:

- consensus reports revision, round count, manuscript path, and core updates;
- deadlock reports a fixed safe error and requires a new run; or
- handled failure reports a fixed category such as provider, transcript,
  workspace, or artifact failure.

Each expected outcome returns to the same prompt. Consensus commands can write
debate records and approved artifacts, but they do not append the conversation
transcript or change its selection, sticky target, session attachment, or
conversation-memory binding. Fresh mode still requires a selected active
conversation so its identity can be recorded.

During `[waiting]`, Ctrl-C joins advisory cancellation, preserves shell scope,
best-effort stores the fixed runtime error after user append, and may force the
next provider thread fresh. Consensus Ctrl-C either cancels before start without a debate or
reports a durable, nonresumable interruption; retry with `/run`. Cancelling a
local search/transcript/history read leaves only completed rows. See the
[exact records](../reference/interactive-terminal.md#exact-cancellation-records).

## Find or route conversation work

`/search QUERY` scans active subjects and visible user/reply text locally.
`/transcript ID [COUNT] [--from SEQUENCE]` reads a fresh bounded page from an
active or archived conversation. Neither command selects a conversation or
calls the provider; they are explicit inspection, not agent retrieval.

Ordinary lines and `/say TEXT` use the sticky target; `@AGENT TEXT` is one-shot.
Changing conversations preserves the target. Attaching, switching, or ending a
story session resets it to `lead`. All responders share the selected transcript;
direct routing is not a private channel.

## Manage and audit without provider work

| Command | Scope |
| --- | --- |
| `/help [COMMAND]` or `/?` | Global, registry-derived help. |
| `/status` | Global shell/session/conversation/context/last-run snapshot. |
| `/language`, `/persona`, `/reset` | Attached; update only runtime overrides. |
| `/inspect [ID\|last]` | Attached; safe debate metadata and context counts. |
| `/history [COUNT]` | Attached active selection; one transcript/context snapshot. |

History includes every visible event kind, unmatched users, and whitespace;
count selects the newest events but displays them chronologically. Inspection
never prints raw messages, candidates, errors, actions, context content, or
provider fields. `last` means the latest accepted `started` run retained for
this process and session, not the newest file. Commands preserve shell/story
state except that successful directives atomically change overrides. See the
[CLI management contract](../reference/cli.md#interactive-streams) and
[Inspection and recovery](inspection-and-recovery.md).

Exact shell grammar lives in the [CLI reference](../reference/cli.md), local
search in [Conversation search](../reference/conversation-search.md), agent
actions in [Bounded retrieval](../reference/conversation-retrieval.md), and
provider exposure in [Privacy](../operations/privacy-and-provider.md).
