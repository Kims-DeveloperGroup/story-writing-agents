# Interactive terminal component

> **Availability:** this package-supported component owns shell input, safe
> records, and `/run --output PART/PATH.md` composition. Orientation, status/inspection,
> central errors/recovery, and accepted management, advisory, retrieval, and
> consensus output use its semantic renderer. Full/context help, agent roster,
> session/conversation lists, search, transcript, and focused history now do as
> well. Capable main input now provides selectable semantic command completion
> coordinated with bounded process-local history. Line-oriented RUN/CHAT
> composition and yes/no confirmation use semantic input views, and composer
> lifecycle records use semantic notices. A generic single-select picker
> foundation and a cancellable prompt seam are implemented behind unlisted
> package-internal adapters. Provider authentication and `/settings` consume
> the picker without changing the supported public terminal API; exact omitted
> `/session resume` and
> `/conversation resume` receive controller-owned main choices on capable
> streams. Highlighted Enter submits an explicit identity and bypasses the
> picker. Submitting the omitted command performs a fresh choice read; a
> successful read with eligible rows invokes a generic picker that is live on
> capable streams and numbered in fallback, while empty or failed reads report
> without opening it. Every capable input frame clears before return; fallback
> input remains visible without a synthetic final control frame. #274 adds an
> unlisted mode-gated accepted-input projection operation, and #275 uses it for
> accepted advisory and consensus prompt content. Capable streams render one
> exact-body attributed message after input cleanup and before progress;
> callback and ordinary-line fallback input remain visible without a duplicate.
> Consensus and local-read request records add safe result-defining context.
> Advisory and
> consensus turns additionally use
> one unlisted terminal-owned rolling indicator on capable raw streams;
> callback and incapable paths remain silent, and these waits emit no
> `[routing]` or `[waiting]` record. The same driver owns a private bounded
> startup welcome and transient authentication guidance lifecycle.
> In provider-bound prompt spans the installed
> shell now uses `$`, `$${path}`, and `$$` to discover current safe reference
> sources and invoke the independent package-internal ranged viewer. Exact `$$`
> opens ranged-source selection as soon as the second dollar is entered; it
> does not wait for Enter, Tab, or newline. The viewer remains an
> unlisted supplied-text component rather than a public command or selection
> API; downstream agent-read enforcement remains runtime-owned.
> The Settings editor exposes provider-neutral Provider, Model, and Effort
> fields; entering Model or Effort performs one fresh bounded discovery for the
> pending effective provider. Within `/settings` only, valid value commits keep
> the active editor open with updated selection/default and one replacing inline
> completed success result; a second consecutive commit of the same normalized
> value returns one level. The controller also marks dirty Save labels and
> unifies explicit Cancel with root Settings Escape. Generic picker/prompt reads,
> resume pickers, and Save/Cancel confirmations remain one-shot.

## Public API

Import from the package-supported facade
`story_writing_agents.interactive_terminal`. Its exact `__all__` is
`AttachmentKind`, `ComposeMode`, `ConfirmationResult`, `TerminalActionKind`,
`TerminalMessageKind`, `InteractiveErrorCode`, `TerminalAction`,
`InteractiveErrorSpec`, `INTERACTIVE_ERROR_SPECS`, `InteractiveError`,
`sanitize_terminal_text`, and `InteractiveTerminal`. Package leaves and
unlisted factory, model, control, renderer, and driver names are internal, not
additional supported API. The root `story_writing_agents` facade remains
unchanged and does not re-export the terminal.

```python
InteractiveTerminal(read_line, write_text, *, width: int = 80,
                    main_read_line: Callable[[str], str] | None = None)
read_main(*, session_id=None, agent_id=None) -> TerminalAction
request_conversation_subject() -> TerminalAction
request_confirmation(message: str, *, default: bool) -> ConfirmationResult
compose(mode: ComposeMode, *, agent_id=None) -> TerminalAction
write_lobby_orientation(workspace: str) -> None
write_session_orientation(workspace, session_id, attachment, agent_id, history_count=None) -> None
write_conversation_scope(*, session_id, conversation_handle, conversation_subject, agent_id) -> None
write_conversation_list_entry(conversation_handle, conversation_subject, *, archived, focused) -> None
write_conversation_search_hit(conversation_handle, conversation_subject, match_kind, snippet, *, archived, citation_turn_id=None, citation_sequence=None, citation_kind=None) -> None
write_conversation_transcript_event(conversation_handle, *, sequence, turn_id, kind, sender, recipient, content) -> None
write_agent_roster() -> None
write_status(kind: TerminalMessageKind, text: str) -> None
write_run(debate_id: str, text: str) -> None
write_agent(agent_id: str, text: str) -> None
write_help(command: str | None = None) -> None
write_error(error: InteractiveError, *, secrets=(),
            debug_log_path: str | None = None) -> None
sanitize_terminal_text(text: str) -> str
```

`main_read_line` defaults to `read_line` and serves only `read_main`; public
construction continues to use `read_line` for subjects and as the underlying
callback for composition, confirmation, and the internal picker and prompt
line fallbacks. Each composition or confirmation
attempt first renders its full semantic view to one structured plain prompt and
passes that prompt to `read_line` once. The CLI's package-internal wiring
separates subject, main, composition, and confirmation routes without changing
this signature or `__all__`. Record width starts at 20.
Callback-constructed terminals own no history, lifecycle, or storage; the
stream driver's process-local history is internal.

The exact `interactive_line_editor.__all__` remains
`SlashCommandSuggestion`, `slash_command_suggestions`, and
`InteractiveMainLineEditor`; its constructor and `read_line(prompt)` are
unchanged. Its public `read_line()` uses one per-editor process-local main
history store and the same supported internal main completion route;
composition-provider selection and the history-free contextual route are
package-internal, not added supported API.

For controller wiring, the package facade also exposes the unlisted canonical
identities `InteractiveSemanticOutput`, `Field`, `Message`, `Notice`, `Panel`,
`Record`, `Section`, `ListView`, `SemanticRole`, and `ViewState`. They are
package-internal, absent from `__all__` and the root package, and are not a
supported public API. The facade also binds the package-internal
`MainInputHistoryState`, `MainInputControlState`,
`append_main_input_history`, and `transition_main_input` identities used by the
compatibility editor; they likewise remain unlisted and unsupported.
`transition_main_command_input` and `read_main_command_line` are additional
unlisted internal bindings for the shared selectable main route. Its completion
models remain package-private and outside the closed `Component` union; only
their projected existing `SuggestionPalette` is rendered.
The facade also exposes the unlisted `Choice`, `Picker`, `Prompt`, `ControlOutcome`,
`ControlOutcomeKind`, `InteractiveMainInput`, `InteractivePickerInput`, and
`InteractivePromptInput` identities for controller-owned main choices, the
generic picker, and cancellable-line seams.
`PickerControlState` and `PickerViewport` remain package-private; the existing
semantic `Picker` remains a `Component`. None of these additions changes public
`__all__`, root exports, or supported signatures.

The package-internal `StartupWelcomeStage` and frozen/slotted `StartupWelcome`
values are a separate purpose-bound startup vocabulary, not `Component`
members. `StartupWelcome` carries only one validated stage; it has no width,
color, ANSI, glyph, copy, callback, stream, provider, authentication, or
terminal-lifecycle state. It is consumed only by the canonical startup
sequence, its core adapter, and the dedicated startup renderer.

The facade also binds unlisted `InteractiveStartupPresentation`,
`InteractiveAdvisoryProgress`, and `InteractiveDebateProgress` for startup
presentation plus advisory/consensus and Settings option-read awaitable
lifecycles.
They are package-internal and do not expose the driver, renderer, timing,
glyphs, cursor controls, or a public animation API.

The facade additionally binds the unlisted `InteractiveTextViewer`,
`TextViewerExitKind`, and `TextViewerResult` identities. They expose one
package-internal, storage-free supplied-text capability without changing the
exact public `__all__` or root facade.

The narrow internal adapters have these signatures:

```python
InteractiveSemanticOutput(terminal: InteractiveTerminal) -> None
write_component(component: Component) -> None
write_accepted_input(component: Component) -> None

InteractiveMainInput(terminal: InteractiveTerminal) -> None
read_main(choice_provider: Callable[[str, int], Picker | None], *,
          session_id: str | None = None,
          agent_id: str | None = None) -> TerminalAction

InteractivePickerInput(terminal: InteractiveTerminal) -> None
read_picker(prompt: Prompt, choices: tuple[Choice, ...], *,
            highlighted_id: str | None = None,
            selected_id: str | None = None,
            notice: Notice | None = None,
            state: ViewState = ViewState.OPEN,
            transient: bool = True) -> ControlOutcome

InteractivePromptInput(terminal: InteractiveTerminal) -> None
read_prompt(prompt: Prompt, *,
            context: tuple[Panel | Section | Field | Notice, ...] = (),
            transient: bool = True) -> ControlOutcome

InteractiveStartupPresentation(terminal: InteractiveTerminal, *,
                               sleeper: Callable[[float], object] = time.sleep) -> None
show_welcome() -> None
show_notice(notice: Notice) -> None
show_result(notice: Notice) -> None

InteractiveAdvisoryProgress(terminal: InteractiveTerminal) -> None
run(operation: Awaitable[object]) -> object  # async, package-internal

InteractiveDebateProgress(terminal: InteractiveTerminal) -> None
run(operation: Awaitable[object]) -> object  # async, package-internal

InteractiveTextViewer(terminal: InteractiveTerminal) -> None
read_range(text: str, *, display_name: str,
           initial_line: int | None = None) -> TextViewerResult
```

