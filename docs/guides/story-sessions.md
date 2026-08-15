# Story sessions and conversations

> **Availability:** bare `story` and `story chat` open the TTY shell. After
> attaching a story session, a selected active conversation supports advisory
> turns and consensus runs. Search and transcript reads need no selection.

Key terms: **story session**—durable provider-work scope; **conversation**—one
subject and visible transcript; **selection**—the active conversation used by
writes and runs; **sticky target**—the responder for ordinary advisory lines;
**included run**—a debate given one fixed transcript snapshot.

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

The advisory line appends a user event and one reply to the selected
conversation. Now compose a consensus prompt:

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

## Find or route conversation work

`/search QUERY` scans active subjects and visible user/reply text locally.
`/transcript ID [COUNT] [--from SEQUENCE]` reads a fresh bounded page from an
active or archived conversation. Neither command selects a conversation or
calls the provider.

Ordinary lines and `/say TEXT` use the sticky target; `@AGENT TEXT` is one-shot.
Changing conversations preserves the target. Attaching, switching, or ending a
story session resets it to `lead`. All responders share the selected transcript;
direct routing is not a private channel.

Exact shell grammar lives in the [CLI reference](../reference/cli.md), search
and paging in [Conversation search](../reference/conversation-search.md), and
provider exposure in [Privacy and provider](../operations/privacy-and-provider.md).
