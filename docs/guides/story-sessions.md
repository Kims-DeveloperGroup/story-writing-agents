# Story sessions and conversations

> **Availability:** the TTY shell supports session management, persistent
> settings, and local reads; advisory turns and consensus also require current
> focus on an active conversation. Inline canonical artifact references work in
> provider-bound routed, sticky, ordinary, retrieval, and `/run` prompt text.
> After session attachment and conversation focus, `$`, `$${path}`, and `$$`
> discover or select them without submitting. Accepted advisory and consensus
> prompt content remains readable in terminal scrollback, with live capable
> input reprojected after cleanup and ordinary-line fallback left unduplicated.

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
echoed input remain visible. For accepted provider-bound content, capable
streams then render one attributed exact-body message before progress, while
fallback adds no duplicate of its already visible submission. Control-only
commands and editor values are not automatically echoed. See the
[terminal contract](../reference/interactive-terminal.md#main-prompt-live-suggestions).

## Edit persistent settings

Global no-argument `/auth` refreshes native Codex and Antigravity status and
opens the provider picker in either the lobby or an attached session. It may run
native sign-in and durably establish a missing workspace default, but it does
not send story content to a model. Cancellation returns to the shell and keeps
any default already established.

`/settings` is global, alias-free, and works in the lobby or while attached. It
shows Current and Pending values for Language, Persona, Maximum debate rounds,
Conversation storage, Conversation RAG, Message details, and Agents. A clean
top level contains exactly those seven choices in that order. Dirty state
appends `Apply Changes (*Required)` and `Discard Changes`, for nine choices.
Conversation storage
offers Filesystem and SQLite as an independent next-shell workspace choice; it
is not an agent provider route. Conversation RAG opens a nested editor with
Provider Disabled/Local and Automatic Off/On. Disabled also forces Off;
selecting Local leaves Automatic Off; On requires pending Local. Message
details offers Off and On. Off is the default compact presentation; it never
removes stored message content.

Every view entered through `/settings` uses a structured form layout, including
the provider-authentication picker opened from Agent settings. The current view
name stands alone, optional agent/provider scope appears as `Context:`, guidance
has its own region, and choice descriptions are stacked below their labels.
`>` marks the active row, `(selected)` marks the selected value, and unavailable
rows retain `(disabled)` plus a textual `Disabled:` reason. The form omits the
generic `[available]`, `[highlighted]`, `[focused]`, and `[open]` labels and
does not print a `Settings/Agents/...` breadcrumb. Global `/auth`, session and
conversation resume, and other non-Settings pickers keep their established
presentation.

The clean root renders all seven rows in its current frame. As soon as a value
is pending, the dirty root renders all nine rows, including Apply and Discard,
regardless of which row is active. This means the complete live redraw or
fallback prompt is available in the current terminal frame/scrollback; it does
not promise that nine rows fit the physical terminal height without terminal
scrolling. Nested catalogs retain a seven-row window. Their heading stays
absolute and concise—`Choices 6/10`, for example—with no shown-range text. A
form with no active row reports only its total, such as `Choices 3`.

A dirty root can look like this without color:

```text
Settings

Choose a setting to edit, apply changes, or discard changes.

Choices 6/9
  1. Language
     Current: KO
  2. Persona
     Current: default_kim_eun_hee
  3. Maximum debate rounds
     Pending: 2
  4. Conversation storage
     Current: filesystem
  5. Conversation RAG
     Current: Provider Disabled; Automatic Off
> 6. Message details
     Current: Off
  7. Agents
  8. Apply Changes (*Required)
  9. Discard Changes
     Pending changes will be discarded.
  [save required] Choose Apply Changes to persist pending changes
```

Choose Agents to select an agent in canonical roster order. Each agent shows
its effective `(Provider/model/effort)` values in the description directly
below its name. Agent settings then gives Provider, Model, and Effort their own
rows and shows the effective value below each field:

```text
Agents

Choices 1/4
> 1. Lead Orchestrator
     Pending: (Antigravity/agy-pro/high)

Agent settings
Context: Lead Orchestrator

Choices 3/3
  1. Provider
     Current: Antigravity
  2. Model
     Current: agy-pro
> 3. Effort
     Pending: high
```

The displayed profile is labeled `Current` when it matches the active runtime
profile and `Pending` when it would change that profile. Each field is compared
independently. A missing model or effort is shown as `provider default`.
Warning color reinforces `Pending`, but the words and values remain complete in
narrow, plain, fallback, and `NO_COLOR` output. An inactive-provider-only edit
can leave the effective rows `Current`; the top-level Agents pending count still
records the unsaved configuration change. Launch-overridden fields stay
disabled and show their effective read-only values. Merely rendering these
values uses local validated state and does not authenticate, discover catalogs,
save settings, or invoke an agent.

Provider remains the field-level configuration concept; its choices are Codex
and Antigravity. Changing the route does not erase the inactive provider's
saved model or effort. Current/Pending confirmation remains provider-qualified
so both providers' inactive values stay identifiable.

On a capable terminal, Up/Down moves the current picker highlight. Enter opens
a selected settings level or applies a pending value. Language, Persona,
Maximum debate rounds, Conversation storage, Conversation RAG Provider,
Conversation RAG Automatic, Message details, Provider, Model, Custom model, and
Effort stay active after a valid Enter: the picker selection or prompt default
updates immediately, and the editor shows one inline completed success
result for the latest value, replacing the previous result. Guidance states
that Enter applies the pending value and keeps the editor open. A second
consecutive valid Enter with the same normalized value returns one level
silently. Entering a different valid value applies it, keeps the editor open,
and makes that value the new same-value comparison. Invalid input,
authentication failure, Custom model navigation, or leaving and re-entering
the editor resets the comparison. This is semantic commit sequencing, not a
raw-key timing gesture.

Escape returns one level through Custom model > Model > Agent settings > Agents
> Settings; Provider and Effort return directly to Agent settings, while
Conversation RAG Provider/Automatic return to Conversation RAG, and
Conversation RAG, Conversation storage, and Message details return directly to
Settings. At root,
Escape invokes the same action as explicit Discard Changes: clean settings exit
immediately, while dirty settings open the existing YES/NO discard confirmation.
NO or Escape there returns to Settings. Ctrl-C or EOF still cancels the whole
transaction without saving. Plain fallback uses the picker's numbered grammar
and the same level hierarchy. In the per-agent active-provider Custom model
prompt, blank means inherit that provider's workspace system model and
`/cancel` is literal model text rather than a command.

Every retained value editor, including Conversation storage, both Conversation
RAG fields, and Message details, always says
`Entered changes remain pending in
memory and are not saved; choose Apply Changes in Settings to persist them.` This stays
visible when the editor first opens, after a valid value is entered, and while
fixing validation. Entering a value changes only the pending in-memory settings;
the completed result confirms that pending change, not a file save.

Text and numeric inputs similarly separate the header and guidance from
`Current value: VALUE` and the final `New value > ` line. The current value
remains prefilled in the editable buffer, so input semantics are unchanged.

When you return to the top-level Settings picker, it shows `[save required]
Choose Apply Changes to persist pending changes` exactly while the complete
pending settings differ from the values at opening. Only dirty state shows the
exact plain `Apply Changes (*Required)` action followed by `Discard Changes`;
clean state shows neither. Eligible ANSI decorates only `(*Required)`; the
preceding separator space remains unstyled. Reverting every change removes the
actions and warning. Nested Agents, Agent settings, value editors, and
Apply/Discard confirmations do not show the warning. Narrow, `NO_COLOR`, and
fallback terminals keep the full plain guidance; fallback may leave an older
prompt in scrollback, but the newest top-level prompt reports the current state.

This retained-editor and same-value exit behavior is specific to the `/settings`
controller, not a global picker or prompt rule. Individual picker and prompt
reads, Apply/Discard confirmations, and context-changing pickers stay one-shot.
In particular, `/session resume` and `/conversation resume` close after
selection; capable mode clears the selected picker, and fallback may leave only
inactive prompt text in scrollback. All input components retain the universal
transient-after-submit policy.

Language offers `KO`, `EN`, `JA`, `ZH`, `ES`, `FR`, `DE`, `PT`, `IT`, and `RU`.
Persona uses the safe lexical template catalog. Debate rounds accept a
nonnegative ASCII integer, including audit-only `0`. Provider routes are edited
per agent in roster order: Codex and Antigravity are available, with the workspace
default and final Codex fallback used by nullable agent routes.

Only entering Model or Effort discovers a catalog; opening Settings or Agents
and navigating Provider do not launch provider commands. Each editor entry
discovers the pending effective provider afresh; retained commits and redraws
within that entry do not repeat discovery. Model choices are
ordered Inherit, provider-order discovered models, Current custom when the
pending model is absent, and Custom model. Effort choices are ordered Inherit,
the pending effective Codex model's exact efforts or Antigravity's global
`low`, `medium`, `high`, and Current compatibility when a pending effort is
unreported.

Codex uses `codex debug models --bundled` and exact list-visible model effort
rows instead of freezing OpenAI's
[current model guidance](https://developers.openai.com/api/docs/guides/latest-model).
Antigravity uses `agy models`, preserves aligned opaque slug/display order, and
does not infer effort from names; Google's
[headless/CLI guide](https://antigravity.google/docs/cli/headless/) documents
the command and global effort values. Each attempt is limited to five seconds
and one MiB retained stdout, with no automatic retry, cache, persistence,
stderr, or raw diagnostic. Fixed failures preserve inherit/current/custom
paths; leave and re-enter to retry.

Apply Changes writes only the chosen opaque value or null. Custom models remain valid,
Codex effort tokens match `[a-z][a-z0-9_-]{0,31}`, and Antigravity effort
remains exactly `low`, `medium`, or `high`. The legacy backend name `gemini` is
rejected; use `antigravity`.

Selecting a provider freshly authenticates it before changing that pending
route. Authentication is subordinate to the Provider editor and returns there
after its result. The provider route and any missing workspace default remain
pending in memory; Escape or cancellation before confirmed Apply Changes leaves both
configuration files byte-for-byte unchanged. No configuration value is
persisted before Apply Changes is confirmed.

After Apply Changes is confirmed, every mixed candidate freshly gates every
distinct effective provider before the guarded file update; a stale or failed
status prevents the settings file transaction. A missing workspace default
learned during that gate remains in the pending candidate and becomes durable
only through the same complete Apply Changes action. Conversation-storage
readiness and selection are provider-free, but a mixed Settings apply retains
the provider-authentication gate. If Message details is the only change, Story
instead prepares, verifies, and commits that exact configuration candidate. It
skips authentication, storage readiness, backend/runtime activation, route
generation, and provider/thread work, then changes the active presentation flag
only after commit succeeds. If only Conversation RAG changes, Story prepares,
verifies, and commits the system configuration without authentication, storage
selection or readiness, backend/runtime prepare, commit, discard, or activation,
route/thread work, composition, optional-package probing or installation,
provider catalog, subprocess, socket, or network work. The current RAG service
and automatic policy remain unchanged.

Selecting SQLite records only a pending next-shell choice. During Apply Changes, Story
checks workspace coordination and the active Python interpreter's functional
`sqlite3` support. The standalone `sqlite3` executable and FTS5 are not
readiness gates. If a supported host prerequisite is missing, Settings first
shows the one exact command and warns that it changes host packages with the
real terminal attached. The separate confirmation highlights NO and runs the
command only after exact YES. Decline, recognized CI, unsupported platform,
installation failure, failed same-interpreter reprobe, provisioning failure,
or migration/parity failure leaves configuration unchanged.

An explicit SQLite choice may create the fixed empty database, but it never
copies conversations. If Apply Changes says migration is required, leave the current
shell, run the provider-free migration and verification commands in the active
direction, reopen `/settings`, select SQLite again, choose Apply Changes, and
start a new shell.
The same sequence applies in reverse when returning to filesystem. See the
[exact selection workflow](../reference/cli.md#conversation-storage-selection).

Apply Changes fails closed if either configuration file, the provider-route generation
record, or the persona catalog changed after opening. It also freshly checks
the desired conversation-storage readiness and parity before committing the
complete prepared snapshot. Provider, Model, or Effort fixed by a launch
override is disabled and read-only because that override remains higher
precedence.

A confirmed mixed Apply Changes applies live-capable runtime changes, including
provider/model/effort and Message details, to this running shell without a
restart. A Message-details-only Apply uses the smaller path described above. A
RAG-only Apply reports `Conversation RAG settings were saved for the next
shell. This running shell retains its current Conversation RAG service and
automatic policy.` A storage change reports `Restart is required for sqlite.`
when selecting SQLite (`filesystem` appears on the reverse change). Mixed
changes emit only the applicable current-shell, RAG-next-shell, and storage-
restart notices. Desired storage and Conversation RAG apply to the next shell;
the current controller/runtime bundles remain unchanged, and other running
shells keep the runtime state and bundles they captured.

If effective execution profiles changed, the completed Settings panel adds one
compact `[profiles changed]` count. It does not repeat every agent or changed
property. One provider switch adds `The provider switch starts a fresh thread
with story-session context on the next turn.` Multiple switches instead add
`Provider switches start fresh threads with story-session context on the next
turn.` Use `/agents` to confirm active values and confirmation state, or
`/status` for thread and source details after Apply Changes. Provider, model,
and effort changes are already active in this shell and need no restart.

Editing the JSON files directly is different: there is no file watcher. A
manual edit is reconciled when a new backend launches, while already running
shells remain unchanged. Use `/settings` when live-capable runtime settings in
the current shell must change without restarting; Conversation RAG remains a
next-shell choice. See
[Agent configuration](../reference/configuration/agent-configuration.md) and
[Filesystem safety](../operations/filesystem-safety.md). Antigravity setup and
provider-specific precedence are in the [Antigravity guide](antigravity.md).

<a id="execution-identity-and-fresh-thread-continuity"></a>

## Execution identity and fresh-thread continuity

The runtime, not an agent's prose, is authoritative for the provider, model,
and effort used for a turn. A reply that calls itself “Codex” is only model
text; use the live input header, reply metadata, `/agents`, or `/status` to see
the profile the backend selected and passed to the provider CLI. A missing
model or effort is shown as `provider default`: Story omitted that process
flag and does not claim to know the vendor's server-side choice.

Message details Off makes the live runtime header show only the target display
name and leaves direct replies compact. On makes the header show the resolved
provider/model/effort and confirmation, and adds one subordinate reply metadata
line containing Provider / Model / Effort and Thread. It adds only exceptional
`pending` and `launch override` qualifiers. `/agents` and `/status` retain full
source and confirmation diagnostics in both modes.

With Message details On, a capable input frame follows the active sticky agent, or a valid leading
`@agent` one-shot route, as the buffer changes:

```text
[active runtime] Lead Orchestrator · Antigravity · model agy-model-id · effort high · pending
story[novel:lead]> Who is answering?
```

With Message details On, the corresponding reply keeps compact execution
evidence beneath its speaker header and outside the reply body:

```text
Lead Orchestrator → You
  Antigravity / agy-model-id / high · reused
│ <agent reply>
```

`pending` means the changed profile is active for the next invocation but has
not yet completed a logical provider call. The first complete success changes
it to `confirmed`; this confirms runtime use, not remote attestation. A
same-provider model or effort change remains eligible to reuse its native
thread. Every provider switch advances a durable route generation, so switching
A to B and later back to A starts a fresh target-provider thread each time.

That fresh provider-native thread is not a fresh story session. Every
substantive turn rehydrates a bounded belonging-session envelope containing
the receiving agent's newest owner outcomes (at most 24 entries and 32,768
compact UTF-8 bytes), the current core-document path/hash identities, and one
focused-context mode:

- `included` carries the bounded complete-turn window for normal focused work;
- `fresh` carries the conversation identity but no focused transcript, as on
  `/run --fresh`; and
- `omitted` carries neither a conversation identity nor transcript for a
  context-omitted noninteractive run.

It intentionally does not carry another agent's owner outcomes, omitted
transcript events, provider-private hidden history, artifact bytes, or core
document bytes. Reference preludes are content-free; the next substantive call
carries the envelope. Retrieval call one carries it, while retrieval
continuations do not repeat it. See the exact
[provider envelope](../design/prompts-and-provider.md#story-session-continuity-envelope)
and [session storage](../reference/configuration/session-storage.md#provider-route-generations).

Conversation RAG field editing does not probe or install the optional package,
use the network, or prompt for credentials. At the next shell startup,
Disabled skips optional-package work. Local composes one runtime service beside
the complete active conversation-storage bundle with the saved Automatic
value. Missing or incompatible pinned dependencies leave configuration
unchanged, emit one fixed warning, and keep a RAG-free shell and `/settings`
usable. Install or restore the reviewed pin with
`python -m pip install --editable '.[rag]'`, then restart.

## Arm one private RAG preflight

`/rag QUERY` does not retrieve immediately. It privately arms one forced
preflight for the next eligible ordinary, `/say`, or routed `@agent` turn in
the exact attached session and focused active conversation:

```text
/rag lighthouse bargain
@plot Reconcile it with ${core_documents/global_outline.md} and ${manuscript/part-01/opening.md}#L20-L32.
```

The command is alias-free; its nonblank control-free query is limited to 200
codepoints. Rearming replaces the pending query. Ineligible commands retain it;
changing scope, pausing/detaching, quitting, or cleanup clears it. A
cancellation before the runtime coroutine enters retains it, while entry
consumes it even if the turn fails or is cancelled. The command does not echo
or persist the query, alter Automatic, or replace literal `/search`.

Artifact references belong in the following story message, as in the example.
Distinct-path whole-file and line-range references can coexist with that one
RAG attempt. The existing same-path whole/range rejection remains. The agent
receives bounded reauthorized excerpts and stable citations; the shell shows
only the content-free RAG outcome/reason, reply, and cited sources—not excerpt
bodies as diagnostics. Automatic On applies the same preflight to eligible
focused advisory turns only; debate and consensus remain unchanged.

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

The shell shows `Conversation scope` on initial attachment and again only when
the session, visible conversation handle, subject, or sticky agent changes.
Commands that leave those facts unchanged do not repeat the panel. Catalog and
focus freshness are still checked before every attached prompt, so an external
rename, archive, deletion, or handle-collision change is reflected on the next
prompt. Detaching clears the presentation snapshot; reattaching shows scope
again even when the semantic values match the earlier attachment.

References follow the same routing: `@plot Review ${core_documents/global_outline.md}`
addresses one agent once, while ordinary text after `/to character` addresses
the sticky character focus. Every provider-bound turn captures its own ordered
batch; a previous agent's read cannot satisfy the next turn. See
[Canonical artifact references](artifact-references.md).

After attachment, focus, target, and runner validation, an accepted ordinary,
`/say`, or `@agent` turn on a capable live stream appears as a `You → DISPLAY
NAME` chat block before retrieval progress or the reply. Its railed body is the
exact semantic message text without `/say` or route syntax. Ordinary-line
fallback retains its already-echoed input as the sole submission representation
and renders the reply through semantic plain output; it promises complete,
nonduplicated meaning, not identical topology. The accepted content remains
visible through later success, handled provider failure, cancellation, or
post-acceptance interrupt. It is terminal context; the conversation runtime
remains the authority for durable user/outcome events.

Artifact controls require all three of: an attached session, a focused active
conversation, and a cursor in provider-bound prompt text. Shell orientation and
full `/help` summarize this prerequisite. Lobby input, attached input without
focus, and management commands, IDs, options, subjects, output paths, and
settings remain inert.

While editing an eligible span, type `$` to open
`Writing artifact selection [open]`. It shows
`[controls] Up/Down selects; Enter/Tab inserts a whole-file reference.`, and
lists current safe reference sources in lexical order: the four supported core
documents and every eligible lowercase-`.md` descendant anywhere under
`manuscript/`, including root files, arbitrary nesting, spaces, Unicode, `{`,
`$`, and `#`. This read boundary is broader than the part-scoped publication
grammar. Type a case-sensitive path fragment after `$` to filter the list; no
match shows
`[empty] No matching writing artifacts. Change the path fragment.` Up/Down and
Enter or Tab insert `${path}` on a capable terminal without sending the prompt.
If no source exists, the panel says `No writing artifacts. Add a supported
canonical core document or Markdown file with a lowercase .md suffix under
manuscript/. Consensus can also publish one.` Enter or Tab emits the established
`empty_sources` error and restores the draft.

Type `$$` to see ranged-source choices immediately after the second dollar,
without Enter, Tab, or newline. Up/Down changes the highlight; Enter or Tab
opens that source in the independent viewer. Shift+Up/Down adjusts an inclusive
range while the viewer remains open, and Enter closes the viewer, returns to
the same composer, and replaces only `$$` with `${path}#Lx-Ly`. Escape closes
the source panel but preserves `$$`; a later Enter or Tab reopens it in place.

For a direct source, type a complete `$${path}` marker and press Enter or Tab.
Typing `{` immediately after `$$` changes from the source panel to literal
direct-marker entry, so paths with spaces, Unicode, `{`, `$`, and `#` remain
usable. Escape, Ctrl-C, EOF, or a handled read error restores the exact draft
and cursor with no dispatch or history entry. Type `\$` for a literal dollar.

Ordinary-line fallback uses numbered source choices and exact `START-END`
viewer input, then displays the amended draft for a second Enter confirmation.
It never submits a selection implicitly. Multiple completed indicators remain
in order. An indicator contains only a path or a path with one-based inclusive
line numbers, never the file body. Every later addressed agent or debate phase
performs its own enforced read of the complete ordered batch after normal
prompt submission.

## Choose the right memory operation

| Operation | What it does |
| --- | --- |
| `/history [COUNT]` | Local snapshot of current focus plus context accounting; no provider. |
| `/search QUERY` | Bounded literal search over active subjects/events; no focus required. |
| `/rag QUERY` | Privately arm one forced preflight for the next eligible focused advisory turn; no immediate read. |
| `/transcript ID [COUNT] [--from N]` | Fresh bounded page from active or archived identity; focus unchanged. |
| One advisory question | Agent may answer or request bounded active same-session search/read actions. |

Accepted local reads identify the request before their result. `/search` shows
its normalized query and active scope. A filtered `/conversation list` shows
its normalized query and active/all scope; an unfiltered list adds no request
record. `/transcript` shows the resolved visible handle and effective start and
count after identity resolution but before the page read. These records remain
if later work is empty, fails safely, or is cancelled.

Agent retrieval is conditional. The shell shows `[context]` progress, reply,
and sources. Subject and Conversation are always present on each source;
event-level sources always add Sequence, while Message details On also adds Turn
and Kind. History and transcript rows always show position, Sequence, a human
sender/recipient header, and the full body. On additionally shows canonical IDs,
Turn, and Kind. Only user and outcome enter the transcript; local reads never
call a provider or alter defaults/focus.

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

The runtime supplies every debate phase one bounded belonging-session envelope.
Normal `/run` includes one bounded focus snapshot. `/run --fresh --output
PART/PATH.md PROMPT` retains the story-session owner/core facts and focused
conversation identity but omits the focused transcript; `/draft-file PATH`
reads strict UTF-8, includes the snapshot, and prompts for the required output
path. After validation, inline, fresh, and committed composer prompts appear
once as a `You → Consensus` chat block with the full prompt body, followed by
`Consensus request` with the
safe output path, before debate progress. `/draft-file` emits no message copy of
its bytes; its request record names only the validated source and output paths.
Capable streams use the post-cleanup message, while fallback keeps its already
visible prompt without a duplicate. These terminal records survive later
success, deadlock, handled failure, cancellation, or post-acceptance interrupt
but add no separate storage guarantee. Runs do not change conversations.

Each accepted progress event then appears as one
`Debate phase · DISPLAY_NAME` panel with its fixed status fields and any newly
persisted contiguous speech sections. Active headings name their actual phase
without `[loading]`; successful completion is silent, while error or cancelled
state remains visible. The canonical Phase field and Outcome role/state are
unchanged. Full non-error speech remains visible. Message details On adds Handoff and speech
Kind; Off removes only those diagnostics. Successful consensus receives a
separate final `Consensus` Record after terminal progress; deadlock and failed
outcomes instead use typed error presentation.

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
