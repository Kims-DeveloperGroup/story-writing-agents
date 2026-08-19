# Story sessions and conversations

> **Availability:** the TTY shell supports session management, persistent
> settings, and local reads; advisory turns and consensus also require current
> focus on an active conversation.

Key terms: **defaults** are persisted fallbacks; **current attachment** is the
session held by this shell; **current focus** and **agent focus** select the
conversation and ordinary advisory responder.

## Create, open, switch, and pause

Run `story` from an initialized workspace. Without a default it opens
`story[lobby]>`. Session commands are:

```text
/session list
/session create draft
/session open [NAME]
/session pause
```

| Starting facts | Command behavior | User-visible result |
| --- | --- | --- |
| Name is absent | `/session create NAME` creates ready state and attaches it. | Orientation says `created`; it becomes the default. |
| Ready and claimable | `/session open NAME` acquires it without durable rewrite. | Orientation says `opened`. |
| Paused and claimable | `/session open NAME` makes it ready and acquires it. | Orientation says `reopened`. |
| Another session is attached | Create/open acquires the destination before releasing the source. | Destination becomes default/current; agent focus resets to `lead`. |
| Session is attached | Confirmed `/session pause` pauses and releases it. | Defaults remain; attachment and both focuses clear. |

Omitting `/session open`'s name uses the default but never creates it.
`story chat NAME` creates an absent name or opens/reopens it. Bare `story` opens
a ready default and asks before reopening a paused default.

`/quit`, `/exit`, and EOF release access without pausing; ready state and
defaults remain. Restarting establishes a new attachment and restores an active
default conversation. `/status` separates defaults from process scope.

On capable POSIX main prompts, `/` opens a selectable command palette. Exact,
case-sensitive prefixes narrow canonical root, subcommand, and finite-option
candidates. Up/Down moves the highlight, Tab completes the active token, and
Escape restores the first pre-completion snapshot when present and dismisses
that exact state; the palette does not enforce runtime scope. Plain fallback
has no palette. See the
[terminal contract](../reference/interactive-terminal.md#main-prompt-live-suggestions).

## Edit persistent settings

`/settings` is global, alias-free, provider-free, and works in the lobby or
while attached. It shows separate Current and Pending values for Language,
Persona, Maximum debate rounds, and Providers. The top-level choices are those
four rows followed by Save and Cancel. Choose Providers to select an agent in
canonical roster order, then choose Provider, Model, or Reasoning effort for
that agent.

On a capable terminal, Up/Down moves the current picker highlight and Enter
opens or commits it. Escape from a nested picker or prompt restores that
field's prior pending value and returns to its parent. Escape at the top level,
Cancel, Ctrl-C, or EOF cancels the whole workflow without saving. Plain
fallback uses the picker's numbered grammar. In the per-agent Codex model text
prompt, blank means inherit the workspace system model and `/cancel` is literal
model text rather than a command.

Language offers `KO`, `EN`, `JA`, `ZH`, `ES`, `FR`, `DE`, `PT`, `IT`, and `RU`.
Persona uses the safe lexical template catalog. Debate rounds accept a
nonnegative ASCII integer, including audit-only `0`. Providers are edited per
agent in roster order: Codex is enabled; reserved Gemini is disabled for new
selection, while an existing Gemini route may be preserved or remediated to
Codex. Model accepts exact safe text or blank to inherit the workspace system
model. Reasoning offers Inherit, `minimal`, `low`, `medium`, `high`, and
`xhigh`; Inherit uses the workspace system reasoning value, then the provider
default.

Save fails closed if either configuration file or the persona catalog changed
after opening. A successful save affects only the next shell/backend
construction; the active shell keeps its construction-time configuration
snapshot, and live session or explicit overrides still take precedence. See
[Agent configuration](../reference/configuration/agent-configuration.md) and
[Filesystem safety](../operations/filesystem-safety.md).

## Manage conversations and routing

| Command | Effect |
| --- | --- |
| `/conversation new [SUBJECT]` | Create an active subject; save it as default and current focus. |
| `/conversation list [--all] [QUERY]` | List in catalog order; optionally include archives or filter subjects. |
| `/conversation use ID` | Focus an active exact ID or displayed unique handle; save the default. |
| `/conversation subject SUBJECT` | Rename the focused identity. |
| `/conversation archive [ID]` | Confirm focused archive, or archive another identity directly. |

Identities cannot change. Responders share the focused transcript. Ordinary
text uses agent focus; `@plot TEXT` is one-shot, while `/to character` persists.
Conversation changes preserve agent focus; session transitions reset or clear it.

## Choose the right memory operation

| Operation | What it does |
| --- | --- |
| `/history [COUNT]` | Local snapshot of current focus plus context accounting; no provider. |
| `/search QUERY` | Bounded literal search over active subjects/events; no focus required. |
| `/transcript ID [COUNT] [--from N]` | Fresh bounded page from active or archived identity; focus unchanged. |
| One advisory question | Agent may answer or request bounded active same-session search/read actions. |

Agent retrieval is conditional. The shell shows `[context]` progress, reply,
and sources. Only user and outcome enter the transcript; local reads never call
Codex or alter defaults/focus.

## Turn the current focus into consensus

```text
/run
Draft the opening using the decisions above.
Keep the viewpoint close and the final discovery concrete.
/send
```

Bare `/run` opens the multiline composer. There, `/send` starts the debate and
`/cancel` or Ctrl-C discards the buffer; those commands are unavailable outside it.
On capable POSIX live editing, typing `/` previews only `/send` then `/cancel`;
case-sensitive whole-buffer prefixes narrow that list. The panel does not
complete or submit text. `/say /send` and `/say /cancel` still insert literal
control lines, and ordinary fallback input shows no panel.

The runtime supplies every debate phase one bounded focus snapshot.
`/run --fresh PROMPT` records its identity without transcript; `/draft-file PATH`
reads strict UTF-8 and includes the snapshot. Runs do not change conversations.

## Inspect outcomes and recover

`/status` is global. Attached `/inspect [ID|last]` exposes safe debate metadata;
`last` is this process's latest accepted started run for that session. Expected
consensus, deadlock, and handled failure return to the same prompt. A deadlocked
or interrupted debate requires a new ID.

Main-prompt Ctrl-C emits `[cancelled] No action was taken.` Advisory/consensus
cancellation recovers only at documented controller boundaries; cancelled local
reads keep completed rows. See [Inspection and recovery](inspection-and-recovery.md),
the [CLI contract](../reference/cli.md), and
[recovery limits](../operations/recovery-and-limitations.md).