The viewer result contains only an exit kind and, for `selected`, a one-based
inclusive `(start_line, end_line)` tuple. It never carries supplied text,
display metadata, a path, a reference, a viewport, or terminal state.

Construction validates and retains the terminal's bound ordinary-component
writer and accepted-input writer but does not render, read, write, observe a
stream, or acquire a resource. `write_component()` performs one render and one
ordinary component write, including the final LF. `write_accepted_input()`
performs the same one-component render, then delegates the LF-terminated text
to the terminal's distinct mode-aware sink: callback construction is silent;
stream `LIVE` writes once; stream `FALLBACK` is silent; and stream `UNDECIDED`
rejects the lifecycle violation. Render and writer failures propagate without
a retry. Neither operation retains the component, rendered payload, or a
commit token. The main-input adapter binds
a call-local choice provider; the picker adapter likewise binds only the
terminal's picker reader without observing capabilities or acquiring I/O. The
controller supplies disposable session/conversation snapshots to the main
adapter and uses the picker/prompt adapters for provider authentication,
settings, and submitted
omitted resume commands whose fresh reads return eligible rows; none is public
API. The input adapters validate the
`transient`
argument for their internal signature but normalize supported reads to the
universal transient lifecycle; callers cannot request a persistent final
control frame. `InteractiveAdvisoryProgress` and
`InteractiveDebateProgress` are also unlisted and bind one call-local async
operation to the terminal's indicator without changing the supported public API.
The controller uses the same terminal-owned lifecycle for the bounded Provider,
Model, and Effort option reads described below; no general animation API is
introduced.

<a id="startup-welcome-and-authentication"></a>

## Startup welcome and authentication

The startup presentation uses the private frozen/slotted `StartupWelcome`
value and its `StartupWelcomeStage` vocabulary. `_core` owns one canonical
four-value sequence—`STORY`, `WRITING`, `AGENTS`, `HANDOFF`—and adapts the
driver's integer stage index to the corresponding validated purpose-bound
`StartupWelcome` value before calling the renderer. The renderer
receives that validated value, current capabilities, and physical width; it
never interprets a raw index.
The driver remains stage-unaware and owns only the index cadence, geometry,
terminal controls, cleanup, and restoration.

The stages are cumulative: `STORY` establishes the title identity, `WRITING`
adds the writing identity, `AGENTS` reveals the full team/wordmark identity,
and `HANDOFF` adds the completed shared-story orientation and says that Codex +
Antigravity access checking follows. `HANDOFF` is a handoff marker, not an
authentication-success state. These meanings remain stable across wide,
compact, narrow, plain, and ANSI projections; each responsive tier uses its
own fixed bounded copy.

The real stream terminal uses four fixed semantic welcome frames with a
physical-width projection. At 48 columns and wider, the frames progressively
reveal an original, prefix-free ASCII `STORY` wordmark. The completed wide
projection is exactly nine rows, its logo is at most 44 cells wide, and it
finishes with `WRITING AGENTS`, a blank row, and two concise access-orientation
lines:

```text
  ____     _____     ___     ____    __   __
 / ___|   |_   _|   / _ \   |  _ \   \ \ / /
 \___ \     | |    | | | |  | |_) |   \ V /
  ___) |    | |    | |_| |  |  _ <     | |
 |____/     |_|     \___/   |_| \_\    |_|
        W R I T I N G   A G E N T S

Four roles. One shared story workspace.
Next: checking Codex + Antigravity access.
```

At 32–47 physical columns, the compact projection uses the spaced `STORY`
title and `W R I T I N G   A G E N T S` identity; below 32 columns, the narrow
projection uses `STORY` and `WRITING AGENTS`. Both compact and narrow completed
projections are five rows and remain within their tier's safe width. The
wordmark has no bracketed semantic labels or wall-to-wall rules.

The physical width selects the tier before rendering. A capable live
projection reserves the rightmost physical cell, so the renderer receives
`physical width - 1` while still selecting the tier from the original physical
width; it reobserves that geometry for every frame. Static stream output uses
its full configured width, and callback construction uses its configured width.

On a capable POSIX ANSI TTY with reduced motion absent, the driver replaces the
four frames and waits exactly three times for 0.4 seconds each, then writes one
final newline so the completed frame remains in scrollback before provider
authentication. It never enters the alternate screen or hides the cursor.

When ANSI decoration is eligible, the five letters use fixed bold 16-color
foreground spans: `S` cyan, `T` magenta, `O` yellow, `R` blue, and `Y` green.
Each span has a local reset only; there are no backgrounds, blink, OSC, or
cursor sequences. Removing the local SGR produces the exact same ASCII text
and line topology as plain output. The wordmark is original; Oh My Zsh and
FIGlet are visual research inspiration only, not runtime dependencies or copied
branding.

The application color gate treats `NO_COLOR` as color-eligible when it is
unset, empty, whitespace-only, or whitespace-trimmed exact `0`—so
`NO_COLOR=0` explicitly keeps eligible color enabled. Any other value disables
decorative ANSI. The runtime only reads this value; it does not overwrite or
export the process environment.

`STORY_REDUCED_MOTION` is separate and presence-based; its value is not parsed.
When it is present on an otherwise ANSI-eligible capable TTY, the driver emits
one final colored frame with no animation, sleep, raw-mode activation, cursor
control, or clearing. A truthy/nonzero `NO_COLOR`, non-TTY, `TERM=dumb`,
non-POSIX, callback, or uncertain output path emits one width-appropriate plain
final frame with no delay or ANSI. These fallbacks do not include provider
output, account facts, URLs, tokens, credentials, or executable diagnostics.

The driver reobserves width before each capable frame and reserves the rightmost
column so wrapping and clearing stay inside the current terminal. It disables
echo/canonical input only under a guarded presentation, reads no input, clears
only its confirmed transient rows, and restores the original termios with input
discard before any native login inherits the terminal. It uses carriage return,
cursor-up, clear-to-end, and local SGR reset only; no provider or dynamic text
becomes terminal control.

The authentication picker labels Codex and Antigravity with explicit textual
states. Authenticated and unauthenticated choices are enabled, unavailable is
disabled with installation guidance, and indeterminate remains enabled for a
recheck. Highlight and current selection remain separate, and the generic
picker's cancellation, interrupt, EOF, capable cleanup, and numbered fallback
contracts apply.

Before native sign-in, one fixed sanitized loading notice is shown for at most
0.8 seconds on the capable path and cleared. Login completes either when the
child exits or when fresh authenticated status causes Story to boundedly stop
and reap that exact child. Story then resumes without extra input. The driver
first establishes a fresh output-line boundary, then shows one fixed success or
recovery result for at most 0.8 seconds and clears it. Static paths emit each
meaningful notice once with LF and no delay. Provider output, account facts,
URLs, return codes, tokens, and credentials are never interpolated into these
components.

| Enum | Exact values |
| --- | --- |
| `AttachmentKind` | `created`, `opened`, `reopened` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |

`AttachmentKind.OPENED` (`opened`) and `AttachmentKind.REOPENED` (`reopened`)
are result classifications rendered after a successful session transition.
They are not interactive command words; the human command is
`/session resume [NAME]`.

The established `routing` and `waiting` enum values remain compatibility
identities, but advisory and consensus wait paths do not emit records with
either label. Routing acknowledgements outside these paths retain their
existing meaning.

## Implemented semantic output and input

The import-compatible package contains private immutable semantic components,
input views, composer/history/completion/picker states, pure one-line,
selectable main/history, composer, confirmation, and picker transitions,
cursor-aware canonical grammar projection, responsive structured
plain/fixed-ANSI renderers, capability profiles, one raw driver, and one
real-CLI stream factory. These
internal seams support architecture and deterministic testing; they add no
public theme API or public component/control types.

The real CLI factory constructs one `InteractiveTerminal` around one raw
driver. That driver alone owns its streams, capability/width observation, key
decoding, functional cursor sequences, raw-mode activation/restoration, writes,
flushes, and one bounded process-local main history store. Each separate stream
terminal or compatibility editor therefore has an isolated store.
The factory also binds the terminal's accepted-input sink to that same driver's
selected-mode projection; it does not add a second raw owner or expose the
driver's latch to controller code. Callback construction retains a no-op sink,
so callback readers that already leave their submitted text visible receive no
duplicate record.
`interactive_line_editor.InteractiveMainLineEditor` remains a
standalone compatibility facade with its exact constructor and `read_line`
surface, delegating to the same driver implementation rather than owning a
second terminal lifecycle. Callback construction remains deterministic plain
and performs no stream observation or resource acquisition.

