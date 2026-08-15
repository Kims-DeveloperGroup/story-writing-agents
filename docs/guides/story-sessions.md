# Story sessions and routed conversations

> **Availability:** bare `story` and `story chat` open the interactive TTY
> shell. After attaching a story session, you can search active conversations
> and read active or archived transcripts without selecting a conversation.
> Sending a turn still requires one selected active conversation.

Key terms: **story session**—the durable provider-work scope;
**conversation**—one subject and visible transcript; **handle**—a copyable full
ID or unique prefix; **sticky target**—the responder used by ordinary lines;
**one-shot route**—an `@AGENT` turn that does not change that target.

<a id="the-lobby-at-a-glance"></a>
<a id="scenario-first-session-return-switch-and-end"></a>

## Scenario: find and continue an earlier discussion

Assume `river-story` already has a `draft` session with conversation logs, but
no conversation is selected. Attach the session:

```console
$ cd river-story
$ story chat draft
```

The scope line reports that `draft` is attached and its conversation is
`none`. Search the active subjects and visible user/reply text:

```text
/search council vessel
```

Search needs the attachment, not a selection. It makes no provider call and
returns at most the default number of deterministic cited hits. Copy a handle
from a hit, then request the first two stored events:

```text
/transcript c_0123456789 2
```

If the continuation hint names sequence 3, follow it explicitly:

```text
/transcript c_0123456789 2 --from 3
```

That second command takes a fresh snapshot; the shell did not retain a cursor.
Search is active-only. To read an archived discussion, find its handle in the
all-record catalog and name it explicitly:

```text
/conversation list --all
/transcript ARCHIVED_HANDLE
```

These reads do not select the conversation, change the sticky target, call the
provider, or rewrite any story data. Their scope always follows the attached
session; creating, selecting, renaming, or archiving conversations does not
create a separate read scope.

## Routed turns

Create or select an active conversation before sending content:

```text
/conversation new Opening scene
/to plot
Should the reveal happen before the midpoint?
@character Check whether Mara's choice fits her motives.
/say /run is literal conversation text
```

`/to plot` changes the process-local sticky target to `plot_architect` without
calling a provider. The ordinary line and `/say` use that target;
`@character` is one-shot. All responders share the selected conversation—it is
not a private channel.

Each accepted line waits for one runtime turn. Bounded complete-turn context
from only that conversation goes to the addressed agent. Normally the user
event is stored before provider work, then one reply or safe runtime-error
outcome is stored. Returned content is displayed once and never reparsed.

Conversation create/use/rename/archive preserves the sticky target. Attaching,
switching, or ending a story session resets it to `lead`. Quit detaches without
ending; end makes the session inactive while retaining saved session and
conversation selections.

## Command gates and limits

- `/agents`, help, and session listing work without attachment.
- `/to`, `/search`, `/transcript`, and conversation create/list/use require an
  attachment but no selected conversation. Rename and unqualified archive need
  a selection.
- Ordinary lines, `@AGENT TEXT`, and `/say TEXT` require a selected active
  conversation and may contact the provider.
- `/compose`, active-turn cancellation, `/history`, debate, and artifact
  actions are not dispatched in this shell.

<a id="current-boundary"></a>

Exact syntax lives in the [command parser](../reference/interactive-command-parser.md),
search and page rules in [Conversation search](../reference/conversation-search.md),
and exact display copy in the [terminal reference](../reference/interactive-terminal.md).
For persistence, see [Conversation transcripts](../reference/conversation-transcripts.md);
for exposure, read [Privacy and provider](../operations/privacy-and-provider.md).
