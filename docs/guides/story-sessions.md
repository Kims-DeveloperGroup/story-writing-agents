# Story sessions and conversations

> **Availability:** bare `story` and `story chat` open the TTY shell. An attached
> session supports subject management and local reads; advisory turns and
> consensus additionally require one selected active conversation.

Key terms: **story session**—durable provider-work scope; **conversation**—one
subject and visible transcript; **selection**—the active conversation used by
writes and runs; **sticky target**—the responder for ordinary advisory lines.

## Enter and attach

Run `story` from an initialized workspace. With no saved session it opens
`story[lobby]>`; with an active saved session it attaches automatically.

```text
/session list
/session new draft
```

`/session resume [NAME]` attaches an active session or resumes an inactive one.
`story chat draft` is the explicit startup equivalent. Attachment resets the
sticky target to `lead`. `/quit`, `/exit`, or EOF releases the lease without
deactivating the session. `/session end` asks for confirmation, deactivates it,
and returns to the lobby while retaining saved selections.

## Manage subjects and routing

| Command | Effect |
| --- | --- |
| `/conversation new [SUBJECT]` | Create and select an active subject. |
| `/conversation list [--all] [QUERY]` | List in catalog order; optionally include archives or filter subjects. |
| `/conversation use ID` | Select an active exact ID or displayed unique handle. |
| `/conversation subject SUBJECT` | Rename the selected identity. |
| `/conversation archive [ID]` | Confirm selected archive, or archive another identity directly. |

Copy handles shown by creation, list, search, or transcript output; a subject can
change but its generated identity cannot. All responders share the selected
transcript—direct routing is not a private channel.

At the main prompt, an ordinary line or `/say TEXT` uses the sticky target.
`@plot TEXT` is one-shot; `/to character` changes later ordinary turns. Advisory
input is exactly one line. Conversation changes preserve the target; session
attach, switch, or end resets it to `lead`.

## Choose the right memory operation

| Operation | What it does |
| --- | --- |
| `/history [COUNT]` | Local selected-transcript snapshot plus context accounting; no provider. |
| `/search QUERY` | Local bounded literal search over active subjects and visible events; no selection needed. |
| `/transcript ID [COUNT] [--from N]` | Local fresh bounded page from active or archived identity; does not select it. |
| One advisory question | The agent may directly answer or request bounded active same-session search/read actions. |

Agent retrieval is conditional, not another spelling of `/search`. When chosen,
the shell shows safe `[context]` progress, the reply, and ordered sources; only
the user and final outcome enter the transcript. Local reads never call Codex,
change selection, or enter transcript data.

## Turn a selected discussion into consensus

```text
/run
Draft the opening using the decisions above.
Keep the viewpoint close and the final discovery concrete.
/send
```

Bare `/run` opens the multiline composer. At the main prompt, standalone
`/compose`, `/send`, and `/cancel` are unavailable. Inside this composer only,
`/send` starts the debate and `/cancel` or Ctrl-C discards the local buffer.

The runtime reads one bounded selected-conversation snapshot and supplies that
same snapshot to every debate phase. Alternatives are:

```text
/run --fresh Draft an unrelated opening in a train station.
/draft-file ./working/opening.md
```

Fresh mode records selected identity but reads/sends no transcript. Draft-file
mode reads strict UTF-8 from the shell process's current directory and includes
the selected snapshot. Runs may write debates and artifacts but never append or
change conversation state.

## Inspect outcomes and recover

`/status` is global. Attached `/inspect [ID|last]` exposes safe debate metadata;
`last` is this process's latest accepted started run for that session, not the
newest file. Expected consensus, deadlock, and handled failure return to the same
prompt. A deadlock or interrupted debate needs a new ID.

Main-prompt Ctrl-C emits `[cancelled] No action was taken.` and changes no
state. Advisory/consensus cancellation recovers only at documented controller
boundaries; a cancelled local read keeps completed rows. See
[Inspection and recovery](inspection-and-recovery.md), the
[CLI contract](../reference/cli.md), [local search](../reference/conversation-search.md),
[agent retrieval](../reference/conversation-retrieval.md), and
[recovery limits](../operations/recovery-and-limitations.md).