#83 uses this foundation for:

- lobby, session, and conversation orientation;
- interactive `/status` and `/inspect` projections built from frozen validated
  facts, without raw persisted messages, errors, candidate text, or provider
  state;
- typed interactive errors and recovery hints;
- accepted management acknowledgements and routing/current-context notices;
- advisory cancellation, context disclosure, and agent replies; #111 adds an
  optional capable-only indicator without an advisory waiting record;
- retrieval progress and cited-source records; and
- consensus progress, inspection, cancellation, and terminal summaries.
- Consensus progress now includes phase handoff/action metadata and a dedicated
  semantic `Panel` titled `Agent saying` when new agent messages are available
  for a phase.

#84 uses the same foundation for:

- full registry-backed help and one-command contextual help;
- the canonical four-agent roster;
- positioned session and conversation catalog rows plus explicit empty lists;
- positioned search hits followed by their result and optional truncation hint;
- positioned transcript events followed by their result and optional
  continuation hint; and
- positioned focused-history events followed by one context-accounting record.

#275 completes the informative-content consumers:

- accepted ordinary, `/say`, and one-shot routed advisory text becomes one
  exact-body `Message` attributed as `you → ALIAS`, where `ALIAS` is
  the resolved preferred prompt alias;
- accepted inline, fresh, and committed multiline consensus prompt text becomes
  one `you → consensus` `Message` followed by a `Consensus request` record;
- every consensus request names its validated `Output`, while `/draft-file`
  names only its validated `Source` path and never projects the file bytes;
- search and filtered conversation-list requests name their normalized `Query`
  and `Scope`; unfiltered lists add no request record; and
- transcript requests name the resolved visible `Conversation` handle plus
  effective `Start` and `Count` before the memory read.

The controller writes these components after acceptance and before coroutine
construction, progress, catalog/service work, or result output as applicable.
They remain visible through later success, handled failure, cancellation, or
post-acceptance interrupt. They are call-local terminal presentation and add no
storage, transcript-retention, provider-history, or audit guarantee.

The controller composes typed components and sends one component through the
adapter for each established callback record. The plain form preserves the
established facts, copy, order, and callback boundaries while adding explicit
structure and applicable `loading`, `empty`, `error`, `cancelled`, and
`completed` state markers. Capable real-CLI output may add fixed decorative
ANSI; `NO_COLOR` removes only that decoration. Callback construction renders
the same structure in plain text at its fixed width.

### Consensus progress records

Before consensus progress, an accepted prompt run emits its attributed user
`Message` and every run emits its request record. Draft input emits the request
record without a user-message copy of its source bytes. These records precede
coroutine construction, so a later cancellation, provider failure, deadlock, or
other handled outcome does not erase them.

Consensus-phase rendering now emits per-event metadata as fields (`Event`,
`Round`, `Revision`, `Handoff`, and `Action`, with optional `Conflicts`) in
one `Record`, then the phase status record. When phase messages exist, the
controller also writes one bounded `Panel` titled `Agent saying`, where each
message includes the sender label and its phase/revision metadata.

### Advisory, consensus, and Settings progress

The controller brackets advisory and consensus runtime awaitables, plus each
Settings Provider/Model/Effort option read, with the unlisted progress adapters.
On a capable raw stream, the sole driver renders the fixed
`◐`/`◓`/`◑`/`◒` cycle, clears the active cell around
semantic retrieval/progress/result writes, and clears it before a final record
or completed Settings picker. It joins the operation and indicator task on
every exit. `NO_COLOR` leaves the glyphs unchanged because they are not SGR;
the indicator has no dynamic text, alternate screen, background, or general
animation API.

Callback construction and incapable stream paths await the operation without
writing indicator output. This includes non-TTY, `TERM=dumb`, non-POSIX,
missing, and uncertain capability; the installed CLI guard still rejects
non-TTY shell entry before controller work. An underlying operation failure
takes precedence over a simultaneous indicator failure. If only the indicator
fails, the adapter raises its internal `AdvisoryProgressError` after cleanup so
the controller presents the existing `internal_error` path. Advisory and
consensus waits emit no `[routing]` or `[waiting]` record; their ordinary
result, cancellation, context, and error records remain unchanged.

The five dense-view terminal writer families now accept the same primitive
arguments and emit the same fact order through semantic `Panel`, `ListView`, or
`Record` values. Controller list commands emit one positioned, one-record
`ListView` for each established row callback. The terminal composer now emits
semantic `open`, `error`, `completed`, and `cancelled` notices instead of its
four generic-status call sites; the controller also has no generic
`write_status` call site. Composition and confirmation build ordered semantic
input views and use pure history-free controls as documented below. Selectable
main-command palettes and finite fixed options are available on capable main
reads. The generic picker and cancellable-prompt foundations remain available
only through unlisted internal seams. Provider authentication and `/settings`
consume the picker. A submitted
omitted `/session resume` or `/conversation resume` uses the picker only after
a successful fresh read returns eligible rows; empty and failed reads
short-circuit before picker input.

## Prompts and semantic input presentation

Exact ordinary and focused prompt identities include their final space:

```python
"story[lobby]> "
f"story[{session_id}:{alias}]> "
"conversation[subject]> "
f"compose[chat:{alias}:{line_number}]> "
f"compose[run:{line_number}]> "
"confirm [Y/n]> " or "confirm [y/N]> "
```

Each composition attempt renders, in order, `Mode`, optional `Target`, `Next
line`, `Lines`, `Characters`, `[info] Use /send to submit or /cancel to
discard.`, and the final focused numbered prompt. Each confirmation attempt
renders `Question`, `Choices: y/yes or n/no`, `Default`, and the final focused
`confirm [Y/n]> ` or `confirm [y/N]> ` prompt. Blank selects the displayed
default. Ctrl-C/EOF return typed outcomes.

Plain and fixed-ANSI input projections contain the same semantic text, wrap to
the active width, end in the focused prompt's required space, and have no final
LF. Safe line fallback receives the plain projection once. Capable live input
uses the trusted fixed-theme projection for redraw and sanitizes the dynamic
buffer separately.

Picker input is a separate semantic projection. Capable live input renders a
bounded viewport at the current observed width; a permanently fallback driver,
or a first input call that cannot safely preflight live mode, renders the
complete numbered list through the ordinary line callback. Opaque choice
identities are control state and are never rendered.

Settings custom-model and numeric values use a separate context-free semantic
`Prompt`. That route is history- and completion-free. Enter commits the exact
line for validation. A valid commit applies the pending value and immediately
reopens the same prompt with its default updated and one inline `Notice` using
`SemanticRole.SUCCESS` and `ViewState.COMPLETED`; a later valid commit replaces
that result. The prompt remains `ViewState.FOCUSED`. The `/settings` controller
may consume the next commit of that same normalized value as a silent return to
the parent; the Prompt itself still completes once per read and has no key-timing
policy. Escape returns one settings level without undoing applied pending
values, while Ctrl-C or EOF cancels the whole settings transaction. In fallback
an exact ESC line returns one level, while `/cancel` remains literal input. The
same sole driver owns raw input, redraw, cleanup, and restoration.

One permanent input latch now serves every input route on a stream-backed
terminal. Construction is resource-free; the first actual input read makes the
only mode decision:

| Internal mode | What selected it | What the user sees |
| --- | --- | --- |
| `UNDECIDED` | No input has been consumed. Capability and decoder inspection have not committed a route. | Nothing: construction and inspection do not print, read, wait on a descriptor, or mutate the terminal. |
| `LIVE` | The first read found a capable terminal and fully admitted its exact byte source, codec, error handler, controls, and terminal configuration. | Main, subject, composition, confirmation, picker, and prompt reads use cleared live frames; the viewer uses its independent alternate screen. |
| `FALLBACK` | The first read could not safely establish live input and entered its ordinary callback immediately. | Current and later prompts/viewers use their complete ordinary-line forms, whose printed text remains in scrollback. |

