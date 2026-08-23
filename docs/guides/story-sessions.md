# Story sessions and conversations

> **Availability:** the TTY shell supports session management, persistent
> settings, and local reads; advisory turns and consensus also require current
> focus on an active conversation. Inline canonical artifact references work in
> provider-bound routed, sticky, ordinary, retrieval, and `/run` prompt text.
> After session attachment and conversation focus, `$`, `$${path}`, and `$$`
> discover or select them without submitting.

Key terms: **defaults** are persisted fallbacks; **current attachment** is the
session held by this shell; **current focus** and **agent focus** select the
conversation and ordinary advisory responder.

The status fields describe separate user-visible facts:

| Fact | What it means for you |
| --- | --- |
| Durable `ready` / `paused` | `ready` permits provider work only when the other gates pass; `paused` forbids it. Neither says whether this shell owns access. |
| Process access | `claimable` means no blocking claim and guarded acquisition may be attempted; pair absent with create and ready/paused with resume. `held_here` means this backend owns the exact claim, not that every command is ready. |
| Default vs current | Defaults are saved choices for a later command or process. Current attachment and conversation focus are what this process can use now. |
| Agent focus | The responder for ordinary advisory text. A conversation change preserves it; a session switch resets it to `lead`. |
| Provider readiness | Derived from valid configuration plus durable `ready` and access `held_here`; it is a read-only answer, not another stored state. Commands still require their documented attachment and conversation focus. |

## Create, resume, switch, and pause

Run `story` from an initialized workspace. Without a default it opens
`story[lobby]>`. Session commands are:

```text
/session list
/session create draft
/session resume [NAME]
/session pause
```

| Starting facts | Command behavior | User-visible result |
| --- | --- | --- |
| Name is absent | `/session create NAME` creates ready state and attaches it. | Orientation says `created`; it becomes the default. |
| Ready and claimable | `/session resume NAME` acquires it without durable rewrite. | Orientation result remains `opened`. |
| Paused and claimable | `/session resume NAME` makes it ready and acquires it. | Orientation result remains `reopened`. |
| Another session is attached | Create/resume acquires the destination before releasing the source. | Destination becomes default/current; agent focus resets to `lead`. |
| Session is attached | Confirmed `/session pause` pauses and releases it. | Defaults remain; attachment and both focuses clear. |

At a capable main prompt, typing the exact omitted `/session resume` shows all
persisted sessions *before* submission, ordered by newest durable update then
ID. Each row shows session ID, durable state, and process access. The current
attachment is highlighted first, otherwise the saved default, otherwise the
first row. Up/Down moves the highlight, Tab does nothing, and Enter submits the
highlighted row as one complete explicit command. Escape only dismisses these
live rows and leaves `/session resume` in the buffer; it is not a domain
cancellation.

If `/session resume` itself is submitted—after Escape then Enter, through
nonempty configured VEOF, or without committing a live row—the controller
freshly reads the persisted lifecycles. A successful nonempty read opens the
history-free generic picker. It is live and transient on a capable terminal;
ordinary-line fallback shows the complete numbered form. In fallback, press
Enter for the highlight, enter an in-range ASCII decimal number, or enter exact
`/cancel`. Main history contains the submitted omitted command even when no
picker opens; when one does, its answer is history-free. Cancelling or
interrupting that submitted picker writes
`[cancelled] Session resume cancelled.`; Ctrl-C at the main prompt instead writes
`[cancelled] No action was taken.` Picker EOF exits through normal cleanup
without selecting a destination.

An empty lifecycle list prints `[session] No story sessions found.` without
opening the picker; a lifecycle-read failure reports the applicable error
without opening it. Choosing the current attachment only reports that it is
already attached.
Snapshot display,
highlight movement, Escape dismissal, picker cancel, and rejection before
destination acquisition are mutation-free. A submitted different choice is
freshly validated, then the controller acquires it or durably reopens it,
post-validates, loads its conversation focus, persists the destination default,
releases the source when switching, and finally commits in-memory attachment and
focus. Explicit `/session resume NAME` is direct and never creates a missing
session.

That sequence is not one transaction. If a paused destination is successfully
reopened and a later step fails, its durable state remains `ready`. Later
failure releases newly acquired destination access where possible and does not
advance in-memory attachment/focus, but durable lifecycle and default rollback
are not promised. A reported default-write failure can occur after atomic
replacement; inspect `/status` and persisted defaults rather than assuming the
old default. If source release fails after destination-default persistence, the
shell exits through `cleanup_failed` and the new default remains.

`story chat NAME` creates an absent session or resumes a persisted session. Bare
`story` resumes a ready default and asks for confirmation before resuming a
paused default. Resume choices do not change `/session list` or standalone
session-command behavior.

