# Story sessions

> **Availability:** bare `story` and `story chat` open the interactive TTY
> lobby. Attach a story session before managing its subject conversations.
> These commands do not send chat turns or expose transcripts or search.

Key terms: **story session**—the durable provider-work scope; **conversation
lobby**—an attached session with no subject selected; **subject**—the preserved
display title; **handle**—the short unique or full conversation ID shown by the
lobby.

<a id="the-lobby-at-a-glance"></a>
<a id="scenario-first-session-return-switch-and-end"></a>

## Scenario: organize one drafting session

This single walkthrough creates two subjects, returns to the first, renames and
archives it, then verifies the saved successor after restart.

Create a workspace and attach a new story session named `draft`:

```console
$ story init ./river-story
$ cd ./river-story
$ story chat draft
```

The attached prompt is `story[draft:lead]> `. Before every prompt, a context
line says either `Conversation: none` or shows the selected handle and subject.
Create two conversations; each new record becomes selected:

```text
/conversation new Opening scene
/conversation new Finale
/conversation list
```

The list stays in catalog order and marks `Finale` as selected. It prints each
handle as the first 12 ID characters when that prefix is unique across active
and archived records; otherwise it prints the full ID. Copy the handle shown
for `Opening scene` and replace `OPENING_HANDLE` below:

```text
/conversation use OPENING_HANDLE
/conversation subject Chapter 1 opening
/conversation list chapter 1
```

`use` selects the existing identity. `subject` changes only its display title.
The list query is normalized for case and Unicode whitespace, then matched as a
subject substring; accepted display text remains unchanged.

Archive the selected opening:

```text
/conversation archive
```

The confirmation names the current subject and handle and defaults to no:
press Enter to keep it, or type `yes` to continue. In this scenario, type
`yes`. The opening becomes archived and the first other active record,
`Finale`, becomes selected. Confirm both facts:

```text
/conversation list --all opening
/conversation list
```

`--all` must appear once and first. The first command includes the archived
opening; the second shows active conversations only.

End the story session, confirm with `yes`, and leave the outer lobby:

```text
/session end
/quit
```

Ending keeps both saved selections. On the next bare entry, the story session
is inactive, so the lobby asks `Resume selected session draft? [Y/n] `. Press
Enter to resume; the context restores `Finale`. `/quit` then detaches without
ending it.

<a id="current-boundary"></a>

For exact grammar, see the [command parser](../reference/interactive-command-parser.md).
For ordering and failure compensation, see the single
[lifecycle diagram](../design/session-lifecycle.md#conversation-lifecycle-and-compensation).
Installed entry and exit behavior is in the [CLI reference](../reference/cli.md),
and story-session switching uses the documented
[attach order](../design/session-lifecycle.md#attach-and-switch-ordering).
Lease details are in [Backend and sessions](../reference/backend-and-sessions.md).
