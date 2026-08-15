# Story sessions and routed conversations

> **Availability:** bare `story` and `story chat` open the interactive TTY
> shell. Attach a story session, then select an active subject conversation to
> send single-line advisory turns.

Key terms: **story session**—the durable provider-work scope; **conversation**—
one subject and visible transcript; **sticky target**—the responder used by
ordinary lines; **one-shot route**—an `@AGENT` turn that does not change the
sticky target; **handle**—the displayed conversation ID or unique prefix.

<a id="the-lobby-at-a-glance"></a>
<a id="scenario-first-session-return-switch-and-end"></a>

## Scenario: route one drafting conversation

Create a workspace and attach a new story session named `draft`:

```console
$ story init ./river-story
$ cd ./river-story
$ story chat draft
```

The prompt starts as `story[draft:lead]> `. List the four roles and create the
first subject conversation:

```text
/agents
/conversation new Opening scene
```

`/agents` is global and provider-free. Its roster shows each display name,
canonical ID, aliases, and advisory domain. A new conversation becomes selected.
Set the plot architect as the sticky target, then send three turns:

```text
/to plot
Should the reveal happen before the midpoint?
@character Check whether Mara's choice fits her established motives.
/say /run is literal conversation text
```

`/to` changes the prompt to `story[draft:plot]> ` without calling a provider.
The first and third messages go to `plot_architect`; the `@character` route is
one-shot. Direct routing selects a responder in the same shared room—it does
not create a private conversation.

Each accepted line waits for one runtime turn. The selected conversation's
bounded complete-turn pre-turn context goes with that request. The shell safely
displays the reply as content, never as another command. It shows a notice only when
the returned pre-turn window was truncated.

Create a second conversation, then return to the first:

```text
/conversation new Finale
Does this ending pay off the reveal?
/conversation list
/conversation use OPENING_HANDLE
Continue from the earlier midpoint discussion.
```

Copy `OPENING_HANDLE` from the list. Conversation changes preserve the sticky
plot target, while each conversation keeps separate visible context. Rename and
archive the selected opening; answer `yes` to the default-no confirmation:

```text
/conversation subject Chapter 1 opening
/conversation archive
yes
```

The first other active record, `Finale`, becomes selected. Finally, switch away
and back, then end the session:

```text
/session new notes
/session resume draft
/session end
yes
/quit
```

Every session attach or switch resets the target to `lead`; conversation changes
do not. Ending resets process routing, keeps saved session/conversation
selections, and returns to the unattached lobby. Quit detaches without ending.

## Command gates and current limits

- `/agents` works with or without an attachment.
- `/to` and `/agent` need an attached session, but no selected conversation.
- Ordinary lines, `@AGENT TEXT`, and `/say TEXT` also need a selected active
  conversation.
- `/compose`, active-turn cancellation, and history, search, transcript,
  consensus, and artifact commands are not dispatched in this shell.

<a id="current-boundary"></a>

Exact syntax lives in the [command parser](../reference/interactive-command-parser.md).
Turn ordering and persistence live in the [Runtime API](../reference/runtime-api.md#one-conversation-turn)
and [conversation transcript](../reference/conversation-transcripts.md)
references. For session and archive compensation, see
[Session lifecycle](../design/session-lifecycle.md); for exposure, read
[Privacy and provider](../operations/privacy-and-provider.md).