`/quit`, `/exit`, and EOF release access without pausing; ready state and
defaults remain. Restarting establishes a new attachment and restores an active
default conversation. `/status` separates defaults from process scope.

On capable POSIX main prompts, `/` opens a selectable command palette. Exact,
case-sensitive prefixes narrow canonical root, subcommand, and finite-option
candidates. Agent-target positions show only the four preferred prompt aliases
`lead`, `plot`, `character`, and `execution`; all ten declared route aliases
remain accepted by the parser. Up/Down moves the highlight, and Tab completes
the active token. Escape restores the first pre-completion snapshot when present
and dismisses
that exact state; the palette does not enforce runtime scope. Static Enter
completion adds one ASCII trailing space at the end of a token and does not
submit, so a later Enter is required. Dynamic resume Enter commits immediately.
Plain fallback has no pre-Enter palette. All capable submitted input clears its
complete live frame before persistent results are written; fallback prompts and
echoed input remain visible. See the
[terminal contract](../reference/interactive-terminal.md#main-prompt-live-suggestions).

## Edit persistent settings

`/settings` is global, alias-free, provider-free, and works in the lobby or
while attached. It shows separate Current and Pending values for Language,
Persona, Maximum debate rounds, and Providers. The top-level choices are those
four rows followed by Save and Cancel. Choose Providers to select an agent in
canonical roster order, then choose Provider, Model, or Reasoning effort for
that agent. Provider choices are Codex and Antigravity. Changing the route does
not erase the inactive provider's saved model or effort.

On a capable terminal, Up/Down moves the current picker highlight and Enter
opens or commits it. Escape from a nested picker or prompt restores that
field's prior pending value and returns to its parent. Escape at the top level,
Cancel, Ctrl-C, or EOF cancels the whole workflow without saving. Plain
fallback uses the picker's numbered grammar. In the per-agent active-provider
model text prompt, blank means inherit that provider's workspace system model
and `/cancel` is literal model text rather than a command.

Language offers `KO`, `EN`, `JA`, `ZH`, `ES`, `FR`, `DE`, `PT`, `IT`, and `RU`.
Persona uses the safe lexical template catalog. Debate rounds accept a
nonnegative ASCII integer, including audit-only `0`. Providers are edited per
agent in roster order: Codex is the default, and Antigravity is also available.
Model accepts exact safe text or blank to inherit the active provider's
workspace system model. Codex effort offers Inherit, `minimal`, `low`,
`medium`, `high`, and `xhigh`; Antigravity effort offers Inherit, `low`,
`medium`, and `high`. Inherit uses the active provider's workspace system
value, then its provider default. The legacy backend name `gemini` is rejected;
use `antigravity`.

Save fails closed if either configuration file or the persona catalog changed
after opening. A successful save affects only the next shell/backend
construction; the active shell keeps its construction-time configuration
snapshot, and live session or explicit overrides still take precedence. See
[Agent configuration](../reference/configuration/agent-configuration.md) and
[Filesystem safety](../operations/filesystem-safety.md). Antigravity setup and
provider-specific precedence are in the
[Antigravity guide](antigravity.md).

## Manage conversations and routing

| Command | Effect |
| --- | --- |
| `/conversation new [SUBJECT]` | Create an active subject; save it as default and current focus. |
| `/conversation list [--all] [QUERY]` | List in catalog order; optionally include archives or filter subjects. |
| `/conversation resume [ID]` | Focus an active exact ID or accepted unique prefix, or omit `ID` to choose from active conversations; save the default. |
| `/conversation subject SUBJECT` | Rename the focused identity. |
| `/conversation archive [ID]` | Confirm focused archive, or archive another identity directly. |

On a capable main prompt, exact omitted `/conversation resume` shows active
conversations before submission in catalog creation-time/ID order. Each row
uses the subject as its label and shows `Handle: HANDLE`. A handle is the stable
first 12 characters of the immutable ID when that prefix is unique across the
complete session catalog; a collision shows the full ID. Current active focus
is highlighted first, then an active saved default, then the first row.
Up/Down previews, Tab does nothing, Enter commits the full immutable ID through
one explicit resume command, and Escape only dismisses the live rows.

Submitting the omitted command instead freshly reads the complete catalog and
eligible active rows. A successful nonempty read opens the generic picker. It
is live and transient on a capable terminal; ordinary-line fallback uses the
numbered form and accepts blank for the highlight, an in-range ASCII decimal
ordinal, or exact `/cancel`. This includes Enter after Escape and nonempty
configured VEOF. Main history records the omitted submission whether the
controller opens a picker, reports an empty result, or reports an error; a
picker answer is history-free. A submitted picker cancel or interrupt writes
`[cancelled] Conversation focus cancelled.` and changes no catalog, default, or
focus. EOF
exits through normal cleanup without selecting a conversation. If there are no
active conversations, the shell prints
`[conversation] No active conversations found.` without opening the picker. A
catalog or saved-default read failure reports the applicable error without
opening it.

A picker commit carries the immutable full ID, then rereads the complete
catalog before selection. A conversation that disappeared or became archived
is rejected, and a refreshed subject is used. The saved default is written
before current focus changes. A reported write failure therefore leaves
in-memory current focus unchanged, but it may occur after atomic replacement of
the persisted default; inspect the persisted selection rather than assuming the
old default. Conversation resume never mutates catalog metadata, and agent focus
is preserved. Explicit `/conversation resume ID` remains direct and accepts the
same exact full ID or accepted case-sensitive unique prefix of at least 12
characters as before.

Identities cannot change. Responders share the focused transcript. Ordinary
text uses agent focus; `@plot TEXT` is one-shot, while `/to character` persists.
Conversation changes preserve agent focus; session transitions reset or clear
it. `/conversation list` remains active-only unless `--all` is supplied. The
picker does not change startup default restoration, routing, noninteractive
commands, storage/provider schemas, or package APIs.

References follow the same routing: `@plot Review ${core_documents/global_outline.md}`
addresses one agent once, while ordinary text after `/to character` addresses
the sticky character focus. Every provider-bound turn captures its own ordered
batch; a previous agent's read cannot satisfy the next turn. See
[Canonical artifact references](artifact-references.md).

Artifact controls require all three of: an attached session, a focused active
conversation, and a cursor in provider-bound prompt text. Shell orientation and
full `/help` summarize this prerequisite. Lobby input, attached input without
focus, and management commands, IDs, options, subjects, output paths, and
settings remain inert.

While editing an eligible span, type `$` to open
`Writing artifact selection [open]`. It shows
`[controls] Up/Down selects; Enter/Tab inserts a whole-file reference.`, and
lists current safe canonical filesystem sources in lexical order. Type a case-sensitive path fragment
after `$` to filter the list; no match shows
`[empty] No matching writing artifacts. Change the path fragment.` Up/Down and
Enter or Tab insert `${path}` on a capable terminal without sending the prompt.
If no source exists, the panel says `No writing artifacts. Add a supported
canonical core document or part-scoped Markdown manuscript. Consensus can also
publish one.` Enter or Tab emits the established `empty_sources` error and
restores the draft.

Type `$${path}` then Enter or Tab to open that artifact directly, or `$$` then
Enter or Tab to choose it first; Shift+Up/Down selects an inclusive range and
Enter inserts `${path}#Lx-Ly`. Escape, Ctrl-C, EOF, or a handled read error
restores the exact draft and cursor with no dispatch or history entry. Type
`\$` for a literal dollar.

Ordinary-line fallback uses numbered source choices and exact `START-END`
viewer input, then displays the amended draft for a second Enter confirmation.
It never submits a selection implicitly. Multiple completed indicators remain
in order; every later agent or debate phase still performs its own enforced
read after normal prompt submission.

## Choose the right memory operation

| Operation | What it does |
| --- | --- |
| `/history [COUNT]` | Local snapshot of current focus plus context accounting; no provider. |
| `/search QUERY` | Bounded literal search over active subjects/events; no focus required. |
| `/transcript ID [COUNT] [--from N]` | Fresh bounded page from active or archived identity; focus unchanged. |
| One advisory question | Agent may answer or request bounded active same-session search/read actions. |

Agent retrieval is conditional. The shell shows `[context]` progress, reply,
and sources. Only user and outcome enter the transcript; local reads never call
a provider or alter defaults/focus.

## Turn the current focus into consensus

```text
/run --output part-01/opening.md
Draft the opening using the decisions above.
Keep the viewpoint close and the final discovery concrete.
/send
```

`/run --output PART/PATH.md` without prompt text opens the multiline composer.
There, `/send` starts the debate and
`/cancel` or Ctrl-C discards the buffer; those commands are unavailable outside it.
On capable POSIX live editing, typing `/` previews only `/send` then `/cancel`;
case-sensitive whole-buffer prefixes narrow that list. The panel does not
complete or submit slash controls. Artifact `$` activation is available in the
composer text and likewise returns without sending. `/say /send` and `/say /cancel` still insert literal
control lines, and ordinary fallback input shows no panel.

The runtime supplies every debate phase one bounded focus snapshot.
`/run --fresh --output PART/PATH.md PROMPT` records its identity without
transcript; `/draft-file PATH` reads strict UTF-8, includes the snapshot, and
prompts for the required output path. Runs do not change conversations.

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