`LIVE` and `FALLBACK` are irreversible for that driver. A later live
capability, source, decoder, or terminal-configuration failure is an
operational error, not permission to switch to fallback. Conversely, a driver
that selected fallback never probes for a later live upgrade. The live route
keeps one exact-byte incremental decoder across main, subject, composition,
confirmation, prompt, picker, and viewer calls, so bytes already buffered for a
later interaction are not lost by mixing wrapper-level text reads with raw
byte reads. See the [terminal safety contract](../operations/terminal-safety.md#permanent-input-ownership)
for decoder and drift details.

After a read has selected the mode and returned, the unlisted
`InteractiveSemanticOutput.write_accepted_input(component)` operation renders
one closed semantic component in terminal core, appends its final LF, and
delegates the resulting string to the driver. `LIVE` uses the established
progress-safe persistent writer, so a consumer can project one accepted record
after the complete live frame has been cleaned up. `FALLBACK` deliberately
writes nothing because its ordinary prompt and submitted input already remain
visible. `UNDECIDED` raises before output because projection cannot choose a
mode. The driver interprets no component, stores no accepted payload or token,
and owns no additional cleanup. #275 invokes this operation only after
attachment, focus, collaborator, runner, and accepted prompt validation; the
controller still cannot inspect or alter terminal mode.

Every successful capable input operation clears its complete live frame before returning:
semantic context and prompt, editable text, suggestions or choices, validation,
and redraws. This applies to submit, commit, cancel, interrupt, and EOF across
main, subject, composition, confirmation, picker, and cancellable-prompt reads. An
exceptional failure attempts the same cleanup before propagating; an output
failure cannot guarantee erase bytes it could not write, and restoration
uncertainty remains fatal.
A static highlighted Enter only completes and leaves the main read active, so
it does not clear or enter history until later submission. Cleanup is display
only: an eligible main line is appended to driver-local history after cleanup
and restoration, and persistent semantic validation, cancellation, discard,
error, orientation, and result records remain in scrollback. A later
accepted-input projection is a new semantic record; it is not the restored or
retained editable frame.

Callback and ordinary-line fallback cannot erase printed prompt or input text,
so it stays visible. These paths do not add a synthetic final `completed` or
`cancelled` control frame. Pure picker state may still use those result values
internally. A conversation-subject read is history- and completion-free but,
after `LIVE` is selected, appears as the same cleared live input frame as other
semantic reads. Under `FALLBACK` it keeps the ordinary callback appearance.
The submitted subject text and its controller meaning are unchanged. The
normative lifecycle is in the [design contract](../design/interactive-cli-design-system.md#submitted-input-lifecycle).

Save shows a transient selectable confirmation with highlighted `YES` and `NO`
choices. Explicit Cancel and root Settings Escape invoke the same controller
action: clean state exits immediately without a confirmation, while dirty state
shows the existing transient Cancel confirmation. Its preceding context
contains only fields whose Current and Pending values differ, and both
displayed values carry the `(Changed)` cue; unchanged Save confirmation shows
only `No pending settings changes.` without empty Current or Pending sections.
These confirmations are one-shot: selecting `YES` commits and closes the
requested action, while `NO` or confirmation Escape returns to the root
Settings picker. Ctrl-C and EOF still cancel the whole flow without writing and
do not imply a snapshot was rendered.

<a id="provider-model-and-effort-pickers"></a>

## Provider Model and Effort pickers

Within `/settings`, the visible per-agent fields and editor titles are exactly
Provider, Model, and Effort. Current/Pending confirmations remain
provider-qualified—Codex model/reasoning and Antigravity model/effort rows—so
inactive persisted values remain unambiguous.

The visible top-level roster choice and roster picker are both named Agents.
Selecting an agent opens Agent settings; Provider remains the field-level
configuration concept rather than the roster name.

Only entering Model or Effort starts discovery. Opening Settings or Agents,
selecting an agent, navigating providers, and editing Provider launch no
catalog command. Each entry runs one fresh command for the pending effective
provider. Valid retained commits and redraws within that editor do not run
discovery again; leaving and re-entering does. Effort additionally resolves the
pending effective model from the agent value and then workspace fallback.

After the preceding capable picker is cleared, Provider authentication probing
and Model/Effort catalog discovery run under the terminal-owned `◐`, `◓`, `◑`,
`◒` progress cycle. The cell is cleared before the completed option picker is
drawn, so loading adds no durable row. Callback, ordinary fallback,
`TERM=dumb`, non-POSIX, missing-capability, and uncertain-capability paths run
the same option read silently. The indicator changes neither read frequency nor
the established failure guidance, choice ordering, cancellation, or save state.

Language, Persona, Maximum debate rounds, Conversation storage, Provider,
Model, Custom model, and Effort remain active after a first valid Enter. A
picker reopens in
`ViewState.OPEN` with its prompt focused and selection updated; a prompt reopens
in `ViewState.FOCUSED` with its default updated. The latest applied value appears
as the only visible inline `SemanticRole.SUCCESS`/`ViewState.COMPLETED` notice,
replacing the previous result. The next valid commit returns one level silently
when it has the same normalized value; a different valid value applies, remains
active, and re-arms that comparison. Invalid input, authentication failure,
Custom model navigation, and editor re-entry reset it. This controller state is
semantic and consecutive, with no raw-key timing window. Escape returns one
level through Custom model > Model > Agent settings > Agents > Settings;
Provider and Effort return directly to Agent settings, while Conversation
storage returns directly to Settings. Authentication launched while choosing
Provider is a subordinate action and returns to Provider after its result.
Ctrl-C or EOF at any level cancels the whole settings transaction.

Every one of those retained value editors also always renders the instruction
`Entered changes remain pending in memory and are not saved; choose Save in
Settings to persist them.` It remains present on initial entry, after a valid
commit reopens the editor, and during validation. It does not replace, duplicate,
or become the exact entered-value success result, which remains the only inline
completed result.

Each read of the top-level Settings picker structurally compares the complete
pending configuration with the opening baseline. Only a difference adds
`[save required] Pending changes are in memory only and are not saved. Choose
Save to persist them.` as a `SemanticRole.WARNING`/`ViewState.IDLE` notice. The
notice is absent initially, after a complete revert, and in Agents, Agent
settings, every value editor, and Save/Cancel confirmations. Capable, narrow,
`NO_COLOR`, and fallback projections keep the complete text. Fallback may retain
an older prompt in historical scrollback, but the current top-level prompt is
recomputed and truthful. Existing instruction and `Notice` semantics are used;
there is no new terminal primitive or global picker policy.

The same comparison projects the Save label as exact plain `Save` when clean
and exact plain `Save (*Required)` when dirty. Fixed ANSI warning decoration,
when eligible, is limited to `(*Required)`; the preceding separator space and
`Save` prefix stay ordinary. Plain, `NO_COLOR`, narrow, and fallback projections
preserve the exact plain label. The label does not replace the existing warning
`Notice`.

Model choices are ordered Inherit, discovered models in provider order,
Current custom when the pending value is not in that projection, and Custom
model. Effort choices are ordered Inherit, the exact effort rows for the
selected Codex model or Antigravity's global `low`, `medium`, `high`, and
Current compatibility when a non-null pending effort is unreported. Inherit
stores null, Current custom/compatibility preserve exact pending values, and
Custom model retains the safe text prompt. Persisted Codex efforts accept
`[a-z][a-z0-9_-]{0,31}`; Antigravity persists only `low`, `medium`, or `high`.

Codex runs exact `codex debug models --bundled`, keeps only
`visibility="list"` rows, and projects exact per-model
`supported_reasoning_levels` and `default_reasoning_level`. It does not
hard-code names from OpenAI's
[current model guidance](https://developers.openai.com/api/docs/guides/latest-model).
Antigravity runs exact `agy models` (or the resolved executable with `models`),
projects aligned opaque slug/display rows in provider order, and never infers
effort from model text. Its effort vocabulary follows Google's
[Antigravity headless/CLI guide](https://antigravity.google/docs/cli/headless/).

One attempt has a five-second deadline and a one-MiB retained-stdout ceiling.
It discards stdin and stderr, drops raw bytes after strict projection, and has
no automatic retry, cache, persistence, or raw diagnostic. Fixed failure
guidance leaves Inherit and applicable Current custom/compatibility or Custom
model paths available; re-entering the editor retries later. Save persists
only the selected opaque model/effort value or null and activates it on the
next shell/backend construction.

Every capable picker and input component follows the universal transient
lifecycle: commit, cancellation, interrupt, EOF, and failure erase or replace
the submitted control instead of leaving its snapshot in terminal display
history. Durable result/event components are separate. Numbered fallback
cannot erase bytes already printed, but adds no duplicate or synthetic final
control frame.

## Current semantic plain shapes

Every semantic `Panel` uses an ASCII title-bearing top splitter and a bottom
splitter, with no side borders:

```text
--- Panel title [completed] ------------
  PANEL CONTENT
----------------------------------------
```

The top run fills the remaining usable width when at least four cells remain.
At narrower widths, title and state text wrap with continuation indentation and
the trailing top run may be omitted; the bottom splitter and content remain.
The frame is part of the Panel's existing single render and callback write, so
callback cardinality and committed scrollback behavior are unchanged.
Standalone `Prompt`, `Section`, `ListView`, `Record`, `SuggestionPalette`, and
`Picker` components retain their unframed shapes. Nested components appear
inside a Panel's splitters without receiving their own frame.

Orientation uses titled `Story chat` or `Conversation scope` `Panel` values with
indented `Workspace`, `Session`, `Target`, optional `History`, `Conversation`,
`Subject`, and `Agent` fields in the established order. Status and inspection
use records headed by stable bracketed identities such as `[session]`,
`[conversation]`, `[context]`, and `[run DEBATE_ID]`. Values stack under their
labels at narrow widths instead of being dropped.

An attached controller presents `Conversation scope` initially, then only when
the semantic tuple `(session ID, visible conversation handle, subject, sticky
agent)` changes. It still revalidates any current focus against a complete
conversation-catalog read before every attached prompt, so an external rename,
archive, deletion, or handle-collision change can produce a new scope even
without a local focus command. Detachment discards the controller's loop-local
presented tuple, and reattachment therefore presents scope again. The terminal
remains stateless: `write_conversation_scope()` renders every call, retains no
tuple, and does not cache a failed write.

Agent and run output preserve content boundaries:

```text
ALIAS [completed]> FIRST LINE
                   CONTINUATION

[run DEBATE_ID]
  CONTENT
```

Accepted user-to-agent content uses the same message boundary. Command
envelopes and options are excluded from the body:

```text
you → lead [completed]> EXACT ADVISORY BODY
you → consensus [completed]> EXACT CONSENSUS PROMPT
```

The sender label uses the resolved preferred advisory alias or `consensus`.
Embedded LF, wrapping, visible control escaping, and narrow continuation
indentation follow the ordinary safe `Message` renderer; “exact” refers to the
accepted semantic body, not unsanitized terminal bytes.

Request-defining context uses completed `Record` values. Consensus field order
is `Output`, then `Source` only for draft input. Search and filtered-list field
order is `Query`, `Scope`; transcript field order is `Conversation`, `Start`,
`Count`. For example:

```text
[context] Consensus request [completed]
  Output: part-01/opening.md

[context] Search request [completed]
  Query: opening scene
  Scope: active
```

`Conversation list request` uses `Scope: active` or `Scope: all`.
`Transcript request` uses the resolved visible handle and the effective page
values, including defaults. `/draft-file` adds its safe source path to the
consensus record but never renders its loaded bytes.

Agent replies receive a presentation-only JSON projection before the semantic
message or record is rendered. The projection runs only when the entire
content string parses as strict JSON whose top-level value is an object or
array. Duplicate object keys and non-finite constants (`NaN`, `Infinity`, and
`-Infinity`) reject the projection. Accepted values are re-emitted with two
spaces per indentation level, preserved object-key order, and Unicode emitted
without ASCII escaping. Malformed JSON, embedded prose or extra data, scalar
JSON values (`null`, booleans, numbers, or strings), duplicate keys,
non-finite values, and any serialization failure return the original string
unchanged. The provider result and any stored transcript bytes are never
rewritten.

An `AGENT` semantic role is bold with the terminal's default foreground color;
it does not inspect or choose a terminal background. ANSI-capable output wraps
that emphasis in a local reset, while plain and `NO_COLOR` output retain the
same unstyled text.

Status roles and terminal states are textual. For example, consensus inspection
uses `[run DEBATE_ID] [completed] [success]`, interrupted inspection uses
`[cancelled] [warning]`, failed/deadlocked inspection uses `[error]`, active
inspection uses `[loading] [info]`, and a missing debate uses `[empty]`. These
markers are present in plain and `NO_COLOR` output; color is redundant.

Full help is one completed `Panel` titled `Help`. It retains registry group
order; each completed group section contains one completed `Commands` list
whose records retain syntax, aliases, provider-call, and artifact-write facts.
Context help is one completed `Panel` titled `Help` containing one command
record in `Group`, `Syntax`, `Aliases`, `Calls provider`,
`May write artifacts`, `Example` order. Agent roster is one completed `Agents`
list in canonical route order; each record retains display name, `ID`,
`Aliases`, and `Advisory domain`.

Session, conversation, search, transcript, and focused-history rows use this
positioned form, with one row per callback:

```text
TITLE (POSITION/TOTAL) [completed]
  [RECORD] [completed]
    LABEL: VALUE
```

Wide output keeps label/value pairs inline; narrow output stacks them instead
of dropping facts. Empty session and conversation catalogs are one explicit
empty `ListView`. Empty search and transcript results emit their exact empty
result notice without a synthetic row. Empty focused history emits no History
row and still emits the context record with `History displayed: 0/0 events`.

## Main-prompt live suggestions

The unified driver provides selectable semantic palettes at capable POSIX main
prompts. Static command completion uses `Suggestions [open]`; artifact
completion uses the distinct `Writing artifact selection [open]` contract
described below. A root `/` opens with its first candidate highlighted. Compact
registry order (not output layout):

```text
/say /to /agent /agents /auth /language /persona /reset /settings /run /draft-file /session
/conversation /search /transcript /inspect /history /status /help /? /quit /exit
```

A leading routed-message token opens with these full candidates, in route
order derived from each `AGENT_ROUTES` entry's `prompt_alias`:

```text
@lead @plot @character @execution
```

Exact case-sensitive prefixes filter in stable order. Root provides the 22
tokens above; help provides 25 including `/compose`, `/send`, and `/cancel`;
agent-valued slash positions provide the four preferred prompt aliases `lead`,
`plot`, `character`, and `execution` in the same route order. The parser still
accepts all ten declared route aliases for slash and routed-message input;
completion does not duplicate those aliases. Static finite positions also
provide session and conversation subcommands, `--fresh`, `--all`, `last`, and
`--from`. Static projection does not synthesize session, conversation, debate,
count, sequence, query, prompt, subject, or path values. Fixed-only positions
remain open with an explicit empty palette on no match; mixed fixed/free
positions close when the active slot is free text. Tokens do not reflect
runtime scope gates; `/help` remains authoritative.

Two exact omitted commands have a separate controller-owned live choice cycle:

- `/session resume` at the end of the buffer shows all persisted sessions,
  ordered by newest durable update then ID. Labels are session IDs; descriptions
  show `Durable: STATE; access: STATE`. Current attachment is highlighted first,
  then the saved default, then the first row.
- `/conversation resume` at the end of the buffer, while attached, shows active
  conversations in catalog order. Labels are subjects; descriptions show
  `Handle: HANDLE`. Current active focus is highlighted first, then an active
  saved default, then the first row. The committed identity is the immutable
  full ID, never the possibly shortened handle.

The controller owns catalog reads, ordering, labels, initial highlight, and all
authorization. The terminal owns only the call-local buffer, palette, keys,
rendering, cleanup, restoration, and history. Up/Down moves the live highlight;
Tab is a no-op. Enter commits the highlighted row as one complete explicit
canonical command in that same key event, clears/restores the live input, and
then records that explicit line in main history. This differs from static Enter
completion, which does not submit. Escape dismisses the rows without changing
the omitted command buffer or producing a domain cancellation record.

The displayed snapshot is disposable. Dynamic-choice Enter produces an
explicit command; after parsing, the controller freshly rereads and validates
that identity. A failed pre-Enter snapshot read only suppresses those rows. If
the omitted command is then submitted, its independent post-submit read may
still succeed and open the generic picker rather than repeat the first failure.
An empty post-submit result emits the documented empty record, and a failed read
emits the applicable error; neither opens the picker.

A submitted omitted resume command uses that history-free picker when the fresh
lifecycle/catalog read succeeds with eligible rows. On a capable stream the
picker is live and transient; ordinary-line fallback shows the numbered form.
This includes Enter after dismissing the pre-Enter rows with Escape and nonempty
configured VEOF, which submits the unchanged omitted buffer even while a row is
highlighted. Main history records the omitted command once whether a picker,
empty result, or error follows; an opened picker's answer adds no entry.
Dynamic-choice Enter instead records the full explicit command and bypasses the
picker. Explicit `/session resume NAME` and `/conversation resume ID` remain
direct forms.

Picker cancel, interrupt, and a selected identity rejected before session
acquisition or conversation-default persistence are mutation-free. Picker EOF
selects no destination and proceeds through normal shell cleanup. A session
choice that advances runs provider validation,
lifecycle pre-read, acquire or durable reopen, correlated post-read, destination
focus load, destination-default persistence, source release when switching,
and only then in-memory attachment/focus commit. A successful paused-to-ready
reopen is not rolled back if a later step fails. Later failure releases newly
acquired access where possible and does not advance in-memory attachment/focus,
but no durable lifecycle or default rollback is promised. Source-release
failure after default persistence is fatal `cleanup_failed`, and the new
default remains. A reported default-write failure can occur after atomic
replacement but during later durability work; inspect persisted state instead
of assuming the old default. Conversation resume likewise persists its default
before changing in-memory focus; on a reported write failure the focus stays
put, but the persisted default must be inspected. Successful session switch
resets agent focus to `lead`; conversation focus change preserves it.

The routed-message palette is cursor-aware and applies only when the first
token begins at the start of the buffer and is cursor-active. It does not
project for a non-leading `@`, while the cursor is in the message body, or for
`@` text inside slash message input. For example, Enter or Tab completes `@pl`
to `@plot ` under the shared spacing rule. Enter completion does not submit;
after typing the message body, Enter submits the visible routed message.
Submitting a route with no nonblank message remains an error.

The active token is the maximal ASCII-space/tab-delimited span around the
cursor. With a highlighted static suggestion, Tab or Enter replaces that full
span with the candidate. When the replaced token ends at the buffer end,
completion adds exactly one ASCII space; otherwise the existing space/tab delimiter and
suffix remain exact. Help permits a bare prefix to select a slash replacement,
for example
`/help ru` to `/help /run`. Up/Down moves the highlight and clamps at the ends
without replacing text or traversing history. Filtering keeps the highlight
when its identity remains eligible; otherwise the first match is highlighted.
Tab is a no-op when there is no highlight or the replacement and spacing would
leave the line unchanged. Shift-Tab is always a no-op.

The first Tab that changes text retains the complete pre-completion buffer and
cursor. Edits in the same grammar position keep that snapshot; a position
change resets it. Escape restores the snapshot when present, otherwise leaves
the buffer unchanged, then closes the palette and latches that exact line state
dismissed. An edit or history change clears the latch and permits reopening.
Closed-palette Up/Down resumes command-history navigation. Enter with a
highlighted static suggestion applies the same replacement and spacing as Tab
without submitting, then closes the palette and latches the completed line
dismissed;
a second Enter submits that exact visible buffer. Enter with no highlighted
suggestion submits the exact visible buffer to the established parser once.
Configured VEOF with a nonempty buffer always submits the exact visible buffer,
even when a suggestion is highlighted.

Plain, fixed-ANSI, `NO_COLOR`, narrow, and empty palette rendering preserve the
same semantic candidate order and highlight, including controller-owned resume
rows. Ordinary-line fallback receives no pre-Enter palette projection and
retains its established behavior. Settings and the
subject keep their non-main reads, composition uses the separate preview-only
fixed suggestions below, and confirmation has no suggestions. None of those
contexts receives the leading `@` palette. Editing, decoding, fallback, and
restoration details are in
[Terminal safety](../operations/terminal-safety.md#live-editor-boundary); the
pure control contract is in the
[design contract](../design/interactive-cli-design-system.md#main-command-palette-and-fixed-choices).

## Main-command history

Each real stream terminal and each `InteractiveMainLineEditor` owns one
driver-local store of at most 100 main-prompt entries. Up recalls older entries;
Down recalls newer entries. The first Up saves the exact current draft and
cursor, and Down past the newest entry restores both. Recalled text is an
editable copy with the cursor at its end. Further navigation can replace that
edited copy without changing the stored entry.

The driver records each successfully completed exact nonblank main submission once,
before the existing parser runs. Leading and trailing whitespace are preserved;
invalid command text, duplicates, `/quit`, ordinary-line fallback results, and
a nonempty configured VEOF commit are therefore eligible. The 101st entry
evicts only the oldest. The exact visible submitted buffer still reaches the
parser once. A capable dynamic resume choice records its complete explicit
command. Any submitted omitted resume buffer records that omitted command even
when its fresh read reports empty or fails. If a picker opens, its history-free
answer is never a following entry.

Empty or whitespace-only input, Ctrl-C, empty configured VEOF, physical stream
EOF, incomplete input, invalid fallback framing, and operations that fail
without returning an accepted line are not recorded. Composition and its
contextual suggestions, conversation subject, confirmation, previews, and
uncommitted edits neither navigate nor mutate the store. A line-fallback main
read can add an entry, but the component supplies no arrow navigation in that
fallback. Because `FALLBACK` is permanent for the driver, the retained entry
cannot later be recalled through a capable read on that same input UI.

History lasts only for that driver instance and is never persisted to the
workspace, transcripts, configuration, environment, provider sessions, or a
user history file. An open main palette consumes Up/Down for clamped highlight
navigation; closed-palette Up/Down retains the history behavior above.

## Composition live suggestions

On capable POSIX live editing, RUN and module-supported CHAT composition reads
routed through the unified stream terminal's explicit composition route show
`[suggestions]` with only these tokens, in this order:

```text
/send
/cancel
```

Matching is an exact, case-sensitive prefix of the whole current buffer. `/`
shows both entries, `/s` narrows to `/send`, and `/c` narrows to `/cancel`.
Whitespace, an empty buffer, or any nonmatching text clears the suggestion
preview. `/say` is not suggested; `/say /send` and `/say /cancel` keep their
established literal-escape meaning and show no preview because the buffers
contain whitespace.

This is preview only: it does not complete, replace, select, parse, or dispatch
the buffer and does not call a story provider, runtime, filesystem, or storage.
Enter submits the unchanged buffer to the existing composition rules. The
installed shell enters RUN composition after the controller has obtained and
validated the required output path; bare `/run` therefore shows that prompt
first. CHAT composition remains a module-supported terminal operation. At the editor boundary,
unsupported, `TERM=dumb`, non-TTY, and non-POSIX reads use ordinary line input
with the structured plain semantic prompt once and without a suggestion preview;
the installed CLI's earlier dual-TTY guard still applies.

Composition remains line-oriented and history-free. Up/Down, Tab, Shift-Tab,
and Escape are no-ops. An ordinary line updates the next view's exact line and
character counts. `/send` commits only nonblank LF-joined content; an empty
send writes one semantic error notice and retries. `/cancel`, Ctrl-C, and EOF
discard with counts only. `/say /send` and `/say /cancel` append literal
`/send` and `/cancel` lines. Entry, empty-send, send, and discard each retain
one semantic notice and one callback write.

## Confirmation input

Confirmation uses the same history-free semantic input route with an empty
suggestion set. Up/Down, Tab, Shift-Tab, and Escape are no-ops. The exact
accepted values remain case-insensitive `y`, `yes`, `n`, and `no` after
surrounding whitespace is ignored; blank selects the displayed default.
Invalid input writes one `[hint] Enter y, yes, n, or no.` semantic error notice,
marks `Choices` with error state in the next view, and reads again. A valid
answer produces `yes` or `no`; Ctrl-C and EOF produce `interrupt` or `eof`.
None of those terminal results adds a final confirmation notice.

<a id="generic-internal-picker-foundation"></a>

## Generic internal picker foundation

This foundation is package-internal. Provider authentication, `/settings`, and
the post-submit controller path for omitted `/session resume` or
`/conversation resume` consume it through
the unlisted terminal facade adapter. Capable pre-Enter resume choices reuse the
main palette described above; only their dynamic Enter commit becomes explicit
and bypasses the picker. Submitting the omitted buffer instead performs a fresh
read. A successful read with eligible rows opens this picker live on a capable
stream or numbered in fallback; an empty or failed read does not open it. A
caller supplies one semantic prompt and immutable choices whose opaque
identities are returned on commit but never rendered as picker identities.

The generic picker remains commit-once. `/settings` alone may consume that
commit, immediately open a new value-editor read with updated state and an
inline success notice, then interpret a consecutive commit of the same
normalized value as a silent parent return. The generic Picker has no retained
state, second-Enter rule, or raw-key timing window. Save/Cancel confirmations
and context-changing consumers remain one-shot, especially
`/session resume` and `/conversation resume`: after selection the picker is no
longer active, its capable frame is cleared, and fallback text may remain only
as inactive scrollback.

Initialization highlights an explicit enabled choice, otherwise an enabled
selection, otherwise the first enabled choice. Empty and all-disabled choice
sets remain explicit `empty` and `disabled` states.

When the submitted omitted `/session resume` picker opens, it supplies one
enabled choice per persisted lifecycle, ordered by newest durable update then
ID. The visible label is the
session ID, and its description is
`Durable: STATE; access: STATE`. The controller explicitly highlights the
current attachment, then the saved default; generic initialization chooses the
first row if neither is present. A commit returns the identity; the controller
reports an unchanged current attachment or freshly validates another choice
before the established session resume transition. Escape or Ctrl-C produces
the persistent `[cancelled] Session resume cancelled.` record without mutation,
while EOF returns to normal shell cleanup. The controller handles an empty
catalog without opening the picker.

When the submitted omitted `/conversation resume` picker opens, it supplies one
enabled choice per active conversation in catalog order. The visible label is
the subject, and its
description is
`Handle: HANDLE`, where `HANDLE` is the stable first 12 ID characters when
unique across the complete session catalog and the immutable full ID on a
collision. The controller explicitly highlights current active focus, then an
active saved default; generic initialization chooses the first row if neither
is present. A commit returns the opaque immutable full ID, which the controller
freshly resolves against the complete catalog before saving the default and
changing current focus. A missing or newly archived choice is rejected,
refreshed metadata is used, and a reported save failure does not change
in-memory focus but may follow replacement of the persisted default. Inspect
the persisted selection after such an error. Escape,
fallback `/cancel`, or Ctrl-C writes
`[cancelled] Conversation focus cancelled.` and leaves catalog, default,
current focus, and agent focus unchanged; EOF returns to normal shell cleanup
without a selection. An
empty active set emits `[conversation] No active conversations found.` without
opening the picker.

Highlight and selection are independent until commit, so a frame can show one
selected choice and another highlighted choice. Up/Down moves the highlight
among enabled choices, skips disabled choices, and clamps at the first or last
enabled choice without consulting main history. Enter commits the highlighted
identity and makes it selected; Escape returns `cancel` while preserving the
prior selection. Configured VINTR/Ctrl-C and VEOF/physical EOF return the typed
`interrupt` and `eof` outcomes. Other edit and completion keys do nothing and
the picker never owns main completion state.

Live rendering shows a contiguous viewport of at most seven choices and
re-observes capability and width on each redraw. Selection and highlight may
appear simultaneously on one row; a selected choice outside the viewport is
summarized by ordinal and label. Choice-set reconciliation preserves enabled
selection and highlight by identity when possible and deterministically finds
the next enabled highlight when not. Opaque identities remain absent from
plain, fixed-ANSI, `NO_COLOR`, narrow, and fallback text.

Line fallback renders every choice as a numbered row and accepts only:

- exact `/cancel`, which cancels;
- a blank line when an enabled choice is highlighted, which commits that
  highlight; or
- an ASCII decimal ordinal with no leading zero that is in range and enabled.

Zero, leading-zero, signed, spaced, non-ASCII, out-of-range, disabled-choice,
name, label, identity, and other command input is rejected without changing
selection or highlight. The retry adds exactly:

```text
Enter an enabled choice number, press Enter for the highlighted choice, or enter /cancel.
```

Pure transitions represent commit or cancel with internal `completed` or
`cancelled` state, but supported adapters never emit a final control frame.
Capable reads clear the complete live picker before returning commit, cancel,
interrupt, or EOF and attempt the same cleanup on failure; restoration failure
remains fatal. Callback and
line fallback cannot erase the already printed numbered prompt or input, so
they retain it and add no synthetic terminal-state frame. Nested settings Escape
returns to the parent silently. Save and Cancel confirmations show a selectable
YES/NO control with changed-only Current/Pending context; unchanged context
contains only the no-changes notice. Top-level cancellation does not claim a
snapshot that was not rendered.

A permanently fallback driver, or a first input call with non-TTY,
`TERM=dumb`, non-POSIX, or uncertain capability, uses the numbered fallback
without acquiring raw input. The unified driver remains the only raw resource
owner and picker reads do not inspect or mutate main history, completion,
parser, provider, runtime, filesystem, storage, or network state.

<a id="artifact-reference-activation"></a>

## Artifact-reference activation

The controller binds three call-local collaborators to the terminal's main or
composition input: a provider-text-span projection, a sorted eligible
reference-path reader, and a bounded verified text reader. Nothing activates when the
cursor is outside that span. Ordinary text, valid `@agent` and `/say` tails,
and `/run` prompt remainders are eligible; command, option, identity, output,
and every non-provider field are inert. `\$` is literal.

The private pure activation layer recognizes only the cursor-active marker.
`$` projects `Writing artifact selection [open]`, followed by
`[controls] Up/Down selects; Enter/Tab inserts a whole-file reference.` Exact
`$$` projects the same sorted paths in a distinct ranged-source position and a
correlated picker request. The input controls therefore open the nonmodal panel
on the second dollar and own Up/Down highlight movement. Enter or Tab correlates
that highlight with the unchanged request and emits a direct viewer request;
the terminal driver then owns viewer entry, input, alternate-screen cleanup,
and return. Escape closes the panel, retains `$$`, and latches that dismissal;
later Enter or Tab reprojects the same panel in place.

Typing `{` after `$$` leaves ranged-source selection and preserves direct
`$${path}` construction. Once its closing `}` is present, Enter or Tab requests
that viewer directly. One-character input, a batched write, and bracketed paste
retain complex safe paths rather than treating their internal `{`, `$`, or `#`
as another selection. A case-sensitive path fragment after one `$` filters the
whole-file rows. No match projects the artifact-specific empty text; a truly
empty source list projects `No writing artifacts. Add a supported canonical
core document or Markdown file with a lowercase .md suffix under manuscript/.
Consensus can also publish one.` plus a deferred
`ReferenceInputFailureKind.EMPTY_SOURCES` (`empty_sources`) failure that Enter
or Tab surfaces through the established error writer. Enter or Tab on a
whole-file source candidate replaces the marker with `${path}` without
submitting. Range selection replaces it with `${path}#Lx-Ly`. Several completed
references remain ordered; each later activation receives a fresh source list
and text read.

The source callback returns the four exact supported core paths and every safe
lowercase-`.md` descendant anywhere under `manuscript/`. Direct
`manuscript/*.md` files, arbitrary nesting, spaces, Unicode, `{`, `$`, and `#`
are eligible; publication grammar remains independently stricter. The terminal
neither scans directories nor loads publication state itself.

The immutable buffer/cursor at activation is the origin. A successful edit
changes only the marker span and places the cursor after the indicator.
Cancellation, interrupt, EOF, stale/unreadable/unavailable repository state,
or invalid replacement restores the origin and performs no parser, runtime,
history, or provider action. The driver runs nested picker/viewer input under
the same irreversible input latch and releases every call-local collaborator
on return. In line fallback, activation starts after the marker line is
submitted; completion/viewing is followed by a second exact submission prompt.
A blank answer commits the amended draft and a replacement draft is reevaluated.

See [Canonical artifact references](../guides/artifact-references.md) for the
user controls, fixed errors, limits, and provider-read sequence.

| Internal control state | User-visible state | Side effects |
| --- | --- | --- |
| Eligible outer editor | The prompt and cursor are editable; no reference control is open. | None. This immutable origin is retained for restoration. |
| Whole-file palette | One `$` shows filtered `${path}` choices. | Fresh metadata listing only. |
| Ranged-source palette | The second `$` immediately shows paths; Up/Down moves the highlight. | No viewer read, history append, parser call, or provider call. |
| Direct marker | `{` after `$$` closes the panel and leaves literal `$${path}` text under construction. | No source read until Enter/Tab activates a complete marker. |
| Independent viewer | The correlated source is freshly read and displayed; Shift+Up/Down adjusts an inclusive range. | Call-local bounded read and terminal lifecycle only. |
| Restored or amended editor | Cancel/error restores the origin; Viewer Enter replaces only the marker with `${path}#Lx-Ly`. | Still no submit, history append, or provider call. |
| Normal committed line | The completed prompt leaves editing through the ordinary submit path. | History/parser/runtime proceed; each addressed agent must read every indicator through its authorization. |

<a id="independent-ranged-text-viewer"></a>

## Independent ranged text viewer

> **Availability:** the viewer remains an unlisted package-internal component
> for exact caller-supplied text. The installed controller now calls it after an
> explicit `$${path}` activation or an immediate `$$` ranged-source selection
> in provider-bound prompt spans. The caller—not the viewer—discovers and reads the artifact,
> restores the draft, attaches its path to returned line numbers, and later
> submits the prompt through the established runtime path.

Each call accepts one exact `str`, a nonblank one-logical-line display name,
and an optional one-based initial line. Before inspecting a stream, observing
terminal capability, or performing I/O, it validates exact types, strict UTF-8
for text and display metadata, at most 65,536 LF-derived logical lines, the
initial line against the derived document, and a private 8 MiB UTF-8 text
limit. The line limit is checked before line splitting or materialization.
The terminal limit is an independent defensive boundary, not the
writing-artifact reference limit. The artifact caller enforces its own limit
before supplying verified text.

Entry validation also proves that a complete sanitized ordinary-line fallback
frame fits a private 16 MiB UTF-8 budget. That budget includes sanitized
display metadata, every numbered source record, separators, notices, and the
prompt. A frame exactly at the limit is accepted; a larger frame is rejected
before capability checks, callbacks, reads, writes, or raw-terminal work.
Because visible escapes can expand source characters, otherwise valid text can
exceed this fallback budget. This complete-fallback invariant applies even
when the eventual interaction would have been capable and live.

Only LF separates logical lines. Empty text has zero lines; one final LF does
not add a phantom line, while preceding empty lines remain real lines. CR,
U+2028, and other terminal controls are content, not separators, and render as
visible escapes. The display name is likewise sanitized before presentation.

| Live key | Exact effect |
| --- | --- |
| Up / Down | Clear any anchor, move the active line by one, and clamp at the document boundary. |
| Shift+Up / Shift+Down | Fix the first active line as the anchor, then move the active end. Repeated keys extend, contract, reverse, and clamp the range deterministically. |
| Enter | Commit the active singleton or normalized anchored inclusive range. Empty text cannot commit and remains open with a visible notice. |
| Escape | After a fixed 50 ms wait distinguishes a standalone Escape from a key sequence, return `cancelled` without a range. |
| Ctrl-C / configured VINTR | Return `interrupted` without a range. |
| Configured VEOF or stream EOF | Return `eof` without a range. |
| Other keys | Make no state change. A nonexit key still triggers the next geometry observation and redraw. |

On a capable raw terminal, the sole driver enters one alternate screen and
renders one physical row per logical source line. Every row carries a
one-based number; plain `>` and `*` cues identify the current and selected
lines, with fixed ANSI emphasis added only when decoration is enabled. Long
rows are clipped with a Unicode-cell-aware ellipsis instead of wrapping.
Each live row sanitizes and scans only the bounded prefix needed for its width
plus a fixed 256-source-code-point allowance. Therefore an unseen multi-MiB
suffix or a long leading run of zero-width combining characters cannot add
unbounded work to each redraw.
The contiguous viewport is bounded by current terminal height. Width and
height are observed before the first frame and again after each nonexit key, so
a resize appears on the next interaction without a `SIGWINCH` handler. The
viewer never hides or shows the cursor; cursor visibility is left unchanged.

Byte-backed `LIVE` input is allowed only when the public codec registry
resolves the declared encoding to canonical `ascii`, `utf-8`, or
`iso8859-1`, with the built-in `strict` or `surrogateescape` error handler;
registry aliases for those codecs are accepted. The driver preflights that
decoder and its exact byte source before terminal mutation. On the first input
call, every other or unknown byte codec—including ISO-2022 variants, HZ,
UTF-7, UTF-8-SIG, and `raw-unicode-escape`—selects permanent ordinary fallback
before `PC_VDISABLE`, control attributes, byte reads, live-terminal output,
raw mode, or alternate-screen work. This is an allow-policy, not a deny-list of
those examples.

Within an allowed byte codec, configured VINTR and VEOF bytes are compared
with `PC_VDISABLE` before decoding. Each enabled byte must decode to exactly
one code point and round-trip to the same byte under the stream's encoding and
error policy. Compatible UTF-8 `surrogateescape` controls work; incomplete,
lossy, or incompatible mappings select ordinary fallback only while the input
latch is `UNDECIDED`; after `LIVE`, a changed mapping is an operational drift
error. Text-only collaborators retain their supplied one-code-point control
semantics. If one input character has multiple roles, configured VINTR has
priority, then configured VEOF, then the ordinary viewer key role.

When the driver is already in `FALLBACK`, or the first input call cannot safely
construct live setup, ordinary-line fallback prints the complete numbered,
sanitized document and accepts only exact ASCII positive `START-END` or exact
`/cancel`.
It never width-clips or adds an ellipsis to accepted document content: every
sanitized source character is emitted, and long logical lines wrap naturally
in terminal scrollback. The one-based numbering and range prompt remain exact.
Endpoints may be reversed and are normalized; zero, leading zero, signs,
spaces, non-ASCII digits or dash, missing endpoints, and out-of-range values
produce a visible retry. The fallback uses no ANSI, raw mode, alternate screen,
or synthetic final control frame, and its printed attempts remain visible.

| `TextViewerExitKind` | `line_range` |
| --- | --- |
| `selected` | Required normalized one-based inclusive tuple. |
| `cancelled` | `None`. |
| `interrupted` | `None`. |
| `eof` | `None`. |

The viewer lifecycle is validation, permanent input-mode resolution if still
`UNDECIDED`, raw activation when `LIVE`, alternate-screen entry,
navigation/selection, typed outcome or operational failure, alternate-screen
exit once, termios restoration once, and disposal. After `LIVE` has been
selected, a failure never starts a second fallback interaction.
Operational read, render, write, flush, or collaborator failures propagate
after cleanup rather than becoming a viewer result. Restoration failure takes
precedence over screen-exit failure, which takes precedence over the active
failure. See the [terminal safety boundary](../operations/terminal-safety.md#ranged-text-viewer-boundary)
and [recovery behavior](../operations/recovery-and-limitations.md#ranged-text-viewer-exits-and-recovery).

Source text, display metadata, active line, anchor, viewport, and result
correlation are immutable and call-local, then dropped on every return or
raise. This is lifecycle disposal, not secure erasure from Python, terminal,
or emulator memory. Reopening always starts from caller-supplied text and
optional initial line. Selecting a range or cancelling closes the independent
viewer after cleanup and returns to the exact originating composer; it never
leaves the caller trapped in viewer mode. The
return is content-free and contains only the typed exit and optional line
numbers. The installed caller invokes this stateless component repeatedly for
multiple ordered references. It reads current canonical files, retains and
restores the immutable originating draft/cursor, and attaches the path to a
selected range; the viewer itself owns none of those responsibilities.

Users can read—but not edit—canonical text, select inclusive lines, and return
to the exact originating prompt without submitting it. Within this internal leaf,
accepted content remains fully visible in fallback and safely bounded per
redraw in live mode. Over-limit pathological documents do not open, fallback
content is never silently truncated, and an incompatible configured control
byte is never guessed into a live key role.

<a id="exact-search-and-transcript-records"></a>

## Dense search, transcript, and history records

Before search catalog/service work, one completed `Search request` record names
the NFKC-normalized, case-folded, whitespace-collapsed query and `active` scope.
A filtered `/conversation list` similarly emits `Conversation list request`
with its normalized query and `active` or `all` scope before its catalog read;
an unfiltered list emits no request record. After transcript identity resolution
and before its memory read, `Transcript request` names the visible handle and
effective start/count. Each accepted request record remains visible if later
work is empty, fails safely, or is cancelled.

Search rows retain these fields in exact order: `Status`, `Match`, `Subject`,
`Citation`, `Snippet`. A conversation-level citation is
`conversation HANDLE`; a transcript citation adds `turn TURN, sequence
SEQUENCE, kind EVENT_KIND`. Each hit is one completed positioned `Search
results` list and one callback. Rows are followed, in separate callbacks, by:

```text
[result] Search returned {HITS} of {MATCHES} matching conversations.
[hint] Search was truncated by fixed scan or result limits; refine the query.
```

The hint appears only when the established limits truncate the result.

Transcript and focused-history rows retain `Sequence`, `Turn`, `Kind`,
`Sender`, `Recipient`, then `Content`, in that order. Each event is one
completed positioned `Transcript` or `History` list and one callback. A
transcript page then emits:

```text
[result] Transcript page returned {EVENTS} events.
[hint] Continue with /transcript {HANDLE} {COUNT} --from {NEXT}.
```

The continuation hint appears only when the page has a next cursor. Focused
history instead emits one `[context]` record with `History displayed`,
`Complete turns`, `Context retained`, `Omitted turns`, `UTF-8 bytes`, `Turn
cap`, and `Truncated` in that order. Source whitespace and LF remain; controls
are visible, events are never joined, and content remains inside its one
callback record. An `agent_reply` content value receives the same strict
object/array presentation projection as a direct reply; all other event kinds
remain unchanged.

If Ctrl-C interrupts search, transcript, or history row delivery, all already
completed row callbacks remain visible. The controller writes exactly one
matching cancellation record and omits every unrendered row plus the trailing
result, hint, or context record. Attached state remains unchanged.

## Exact cancellation records

| Boundary | Exact records in order |
| --- | --- |
| Main prompt / session resume or pause / conversation focus | `[cancelled] No action was taken.` / `[cancelled] Session resume cancelled.` / `[cancelled] Session pause cancelled.` / `[cancelled] Conversation focus cancelled.` |
| Recoverable advisory | `[cancelled] {DISPLAY_NAME} turn was interrupted.`<br>`[hint] The story session remains attached; the affected provider thread may restart on the next message.` |
| Consensus before entry | `[cancelled] Consensus run was cancelled before start.` |
| Consensus with trusted durable proof | `[run {DEBATE_ID}] [cancelled] [warning]` then an indented `Run interrupted after durable state transition.` in that same callback record;<br>`[cancelled] Debate {DEBATE_ID} was marked interrupted.`<br>`[hint] It cannot resume. Use /run to create a new debate.`<br>`[session] "{SESSION_ID}" remains ready and attached.` |
| Partial local render | `[cancelled] Search cancelled; attached state is unchanged.` / `[cancelled] Transcript read cancelled; attached state is unchanged.` / `[cancelled] History cancelled; attached state is unchanged.` |

## Errors and other output

The controller classifies 36 codes; the terminal accepts no exceptions.
`write_error()` redacts secrets, caps summary/hint at 512/256 characters, and
writes one semantic `Panel` and one callback record. Omitting
`debug_log_path`, or passing `None`, preserves the established two-notice
record exactly:

```text
--- Interactive error [error] ---
  [error CODE] SUMMARY
  [hint] HINT
---------------------------------
```

A supplied `debug_log_path` must be nonblank, one-line, strict-UTF-8 display
text. It is sanitized and appended to the same panel as exactly one third
notice:

```text
  [debug log] PATH
```

This additive keyword does not resolve, open, validate as a filesystem path,
or retain the value. Direct callers may still omit it. After workspace
resolution, the installed interactive CLI and controller supply the exact text
`If available, workspace-relative: debug_logs/runtime.jsonl`, producing the
third `[debug log]` notice. Errors before workspace resolution retain the
two-notice shape and disclose no debug-log path.

It preserves the trusted code, summary, hint, order, limits, redaction, and
failure behavior while allowing responsive wrapping and fixed decorative ANSI.
See
[recovery mappings](../operations/recovery-and-limitations.md) and
[retrieval copy](conversation-retrieval.md). Composition retains exact LF;
discards expose only counts. Routed replies are never reparsed.
