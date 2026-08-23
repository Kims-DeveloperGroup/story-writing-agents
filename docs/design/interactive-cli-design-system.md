# Interactive CLI design system contract

> **Status:** #82 delivered the import-compatible package, closed semantic
> models, pure foundation controls, responsive plain/fixed-ANSI renderer,
> capability profiles, unified raw driver and CLI factory, line-editor
> compatibility facade, and initial module-index reconciliation. #83 now uses
> that foundation
> for orientation, status/inspection, central errors and recovery, and accepted
> management, advisory, retrieval, and consensus output. Completed prerequisite
> #73 supplies contextual composition previews through the unified driver.
> #84 now applies the same system to help, roster, lists, search, transcript,
> and focused history. #85 adds bounded process-local main-command history.
> #86 integrates line-oriented composition and confirmation with semantic input
> views and pure controls. #79 adds selectable main-command completion. #87
> delivers the generic package-internal picker foundation without installing a
> shell consumer. #94 adds validated persisted workspace Codex fallback model
> and reasoning defaults. #93 adds the interactive settings workflow, immutable
> lifecycle configuration snapshots, and guarded persistence. #119 adds
> phase-level debate visibility in consensus output via handoff/action metadata
> and dedicated agent-saying `Panel` values. #105 adds
> presentation-only strict JSON formatting for agent replies and bold
> terminal-default agent styling. #106 makes settings picker and prompt editors
> transient without changing non-settings input behavior. The
> #111 advisory indicator adds one narrow terminal-owned rolling glyph cycle for
> capable advisory turns; advisory turns no longer emit `[routing]` or
> `[waiting]` notices. The
> #115 settings lifecycle keeps capable committed `/settings` input cleanup
> display-only, adds transient selectable YES/NO confirmations, and shows
> changed Current/Pending fields with explicit cues or a no-changes notice.
> #123 frames every semantic `Panel` with responsive ASCII top-title and bottom
> splitters. #126 makes Enter apply a highlighted main-command suggestion
> before submission and aligns Enter and Tab completion spacing. #127 adds
> leading routed-agent suggestions to that capable main-prompt palette. The
> [interactive terminal reference](../reference/interactive-terminal.md) and
> [terminal safety rules](../operations/terminal-safety.md) describe current
> observable behavior. #162 adds one isolated package-internal supplied-text
> viewer with pure range state, bounded rendering, strict line fallback, and a
> sole-driver alternate-screen lifecycle. It has no installed-shell consumer.

This contract governs every human-facing interactive view and control in the
installed shell. It preserves scrollback and the established command/runtime
boundaries while giving output, one-line input, line-oriented composition,
confirmation, suggestions, fixed choices, command history, and pickers one
semantic model. It also governs the isolated package-internal ranged viewer
where explicitly identified; the installed controller invokes it through
provider-bound `$`/`$$` prompt editing without exporting it as a command.

The words **must**, **must not**, and **may** below constrain the delivery
issues. Requirements beyond the implemented #82, #83, #84, #85, #86, #79,
#87, #94, #93, #105, #106, #111, #115, #119, #123, #126, #127, #142, #162, #164,
and #73 slices do not describe current production behavior.

## Delivery status

| Slice | Current availability |
| --- | --- |
| #82 terminal foundation | Implemented: package boundary, private semantic values, current line-edit transitions and suggestion projection, responsive renderer, capability observation, sole raw driver, unified CLI construction, compatibility facades, and index reconciliation. |
| #83 output-view migration | Implemented: orientation/current context, interactive status and inspection, central errors and recovery, and accepted management, advisory, retrieval, and consensus output use semantic components with responsive plain/fixed-ANSI rendering. Facts, copy, order, callback granularity, and noninteractive contracts remain unchanged. |
| #84 information-dense view migration | Implemented: semantic full/context help, agent roster, session/conversation lists, search, transcript, and focused history preserve exact facts, copy, order, callback boundaries, cancellation, and empty behavior across responsive plain/fixed-ANSI rendering. |
| #85 input history | Implemented: each stream-backed terminal/editor driver owns one isolated, bounded 100-entry main-command store; capable main input uses Up/Down older/newer navigation with exact draft restoration while its selectable palette is closed. Non-main input stays history-free. |
| Prerequisite #73 | Implemented: RUN/CHAT composition uses the fixed preview-only `/send`, `/cancel` projection through explicit call-local routing. |
| #86 composer/confirmation integration | Implemented: RUN/CHAT composition and yes/no confirmation build ordered semantic input views, render equivalent plain/fixed-ANSI projections at the active width, and use pure history-free controls. Established results, signatures, `/send`, `/cancel`, `/say`, defaults, Ctrl-C, EOF, callback cardinality, and controller/runtime behavior remain unchanged. |
| #79 selectable completion | Implemented: capable main input projects canonical root, subcommand, and finite fixed-option candidates into a semantic palette. Up/Down moves its clamped highlight, Tab applies the highlighted candidate, Escape restores the first pre-completion snapshot when present and dismisses the exact state, and closed-palette Up/Down retains history behavior. Shift-Tab has no action. |
| #87 picker foundation | Implemented: package-internal immutable picker/control/viewport state, pure initialization/reconciliation/live/fallback transitions, responsive live/full-list rendering, a dedicated history-free sole-driver lifecycle, and an unlisted resource-free adapter. #93 supplies its first installed consumer. |
| #94 workspace Codex defaults | Implemented: the original slice added optional workspace fallback Codex model and reasoning values. The current closed seven-field system schema also contains Antigravity model/effort; new workspaces initialize both provider families to null, legacy files remain valid and byte-preserved, and each backend validates and snapshots effective invocation values at construction. |
| #93 settings UI | Implemented: global alias-free `/settings` edits isolated current/pending snapshots of workspace defaults and per-agent provider, model, and reasoning choices through shared picker and prompt controls. Save validates an exact opening baseline and persona catalog, writes only changed configuration documents in deterministic order, and attempts reverse exact-byte rollback after a write failure. The workflow adds no supported public save API. |
| #105 agent reply presentation | Implemented: direct, retrieval, transcript, and focused-history agent replies use a presentation-only strict JSON object/array projection with exact unchanged fallback cases and two-space Unicode-preserving output; stored/provider content remains unchanged, and the `AGENT` role uses bold terminal-default foreground styling without background detection. |
| #106 transient settings UI | Implemented: settings picker and prompt reads clear capable transient rows, suppress duplicate fallback finals, and keep nested Escape silent. #142 later makes this lifecycle universal for capable submitted input. |
| #111 advisory progress indicator | Implemented: one advisory and consensus waiting four-glyph rolling indicator runs only on capable raw streams, including `NO_COLOR`; callback, non-TTY, `TERM=dumb`, and uncertain-capability paths remain silent, and no general animation or `[routing]`/`[waiting]` record is introduced. |
| #115 settings lifecycle | Implemented: capable committed `/settings` input is cleared from the live display without deleting its history entry; Save and Cancel use transient selectable YES/NO confirmations; confirmation context shows only changed Current/Pending fields with `(Changed)` cues or only a no-changes notice; fallback paths preserve visible committed input and report no cleanup they cannot perform. |
| #119 debate visibility | Implemented: consensus progress now includes per-phase handoff/action records and a dedicated semantic `Panel` titled `Agent saying` showing newly emitted messages from the relevant agents. |
| #123 Panel splitters | Implemented: every semantic `Panel` renders a responsive ASCII top splitter carrying its title and state plus a bottom splitter, without side borders or changes to interaction/runtime behavior. |
| #126 Enter completion and spacing | Implemented: at capable main prompts, Enter applies a highlighted suggestion without submitting and dismisses the palette so a second Enter submits; Enter and Tab add one ASCII trailing space only when the replaced token ends at the buffer end. |
| #127 leading routed-agent suggestions | Implemented: the capable main prompt projects `@lead`, `@plot`, `@character`, and `@execution` for a cursor-active first leading token, using canonical preferred route aliases and the existing completion controls. |
| #142 live resume choices and transient input | Implemented: interactive session and conversation selection use only `/session resume [NAME]` and `/conversation resume [ID]`; exact omitted commands show controller-owned choices before submission on capable streams when a disposable read yields eligible rows. A submitted omitted command uses the generic picker—live on a capable stream and numbered in fallback—only after its fresh read succeeds with eligible rows; empty and failed reads report without a picker. Dynamic-choice Enter submits an explicit identity directly. Every capable input read clears its complete live frame before returning and attempts the same cleanup on failure, while persistent semantic results remain in scrollback. |
| #162 independent ranged text viewer | Implemented package-internally: exact caller-supplied text, logical-line count, and complete fallback projection are bounded before terminal access; pure immutable state selects normalized one-based inclusive ranges; capable streams use one bounded alternate-screen lifecycle; and ordinary-line fallback accepts exact ASCII `START-END` or `/cancel` without truncating accepted content. One per-driver input latch now selects `LIVE` or `FALLBACK` permanently and preserves exact-byte decoding across all input routes. Results contain only an exit kind and optional range. No shell command, public API, artifact read, reference construction, composer restoration, or agent integration is added. |
| #164 interactive artifact references | Implemented: the controller gates activation to attached/focused provider-bound spans, borrows fresh canonical-filesystem-path and verified-text callbacks, and restores exact origin state. The private activation leaf projects `$` completion, direct `$${path}` viewing, `$$` picker/viewer, ordered token replacement, and fixed content-free failures. The driver owns same-latch nested input and fallback second confirmation. Normal submission, parsing, runtime capture, and per-agent enforcement remain established downstream boundaries; no command, automation UI, public selection API, or export is added. |

## Goals and limits

The design must:

- represent content and interaction semantically before rendering;
- remain readable in structured plain text, including when color is disabled;
- use one raw-terminal owner and pure control transitions;
- keep preview and navigation free of runtime and storage effects;
- preserve public Python compatibility and noninteractive machine contracts;
  and
- use only the Python standard library.

The installed shell remains scrollback-friendly and line-oriented, not a
full-screen TUI. Mouse input, general-purpose animation, terminal queries, a
signal-driven background loop, user-selectable themes, persistent history,
fuzzy completion, and a full-screen composer are out of scope. #162's isolated
viewer is the sole alternate-screen exception; it is a call-local supplied-text
control, not a shell, composer, picker, settings, or general TUI mode. The
narrow animated advisory/consensus indicator remains the sole animation
exception: it is a terminal-owned, call-local visual aid for one capable
operation, not a general animation mechanism. Rendering outside the viewer
must not assume that earlier terminal rows can be revisited after an
interaction is committed.

## Closed semantic vocabulary

These are the only semantic view components. #82 implements their immutable,
package-private models and deterministic rendering. Delivery slices may compose
them into public views but must not introduce an additional component kind
without first revising this contract.

| Component | Contract |
| --- | --- |
| `Prompt` | Names the active input request, its scope, and any concise instruction or default. It contains no input-side effects. |
| `Panel` | Groups a bounded related view or control and renders ASCII top-title and bottom splitters without side borders, claiming the screen, or erasing committed scrollback. |
| `Section` | Divides a panel or long record into a named logical region. |
| `Field` | Associates one stable label with one value. The label remains visible when alignment degrades. |
| `Notice` | Communicates status, guidance, warning, success, cancellation, empty, loading, or error meaning independent of color. |
| `Message` | Presents attributed user, agent, or system content while preserving content boundaries. |
| `Record` | Presents one atomic domain result or event with its metadata and content. |
| `ListView` | Presents an ordered collection, count/position context, and an explicit empty state. Its items remain semantic records or fields, not preformatted terminal strings. |
| `SuggestionPalette` | Presents noncommitting candidates anchored to an editable input buffer. It owns open/highlight state, never parser or runtime work. |
| `Picker` | Presents a single-select choice list with separate highlight, selection, commit, cancel, disabled, empty, and error states. |

A `Panel` may contain `Section`, `Field`, `Notice`, `Message`, `Record`, or
`ListView` values. `SuggestionPalette` and `Picker` are controls built from the
same content primitives but retain their explicit interaction semantics.
Composer and confirmation views are compositions of `Prompt`, `Panel`,
`Section`, `Field`, and `Notice`; they are not new component kinds.
The ranged viewer uses its own private document, state, viewport, result, and
render contracts rather than adding another member to this closed semantic
`Component` union.
Splitter framing is selected only for the semantic `Panel` type. Standalone
`Prompt`, `Section`, `ListView`, `Record`, `SuggestionPalette`, and `Picker`
components retain their established unframed shapes; a component nested inside
a `Panel` appears between its splitters without gaining another frame.

Semantic models contain trusted roles and untrusted text, never ANSI, cursor
sequences, padding calculated for a specific width, callbacks, streams,
runtime services, or storage handles.

## State and focus model

States are explicit and may occupy different axes. A view must not infer state
from style, punctuation, or cursor position.

| State | Meaning and constraint |
| --- | --- |
| `idle` | Present but not accepting keys and not performing work. |
| `focused` | The sole control that receives decoded key events. At most one control is focused. |
| `open` | A transient palette, picker, or control panel is visible. Open does not imply commit. |
| `highlighted` | A navigable candidate is the current preview target. Highlighting has no external effect. |
| `selected` | A choice has been accepted by its control. Selection remains distinct from highlight and precedes the caller's runtime action. |
| `disabled` | Visible but unavailable for focus, replacement, selection, or commit. Text explains the unavailable choice when needed. |
| `loading` | Work has started and no terminal result is available yet. The view names the operation in text; the #111 advisory glyph is optional visual reinforcement, never the only status. |
| `empty` | A successful read produced no items or content. Empty is not an error. |
| `error` | The control or operation ended in a trusted, user-visible failure state. |
| `cancelled` | The interaction ended without commit. Established cancellation consequences remain authoritative. |
| `completed` | The interaction or operation committed and finished successfully. |

Focus belongs to a control, not to an item. A focused palette may highlight one
candidate; a focused picker may highlight one enabled item. Disabled items are
never commit targets. Losing focus closes transient overlays unless the
calling contract explicitly transfers focus to them. Rerendering, including a
width change, preserves buffer, cursor, focus, highlight, and selection by
stable semantic identity when that identity still exists.

`loading`, `empty`, `error`, `cancelled`, and `completed` are explicit
operation results. Color, symbols, or emphasis may reinforce them but their
text and structure must remain sufficient in plain output.

The viewer has a separate immutable, call-local state rather than extending
these shell view states. A nonempty document has one active line and an
optional fixed anchor; the normalized selection is the inclusive span between
anchor and active line. Ordinary Up/Down clears the anchor. Shift+Up/Down sets
the first active line as anchor and moves the active end, so extension,
contraction, reversal, and boundary clamping remain deterministic. Its terminal
results are exactly `selected`, `cancelled`, `interrupted`, and `eof`; an
operational failure is raised after cleanup, not represented as another result.

The controller's outer cleanup boundary is separate from these handled command
states. It attempts Runner close, known-access release, backend close, and state
clearing in order even after a stage fails. An uncaught body process control
wins; otherwise the first cleanup process control propagates, unchanged with its
traceback, after later stages finish. Runner state remains only when its close
was interrupted by process control; attachment, focus, and memory always clear.
With no retained process control, ordinary cleanup failure returns `2` and
attempts one nonduplicated `cleanup_failed`. A diagnostic process control cannot
replace an already retained body or cleanup winner. A Ctrl-C or cancellation
already handled by a command remains a normal `cancelled` or recovery result
rather than entering this arbitration.

## Input ownership and transition boundary

Exactly one terminal driver owns raw streams, capability and width detection,
raw-mode activation, key decoding, cursor control, writes, flushing, and
restoration. No editor, renderer, model, control transition, controller, or
compatibility facade may independently acquire or restore the terminal.

#82 implements that ownership boundary. Its pure line-input transition handles
the established insertion, deletion, cursor, Enter, Ctrl-C, and EOF behavior.
#85 adds the pure main-input/history transition and one bounded store to each
stream-backed driver. #86 adds pure composer-line and confirmation-answer
transitions plus a history-free semantic input route. #79 composes canonical
grammar projection with the main transition and semantic palette projection.
#126 makes highlighted Enter use that completion projection before submission
and gives Enter and Tab one shared end-of-buffer spacing rule.
#127 extends the same pure projection and palette route to the first leading
routed-message token without changing parsing or non-main input.
#142 lets the controller supply disposable session or conversation choices only
for an exact omitted resume command. The terminal projects those choices through
the existing main control without moving catalog, lifecycle, default, focus, or
authorization work into the driver.
#87 adds a separate generic picker state, transition, bounded viewport, and
dedicated history-free driver route; it does not reuse editable-line or main
completion state. #93 consumes that route for `/settings` and adds one
history-free cancellable prompt route through the same sole driver.
#111 adds one unlisted advisory-progress adapter to that same driver. It wraps
exactly one advisory awaitable, draws only on a capable raw stream, clears and
redraws around persistent output, and joins both the operation and its
call-local indicator task before returning. It does not add a semantic view,
callback record, public API, or general animation facility.
#162 adds a separate supplied-text viewer route. Its private model, controls,
and renderer are pure. A dedicated input-lifecycle leaf owns the driver's
irreversible live/fallback selection and persistent exact-byte decoder; the
existing driver owns Shift+Arrow decoding, geometry, alternate screen,
streams, raw mode, and cleanup. The viewer never opens a file or passes viewer
state through the parser or controller.
Up/Down navigate history only on capable main reads with a closed palette; an
open palette consumes them for highlight navigation. Up/Down, Tab, Shift-Tab,
and Escape remain no-ops for composition and confirmation. Shift-Tab remains a
main no-op.

Controls receive normalized key events such as text, Enter, Backspace, Delete,
Left, Right, Home, End, Up, Down, Shift+Up, Shift+Down, Tab, Shift-Tab, Escape,
Ctrl-C, and EOF. Their transitions are pure:

```text
transition(previous_state, key_event, semantic_options)
    -> (next_state, optional_control_outcome)
```

A transition may edit a call-local/process-local UI value or return a commit,
cancel, interrupt, or EOF outcome. It must not read or write a stream, parse a
command, call the runtime, invoke a provider, open a file, query or mutate
storage, start a subprocess, or use the network. Rendering is likewise a pure
projection from semantic state and a capability/width profile.

Only a committed outcome crosses from the control boundary to its existing
caller. Main input passes the exact committed text to the established
interactive parser once and only once. Recalled history, completion
replacement, highlighting, preview, validation display, and rerendering never
invoke that parser. The driver completes capable display cleanup and terminal
restoration before it appends an eligible main-history entry or returns the
input; no controller-owned display token or later command-specific erase exists.

For established output primitives, one semantic `Record`, `Message`, or
`Notice` produces one callback write including its final newline. A delivery
slice may make a narrower exception only for an explicitly documented and
tested live-control redraw/restoration sequence. It must not split one domain
record into multiple caller-visible callback records.

### Permanent input-mode lifecycle

One resource-free input latch belongs to each stream-backed driver. Before any
input is consumed it is `UNDECIDED`. The first actual read irreversibly selects
`LIVE` after exact source/decoder/control/configuration preflight, or
`FALLBACK` immediately before the ordinary callback. The selected mode applies
to main, subject, composition, confirmation, prompt, picker, and viewer reads.
It cannot upgrade or downgrade later.

`LIVE` retains one source-correlated incremental decoder for the driver's
lifetime. It admits registry-canonical ASCII, UTF-8, or ISO-8859-1 only with
the built-in `strict` or `surrogateescape` handler, reads exactly one byte at a
time, preserves fragmented code points, and exposes a maximum four-code-point
pending suffix before the fixed 50 ms Escape readiness wait. Clean EOF is
stable. Partial/malformed EOF and read/decode/result-shape failures poison later
reads; synchronous reentry is rejected and also poisons the reader. Exact
source/decoder drift rejects that operation. `KeyboardInterrupt` alone clears
the incomplete decoder and suffix without changing `LIVE`. Mixing another
wrapper/raw reader or concurrent external reads against the same source is
outside the supported boundary because it can strand decoded bytes in a
different buffer.

The latch does not own terminal resources, keys, readiness, raw mode, screen,
or restoration. Those remain in the driver and are acquired per capable
interaction. The permanent ownership prevents a `TextIOWrapper` decoded queue
from hiding bytes when a later component reads its raw buffer.

### Submitted-input lifecycle

This is the normative lifecycle for interactive input. A **complete live
frame** is every row and terminal cell drawn for one capable read: semantic
context and prompt, the editable buffer, suggestions or choices, validation,
and every redraw belonging to that read. It is transient display state, not a
domain event or durable record.

- The raw driver owns the complete frame. Before any capable input read returns
  or propagates a failure, it attempts to clear the whole current frame and
  restore every terminal attribute it acquired. This applies to main,
  subject, composition, confirmation, picker, and cancellable-prompt submit/commit,
  cancel, interrupt, EOF, and failure outcomes. Restoration uncertainty remains
  fatal; a clear failure is handled through the established terminal-error path.
- Enter on a highlighted static command suggestion is **completion only**. It
  replaces the active token, applies the spacing rule, dismisses the palette,
  and keeps the live read open; therefore no submitted-frame cleanup or history
  append occurs until a later submission. By contrast, Enter on a dynamic exact
  omitted-resume choice commits one complete explicit resume line in that same
  key event.
- Cleanup changes only what remains visible from the input control. A successful
  nonblank main submission is still appended once to driver-local history after
  cleanup and restoration, and the parser still receives it once. Controller
  validation, cancellation, discard, error, orientation, and result components
  are persistent semantic output written after the input boundary and remain in
  scrollback.
- Plain callback and ordinary-line fallback cannot erase already printed
  prompts or echoed input, so those bytes remain visible. They emit no synthetic
  final `completed` or `cancelled` control frame. Pure controls may still use
  those internal terminal states to describe their outcome; the state is not an
  extra user-visible record.
- Conversation-subject input remains history- and completion-free. After the
  driver selects `LIVE`, it uses a cleared capable frame; after the driver
  selects `FALLBACK`, it uses the ordinary callback. Its submitted value and
  controller semantics do not change.

The input component never mutates session lifecycle, defaults, attachment,
conversation focus, agent focus, provider state, or storage. Those effects begin
only after parser/controller validation of a submitted command.

### Ranged-viewer lifecycle

The package-internal viewer has a distinct lifecycle because its capable form
uses the alternate screen rather than the submitted-input live frame:

```text
validate supplied values
  -> resolve UNDECIDED to irreversible LIVE or FALLBACK if needed
  -> LIVE: validate retained source/decoder/configuration
  -> activate raw mode
  -> enter alternate screen
  -> navigate/select or receive terminal event
  -> exit alternate screen once
  -> restore termios once
  -> dispose call-local state

  -> FALLBACK: print complete numbered text
  -> accept exact START-END or /cancel
  -> dispose call-local state
```

Exact types, strict UTF-8, the private 8 MiB text capacity, at most 65,536
LF-derived logical lines, a complete sanitized fallback projection of at most
16 MiB UTF-8, and the optional one-based initial line are validated before
terminal inspection or I/O. Line-count validation precedes split
materialization; fallback accounting includes sanitized display metadata,
numbered records, separators, notices, and prompt. Exact limits are accepted,
while an over-limit value—including escape-expansion-dense text—never opens the
viewer. Only an `UNDECIDED` driver's first actual read may choose the exact line
fallback. Once either mode is selected it cannot switch: later live source,
decoder, capability, or configuration drift is an error rather than a fallback
upgrade or downgrade. Normal selection, Escape, Ctrl-C, and EOF return typed
content-free results after cleanup. Operational failures propagate after
cleanup, with termios-restoration failure taking precedence over screen-exit
failure and screen-exit failure taking precedence over the active failure.
Cursor visibility is never changed.

Every document, display name, anchor, active line, viewport, and correlation
value is immutable and call-local. Disposal is a lifecycle boundary, not secure
memory erasure. Reopening receives a fresh caller-supplied document and optional
initial line; the viewer owns no resumable state or composer snapshot. Selection
and cancellation both close the alternate-screen viewer after cleanup. The #164
controller caller restores the exact originating buffer/cursor and attaches the
selected path/range indicator; the #162 viewer itself still owns neither.

## Key and commit semantics

The following table is the complete interaction contract. The existing
one-line editing, semantic composition and confirmation controls, #85
main-command history, #79 selectable palette, and package-internal #87 picker
foundation are implemented. #93 invokes the picker and cancellable prompt
through package-internal seams. #162 adds the separate viewer row without
changing the established controls.

| Focused control | Navigation and editing | Commit | Cancel or terminal event |
| --- | --- | --- | --- |
| Main one-line input | Text, Backspace/Delete, Left/Right, and Home/End edit. Up/Down navigate history only while no palette is open. Slash-prefixed input or a leading routed-message token may open a palette; Tab applies a highlighted static completion. | Enter submits the exact visible buffer once when no highlighted suggestion is open. Configured VEOF with a nonempty buffer submits the exact visible buffer unconditionally. | Ctrl-C, configured VEOF on an empty buffer, and physical EOF retain their established typed outcomes; no text is parsed. Every returned capable outcome follows the submitted-input cleanup contract. |
| `SuggestionPalette` anchored to main input | Opening highlights the first eligible candidate without editing. Up/Down moves the highlight among eligible candidates without replacement; Tab applies its highlighted completion. Shift-Tab has no palette action. Exact omitted resume commands may instead project controller-owned dynamic choices. | Enter with a static highlight applies completion without submitting, closes and latches the palette dismissed, and leaves a later Enter to submit. Enter with a dynamic resume choice commits its full explicit command immediately. | Escape closes the palette and restores the pre-Tab buffer and cursor snapshot when one exists. Dismissing a dynamic choice overlay leaves the exact buffer unchanged. |
| `Picker` | Up/Down changes highlight among enabled items. Highlight never selects or commits. | Enter selects and commits the highlighted enabled item. | Escape cancels. Ctrl-C and EOF leave without commit and retain the invoking API's typed semantics. |
| Settings `Prompt` | Text and ordinary line-edit keys edit one history-free value. Completion keys have no effect. | Enter commits the exact buffer for field validation. | Escape returns to the parent without changing the pending value; Ctrl-C and EOF cancel the whole settings workflow. |
| Ranged text viewer | Up/Down clears the anchor and moves/clamps the active line. Shift+Up/Down fixes the first anchor and moves the active end through extension, contraction, reversal, and clamping. Other keys are no-ops. Configured VINTR, then VEOF, take priority over ordinary key roles. | Enter returns the active singleton or normalized anchored one-based inclusive range; empty text cannot commit. | Escape returns `cancelled`; configured VINTR returns `interrupted`; configured VEOF or stream EOF returns `eof`. Operational failures are raised after cleanup. |
| Line-oriented composer | Each ordinary line appends exact content. Up/Down, Tab, Shift-Tab, and Escape are no-ops; no full-screen editing or command-history traversal is introduced. | `/send` commits the exact LF-joined content when nonblank. | `/cancel`, Ctrl-C, and EOF preserve established discard outcomes and counts. |
| Confirmation | Existing accepted yes/no input and blank-default behavior remain authoritative. Up/Down, Tab, Shift-Tab, and Escape are no-ops. Invalid input marks the choices as erroneous, writes one hint, and asks again. | A valid answer or blank default returns the established `yes` or `no` result. | Ctrl-C and EOF return the established `interrupt` or `eof` result. |

`/say /send` and `/say /cancel` in composition continue to append literal
`/send` and `/cancel` lines. The composer remains line-oriented; preview,
highlight, and history behavior supplied by contextual suggestions must not
change `/send`, `/cancel`, `/say`, Ctrl-C, or EOF semantics.

### Main-command history

> **Implemented by #85:** history is available on capable stream-backed main
> reads. The ordinary-line fallback records eligible commits but does not
> provide arrow navigation. Once that driver selects permanent `FALLBACK`, its
> retained entries cannot later be recalled through a capable read on the same
> input UI.

Each unified stream terminal or standalone compatibility editor has exactly one
isolated driver-owned history store for that driver instance's lifetime. The
store is process-local and is not written to a workspace, transcript,
configuration file, environment variable, provider session, or user history
file.

- It contains at most 100 exact nonblank committed main-prompt submissions,
  preserving leading/trailing whitespace. Invalid command text, duplicate
  submissions, `/quit`, ordinary-line fallback commits, and nonempty configured
  VEOF commits are eligible because recording occurs at the completed main-read
  boundary before the parser runs.
- Empty or whitespace-only buffers, Ctrl-C, empty configured VEOF, physical
  stream EOF, incomplete buffers, operations that fail without returning an
  accepted line, and invalid fallback framing are not entries.
  Composition/contextual input, conversation subject, and confirmation routes
  neither navigate nor mutate main history.
- Appending the 101st entry removes only the oldest entry. Duplicate
  submissions remain separate entries.
- Up recalls older entries and Down recalls newer entries. The buffer and cursor
  present when navigation begins are the saved draft. Down past the newest
  history entry restores that exact draft and cursor and exits navigation.
- A recalled value is copied into the editable buffer with its cursor at the
  end. Edits never mutate the stored entry. A later navigation key may replace
  the edited copy; Down past newest still restores the original pre-navigation
  draft.
- Committing an edited recalled value submits that visible value to the existing
  parser once and, if nonblank, appends it as a new exact history entry.
- A projected main palette is open even when filtering leaves no candidates.
  Up/Down are then palette keys rather than history keys. When grammar exposes
  no palette, Up/Down resume history traversal. Tab applies only a highlighted
  candidate, Shift-Tab remains a no-op, and Escape closes the palette as
  specified below.

### Composer and confirmation controls

> **Implemented by #86:** composition and confirmation now use ordered semantic
> input views without changing their public APIs or established outcomes.

Each RUN or CHAT composition attempt renders `Mode`, optional `Target`, `Next
line`, `Lines`, `Characters`, guidance, then the final focused numbered prompt.
The exact accumulated lines live only in immutable call-local composer state;
they are LF-joined for `/send` and discarded for `/cancel`, Ctrl-C, or EOF.
Composition retains the fixed ordered `/send`, `/cancel` preview, but the
preview is not selectable and `/say` remains ordinary composer input.

Each confirmation attempt renders `Question`, `Choices`, `Default`, then the
final focused prompt. It has no suggestion provider. Invalid input writes one
semantic error hint, marks `Choices` as erroneous on the retry view, and reads
again. A valid answer, blank default, Ctrl-C, or EOF retains its established
typed result and adds no final confirmation event.

Both controls are history-free. Their semantic input renderer produces one
plain projection and an optional fixed-theme projection with equal content,
width-aware wrapping, a final prompt space, and no final LF. Callback and safe
line-fallback reads receive the plain projection once; capable live reads use
the trusted fixed-theme projection for redraw while keeping dynamic input
sanitized. Each read remains call-local, and suggestions, rendering, and retry
state perform no parser, runtime, provider, filesystem, or storage action.

Composer lifecycle output is now four semantic `Notice` events: `open` on
entry, `error` for an empty `/send`, `completed` on send, and `cancelled` on
discard. Each event and each invalid-confirmation hint remains one render and
one callback write. The public generic `write_status` method remains for
compatibility, but no internal composer call site uses it.

### Agent-reply presentation

Agent content is formatted only at the presentation boundary. Direct replies,
retrieval answers, transcript pages, and focused history pass `agent_reply`
content through one pure formatter before constructing their semantic message or
record; provider results, retrieval observations, search data, and stored
transcript bytes remain unchanged. The formatter accepts only a complete strict
JSON object or array, rejects duplicate object keys and non-finite constants,
then emits two-space-indented JSON with source key order and Unicode preserved
without ASCII escaping. Malformed JSON, embedded prose or extra data, scalar
JSON values, duplicate keys, non-finite values, and serialization failures are
returned as the exact original string. The `AGENT` role is bold with
the terminal-default foreground and never depends on background detection;
ANSI output adds only a local reset, while plain and `NO_COLOR` output retain
the same content.

### Main-command palette and fixed choices

> **Implemented by #79 and #127:** capable main reads provide deterministic
> selectable root, subcommand, finite fixed-option, and leading routed-agent
> completion without changing parser, runtime, history, fallback, or public API
> contracts.

A leading slash automatically opens `SuggestionPalette` at a recognized
grammar position. Root candidates are the 21 main-available command tokens in
registry order. Help offers those 21 plus `/compose`, `/send`, and `/cancel`;
agent-valued slash positions expose the four preferred prompt aliases `lead`,
`plot`, `character`, and `execution` in route order. Other implemented finite
positions expose their static subcommands or options. Runtime values such as
session, conversation, debate, query, prompt, subject, and file-path text are
never candidates. Fixed-only positions retain an open empty palette when a
prefix has no match; mixed fixed/free positions close the palette for a
free-value slot.

A first token beginning with `@` projects the full candidates `@lead`, `@plot`,
`@character`, and `@execution`. Their order and preferred values derive from
each `AGENT_ROUTES` entry's `prompt_alias`; all other existing route aliases
remain valid parser input but are not suggested. Matching is exact and
case-sensitive. Projection is available only while that first leading token is
cursor-active. A non-leading `@`, the routed-message body, and `@` text in slash
message input project no routed-agent palette.

The only dynamic main choices are available when the buffer and cursor are
exactly at the end of `/session resume` or, with a session attached,
`/conversation resume`. The controller reads a disposable snapshot and supplies
ordered display rows while retaining every lifecycle, catalog, default, focus,
and authorization decision. Sessions are ordered by newest durable update then
ID and show durable/access state; conversations retain catalog order, label each
active row by subject, and show its stable handle. Highlight precedence is
current attachment/focus, then an eligible saved default, then the first row.
Snapshot-read failure suppresses the live rows rather than changing submission.
A later post-submit path performs its own read and may still succeed with
eligible rows and open the picker; an empty or failed read reports without one.

Candidate and active-token matching are exact and case-sensitive. ASCII space
and tab alone delimit the cursor-aware replacement span. Completion replaces
the full active token, including its suffix when the cursor is in the middle,
then adds exactly one ASCII trailing space only when that token ends at the
buffer end. An existing space/tab delimiter and all text after the token remain
exact. Help accepts a bare active token for slash-prefixed replacement, so
`/help ru` may complete to `/help /run`. Filtering, opening, empty results,
highlight, replacement, and dismissal are deterministic pure transitions.

Opening the palette highlights the first eligible candidate deterministically
without changing the editable buffer. While the palette is open, Up and Down
move the highlight among eligible candidates and clamp at the first and last
candidate; they do not replace text or traverse history. Tab applies the
highlighted candidate by replacing the applicable buffer span and applying the
shared spacing rule. Shift-Tab does not navigate or apply the palette. Tab is
completion only, not parser or runtime commit, and is a no-op for an empty
palette, a palette without a highlight, or when replacement and spacing leave
the line unchanged. The first changing Tab retains the full pre-completion
buffer and cursor snapshot; edits within the same grammar position keep that
snapshot, while a grammar position change resets it. Escape restores the
retained snapshot when one exists, closes the palette, and latches that exact
line state so it stays dismissed until an edit or history change. Without a
snapshot it leaves the current buffer unchanged.

Enter with a highlighted static candidate applies the same replacement and
spacing as Tab without parser or runtime commit, closes the palette, and latches
the completed line dismissed. The next Enter therefore submits that exact
visible buffer. A highlighted dynamic resume choice is different: Tab is a
no-op and Enter returns its complete explicit canonical command as the one
submission. Escape dismisses the dynamic rows without changing the omitted
command buffer. When no highlighted candidate is open, Enter submits the exact
visible buffer through the existing parser once. Configured VEOF with a
nonempty buffer submits that exact visible buffer unconditionally, including
while a candidate is highlighted. When the palette is closed, Up/Down resume
older/newer history traversal. Recalled text remains editable and may reopen a
palette based on its current buffer.

After capable cleanup and restoration, one committed dynamic choice appends the
explicit command—not the omitted buffer—to main history, bypasses the picker,
and follows the normal parser/controller route. The controller rereads and
validates the chosen identity before changing lifecycle, defaults, attachment,
or focus. If the omitted buffer itself is submitted—after Escape, through
nonempty configured VEOF, or without committing a live choice—the main history
entry is the omitted command and the controller performs a fresh choice read.
A successful read with eligible rows invokes the generic history-free picker;
its capable form is live and transient, while ordinary-line fallback renders
the complete numbered form. An empty or failed read reports without a picker.
When one opens, its answer is never a second main-history entry.

The routed-agent palette uses these same completion controls and spacing rules.
Enter or Tab completes a highlight; Enter completion does not submit, so the
user enters the routed-message body and then presses Enter to submit. A route
without a nonblank message remains a parser error. Ordinary-line fallback,
settings, composition, confirmation, subjects, and other non-main prompts
receive no routed-agent palette.

Fixed command arguments are palette candidates only when the existing grammar
exposes a finite canonical set. They do not add parser rules, authorization,
runtime actions, or fuzzy matching. Confirmation choices remain owned by the
confirmation control rather than the main-command palette. Finite choices
include the preferred agent prompt aliases for agent-target positions when
exposed by canonical command-route metadata; all ten declared route aliases
remain parser-compatible, and the palette does not duplicate those aliases or
otherwise duplicate parser grammar.

### Picker semantics

> **Implemented by #87 as a package-internal foundation and consumed by #93
> and #142:** picker state, controls, rendering, driver input, callback
> adaptation, and terminal outcomes remain internal. `/settings` uses the
> dedicated picker. A submitted omitted resume command uses it only when a
> fresh lifecycle/catalog read succeeds with eligible rows; empty and failed
> reads report without opening it. Capable streams render an opened picker live
> and transient, while ordinary-line fallback uses its numbered form. A
> pre-Enter dynamic-choice commit is already explicit and bypasses this picker.
> No supported public picker API is added.

A `Picker` is single-select. Initialization uses an explicit enabled highlight,
then an enabled selected value, then the first enabled choice. Up/Down moves the
highlight among enabled choices, skips disabled rows, and clamps at the first or
last enabled choice without changing selection or calling a consumer. Enter
commits only an enabled highlighted choice. Escape cancels while preserving any
prior selection. Ctrl-C/VINTR and VEOF/physical EOF return typed `interrupt`
and `eof` outcomes without selection or cancellation. Other live keys are
no-ops. Empty and all-disabled pickers expose explicit `empty` or `disabled`
state and cannot commit.

Selection and highlight are independent until commit, so one visible choice may
be `[selected]` while another is `[highlighted]`; a committed choice carries
both cues. Choice refresh preserves still-enabled selection and highlight by
opaque identity, otherwise chooses an enabled item at or after the former
ordinal and then before it. Missing or disabled selection clears. Opaque
identities are correlation keys only and never render.

Live rendering shows at most seven absolute-numbered choices in a contiguous
window that minimally follows the highlight. It reports highlight/total and
window range, retains disabled reasons and descriptions, and emits a numbered
label-only `Selected outside view` summary when selection falls outside the
window. Every redraw observes current width. Plain, fixed ANSI, `NO_COLOR`, and
narrow output retain the same facts and simultaneous state cues without an
alternate screen or scrollback erasure.

Plain fallback renders the complete numbered choice list, guidance, and a final
focused prompt with trailing space and no LF. Exact `/cancel` cancels; blank
commits the current highlight; an ASCII decimal ordinal without a leading zero
commits that enabled choice. Disabled, zero, leading-zero, signed, spaced,
non-ASCII, named, out-of-range, and other answers are rejected with exactly one
`[validation] Enter an enabled choice number, press Enter for the highlighted
choice, or enter /cancel.` notice before retry. Callback, non-TTY, non-POSIX,
`TERM=dumb`, and safely recoverable activation paths use this same grammar.

Pure picker state records `completed` or `cancelled` internally, with the chosen
item both selected and highlighted on commit. Supported terminal adapters do not
render that state as a final control frame. Capable commit, Escape, interrupt,
and EOF clear the complete live frame before return; active failures attempt the
same cleanup. Numbered fallback leaves its printed prompt/input visible and adds
no synthetic final frame. The sole driver restores captured attributes after normal and
exceptional live outcomes. Picker reads never consult or mutate main history or
completion state, parse a command, or call runtime, provider, filesystem,
persistence, subprocess, storage, or network code.

### Persisted provider defaults

> **Implemented:** `config/system_defaults.json` has a closed seven-field
> schema: required `language` and `persona`, optional `max_debate_rounds`, and
> optional `codex_model`, `codex_reasoning_effort`, `antigravity_model`, and
> `antigravity_effort`. New workspaces include null values for both provider
> families; existing valid files with omitted optional fields remain valid and
> initialization preserves their exact bytes.

A non-null explicit `AgentBackend(model=...)` or CLI `--codex-model` wins over
persisted Codex model values. Explicit `AgentBackend(antigravity_model=...,
antigravity_effort=...)` or the matching CLI flags win for Antigravity.
Otherwise each agent's provider-specific values in `config/agent_configs.json`
inherit the corresponding workspace system value and then the provider
default. Model text is preserved without trimming or catalog lookup and must be
nonempty, not start with `-`, and contain no Unicode control, format, or
surrogate character. Codex reasoning is null or exactly `minimal`, `low`,
`medium`, `high`, or `xhigh`; Antigravity effort is null or exactly `low`,
`medium`, or `high`. The complete system-default and agent-configuration pair
is validated and snapshotted at backend construction; later saves apply only
to the next backend.

Every backend validates the complete persisted schema before applying explicit
provider settings and snapshots effective values at construction. Provider
arguments do not alter agent definitions, fingerprints, bindings,
provider-thread identity, lifecycle artifacts, or request payloads. Editing a
configuration file affects only the next backend construction. #94 itself
added no save API, `/settings` route, or active-backend refresh; #93 adds the
internal interactive workflow while preserving those public and lifecycle
boundaries.

### Settings panel

> **Implemented by #93:** global alias-free `/settings` provides this
> interaction and guarded persistence workflow without adding a supported
> settings/save object API. #87 supplies the generic internal picker foundation,
> and the configuration layer supplies validated workspace fallback
> model/effort fields for Codex and Antigravity.

The settings view edits two persistent documents through validated Current and
Pending configuration snapshots: the `config/system_defaults.json` defaults
described by
[Agent configuration](../reference/configuration/agent-configuration.md), and the existing
per-agent provider, model, and reasoning selections in
`config/agent_configs.json`. The system snapshot contains required `language`
and `persona`, optional `max_debate_rounds`, and the optional workspace Codex
and Antigravity fallback model/effort values. Provider, active-provider model,
and active-provider effort selection is per agent. A successful save writes
the canonical seven-field system object and canonical seven-field entries for
each agent. The view preserves the inactive provider's settings and does not
change identity, thread
ownership, configuration precedence, or either implemented schema.

The presentation uses one `Panel` with `Section`, `Field`, and `Notice` values,
plus one focused `Picker` or `Prompt`. It is not a new component or a
general-purpose form abstraction. One section shows validated current values;
another shows independent pending values. Controls render without the snapshot
panel. Save and Cancel open transient selectable confirmations with highlighted
YES and NO choices; their context shows only changed fields in both Current and
Pending, with each displayed value marked `(Changed)`. When nothing differs,
only a `No pending settings changes.` notice is shown, without empty sections.
The controller keeps snapshots
call-local and does not emit the diff while opening, navigating, or editing;
top-level cancellation does not claim a panel that was not rendered.
Nested Escape returns silently to its parent without a snapshot or cancellation
record. Settings follows the universal submitted-input lifecycle: capable
pickers, prompts, and confirmations clear their complete live frames before
return, while fallback readers retain printed input and add no synthetic final
control frame. Any implementation need for a new semantic
component must first revise the closed vocabulary through the repository's
modularity process.

The top-level settings order is exact:

```text
Language
Persona
Maximum debate rounds
Providers
Save
Cancel
```

| Setting | Pending-value contract |
| --- | --- |
| Language | The picker candidates are exactly `KO`, `EN`, `JA`, `ZH`, `ES`, `FR`, `DE`, `PT`, `IT`, `RU`, in that order. A schema-valid existing value outside this UI catalog remains visible and may be preserved, but is not synthesized as a candidate. |
| Persona | Candidates come only from the existing safe persona-template catalog. Duplicate identifiers resolve with the top-level template taking precedence over `alternative_templates`; the resulting identifiers are ordered lexically. The picker does not read, preview, create, or edit template content. |
| Maximum debate rounds | A numeric `Prompt` accepts a pending nonnegative integer under the existing validation rule. Blank, negative, fractional, and nonnumeric values produce an inline error `Notice` and retain focus without changing the pending value. `0` remains valid and means audit-only consensus. |
| Providers | A nested agent picker follows canonical `ROSTER` order, then a Provider/Model/Reasoning effort picker edits only that agent's pending `agent_configs` values. Codex and Antigravity are selectable; Codex is the default. Changing the route preserves the inactive provider's model/effort settings. `gemini` is not a selectable or valid backend. The UI neither invents providers nor changes roster identity. |
| Agent Model | A nested free-text `Prompt` uses the active provider's implemented safe-model validator; blank stores null and inherits that provider's workspace system model. It performs no network lookup and does not offer a model catalog. |
| Agent Reasoning effort | A nested picker offers Inherit plus `minimal`, `low`, `medium`, `high`, and `xhigh` for Codex, or Inherit plus `low`, `medium`, and `high` for Antigravity. Inherit uses the active provider's workspace system value, then the provider default. The UI does not add a Codex reasoning CLI flag. |

Opening the panel reads and validates both current snapshots. Failure to read or
validate either snapshot, or to enumerate the safe persona catalog, reports
`error` and performs no write, without exposing raw paths or exception text. On
success, the view copies both current snapshots into pending UI state and still
performs no write. The first focus is the top-level settings `Picker`; exactly
one control is focused:

- Up/Down moves only the current picker's highlight; highlighting does not edit
  pending values or write configuration.
- Enter on Language, Persona, or Providers transfers focus to the corresponding
  nested picker. Provider editing transfers first to the canonical agent
  picker, then to that agent's Provider/Model/Reasoning effort picker, and
  finally to the selected editor. Enter updates only the applicable pending
  value and returns focus to its parent;
  Escape restores that editor's prior pending value and returns focus without
  a write.
- Enter on Maximum debate rounds transfers focus to its `Prompt`. Valid Enter
  updates only the pending value and returns focus;
  invalid Enter leaves the prompt focused with a trusted validation notice.
  Escape restores its prior pending value.
- Enter on Save opens a transient `Save settings?` picker with highlighted YES
  and NO choices plus the changed-only Current/Pending context. YES proceeds
  to validate both complete pending snapshots and re-read both persisted
  documents and the persona catalog before any write. NO or nested Escape
  returns to the top-level picker. Any exact-byte or catalog change from the validated
  opening baseline fails closed with no write. A valid, unchanged baseline
  crosses the sole mutation boundary: each changed file is replaced atomically
  in system-then-agent order, and any write failure triggers best-effort
  reverse-order rollback to the exact original bytes of every attempted target,
  including the target that reported failure. Success reports `completed`.
  Validation, conflict, replacement, or rollback failure reports `error` and
  retains the pending values for retry or cancellation. This is a guarded
  two-file update, not a global transaction or a power-loss-safe transaction
  across both files.
- Enter on Cancel opens a transient `Cancel settings?` picker with highlighted
  YES and NO choices plus the same separately rendered changed-only or
  no-changes context `Panel`. Capable raw input initially highlights YES; Enter
  commits the highlighted choice, while Escape returns to the top-level picker.
  Line fallback accepts blank to commit the highlighted choice (initially YES),
  decimal ordinal `1` for YES or `2` for NO, and `/cancel` to return; free-form
  `yes`/`y` and `no`/`n` are rejected. Committed YES returns `cancelled`
  without writing; committed NO returns to the top-level picker. Top-level
  Escape, Ctrl-C, or EOF cancels without writing. Opening, navigating,
  highlighting, editing, validation preview, nested cancellation, and
  rerendering likewise have no filesystem, runtime, provider, storage, or
  configuration effect.

Saved values apply only to the next backend construction. The active backend
and runtime retain their complete construction-time persisted configuration
snapshot; even later persisted corruption does not reconfigure that lifecycle,
while the next construction reads and validates the then-persisted pair.
Session and explicit overrides remain live and higher precedence. Save does not
mutate an existing provider thread or current identity. The view manages no
credentials, provider network access, model catalog, provider implementation,
persona file editing, theme, key binding,
or automatic save.

The same current/pending labels, choice identity, validation meaning, disabled
provider meaning, and Save/Cancel outcomes must remain visible in wide, narrow,
fixed-ANSI, `NO_COLOR`, and structured plain output. Narrow rendering stacks
fields and windows long picker choices without dropping highlighted identity or
position cues. `TERM=dumb`, non-TTY, non-POSIX, or uncertain component
capability uses ordinary line input and structured plain records without raw
mode or cursor controls. This fallback does not bypass the installed CLI's
earlier two-stream TTY guard.

All dynamic identifiers and error text are sanitized before measurement or
styling. Color never carries current/pending differences, disabled-provider or
validation meaning, error, cancelled, or completed state. The settings view
preserves scrollback and adds no alternate screen, mouse handling, animation,
background loop, dependency, persistent draft, arbitrary form framework, or
automatic save.

## Rendering and capability policy

#82 implements three capability profiles and package-private semantic output and
input renderers. #83 and #84 route all migrated output view groups through that
foundation, #86 routes composition, confirmation, and their lifecycle records
through it, and #79 renders the main `SuggestionPalette` through the existing
component renderer. #87 adds bounded live and complete-list fallback projections
for the existing `Picker`; #142 reuses the main palette shape for controller-
owned resume rows and standardizes complete-frame cleanup. Capable real CLI
streams may therefore show their structured fixed-ANSI form, while `NO_COLOR`
retains the same semantic text. Plain line fallback owns no main palette or
completion keys. A submitted omitted resume uses the generic picker only after
a fresh read succeeds with eligible rows: its capable form is live and
transient, while plain line fallback uses the complete numbered contract. Empty
and failed reads report before picker input. Neither the controller nor the
terminal composer has an internal generic `write_status` call site.

#162's renderer is a separate private projection rather than a semantic
component renderer. Live output uses one numbered physical row per LF-derived
source line, visible plain current/selection markers, optional fixed ANSI
decoration, and Unicode-cell-aware ellipsis clipping. It never wraps one source
line into multiple rows. A live row scans and sanitizes only the visible-width
prefix plus a fixed 256-source-code-point allowance, bounding redraw work for
unseen multi-MiB suffixes and leading zero-width combining runs. Its
ordinary-line projection always remains plain and shows every sanitized
character of the accepted numbered document plus the exact range prompt. It
does not width-ellipsize fallback content; long lines wrap in scrollback.

| Profile | Selection | Output and input behavior |
| --- | --- | --- |
| Capable interactive ANSI | Real CLI with trusted TTY capability and non-`dumb` `TERM`; `NO_COLOR` absent for decoration | Structured layout plus fixed foreground/emphasis SGR. Safe functional cursor controls may support the focused live control. |
| Capable interactive plain | Same interactive capability with `NO_COLOR` present | The identical semantic structure without decorative SGR. Safe functional cursor controls may remain because `NO_COLOR` governs color, not editing capability. |
| Plain line fallback | Non-TTY, `TERM=dumb`, missing capability, or any uncertain capability result | Structured plain records; semantic input prompts pass once through the ordinary line callback and remain visible. Internal picker reads use complete numbered prompts and exact line grammar. No raw mode, cursor movement, live overlay, terminal mutation, or synthetic final control frame. |

For the viewer, either capable profile selects raw mode plus one alternate
screen; `NO_COLOR` changes only the current/selection decoration. Fallback
accepts exact ASCII positive `START-END` or `/cancel`, normalizes reversed
endpoints, and visibly retries invalid input without ANSI, raw mode, or an
alternate screen. This component fallback does not make the viewer reachable
from the installed shell.

Before activating either capable profile, the driver resolves a byte-backed
input's declared codec through the public registry. Only canonical `ascii`,
`utf-8`, and `iso8859-1` enter the live path; their registry aliases are
equivalent. Every other or unknown codec—including ISO-2022 variants, HZ,
UTF-7, UTF-8-SIG, and `raw-unicode-escape`—chooses ordinary fallback before
`PC_VDISABLE`, control-attribute inspection, byte reads, live-terminal output,
raw mode, or alternate-screen work. These examples illustrate an allow-policy,
not a deny-list.

Within an allowed codec, the driver compares raw configured VINTR/VEOF bytes
with `PC_VDISABLE` before decoding. An enabled byte must decode to one code
point and round-trip exactly through the stream encoding and error policy;
incomplete, lossy, or incompatible mappings still choose fallback. Text-only
one-code-point collaborators retain their separate semantics. The viewer never
starts raw mode and then changes interaction modes because of codec uncertainty.

The non-TTY profile does not bypass the installed shell's documented
[TTY guard](../operations/terminal-safety.md#the-tty-guard-comes-first). It
governs module/callback consumers and safe fallback after an entry boundary
has permitted interaction; bare `story` and `story chat` retain their current
guard and `tty_required` behavior.

Callback-based `InteractiveTerminal` construction remains plain, uses its
supplied width contract, and acquires no raw resource. Capability and stream
inspection occurs only when a real CLI interaction begins.

Every semantic `Panel` uses this canonical plain shape at a sufficiently wide
width:

```text
--- Panel title [completed] ------------
  PANEL CONTENT
----------------------------------------
```

The top splitter begins with the ASCII prefix `--- `, carries the sanitized
title and textual state, and fills its remaining usable width with `-`. The
bottom splitter uses `-` across the usable width. There are no side borders.
At narrow widths the title and state wrap with visible continuation indentation,
and the trailing top run is omitted when fewer than four cells remain; content
and the bottom splitter are retained. The splitter lines remain part of the
same component render and callback write as the body, so callback cardinality
and committed scrollback behavior do not change.

The driver evaluates input-source and output-destination capabilities
independently. A successful capability result for one destination never stands
in for the other, and live raw editing is enabled only when every capability
required by that operation is established. Cursor row/column calculations use
the actual physical display columns of the destination, including the
sanitized text's zero-, one-, and two-cell measurements.

Viewer geometry is bounded and call-local. It observes width and height before
the first frame and after every nonexit key, windows a contiguous slice around
the active line or a complete selection when it fits, and redraws the result on
the alternate screen. Resize alone does not redraw because no signal handler or
background loop is installed; the next key performs the observation. Cursor
visibility remains untouched.

The ANSI policy is intentionally fixed:

- only fixed foreground colors and emphasis may be decorative SGR in semantic
  views; the `AGENT` role uses bold with the terminal-default foreground. The
  separate viewer may additionally use fixed inverse SGR for selected rows;
- background detection or selection, backgrounds, dim, blink, OSC, terminal
  queries, and untrusted control sequences are forbidden;
- `NO_COLOR` disables decorative SGR only and does not flatten the structured
  layout;
- each styled span uses a local reset so style cannot bleed into adjacent
  content, prompts, or the user's shell;
- untrusted text is sanitized before styling, wrapping, padding, or joining
  with fixed controls; and
- ANSI sequences have zero display width.

Removing only the allowed fixed decorative SGR sequences from an ANSI render
must yield the same structured text as the plain renderer. ANSI and plain are
two presentations of one semantic layout, not separate content paths.

Display measurement follows the established standard-library rule: combining
and enclosing marks use zero cells, East Asian wide/fullwidth code points use
two, and other safe visible code points use one. Styling never changes those
measurements.

The real CLI detects terminal width, falls back to 80 columns when no reliable
positive width is available, and caps detected width at 120 columns. A resize
is observed on the next live-input or semantic-render operation; no
signal-driven resize loop is added. Composer/confirmation input and composer
lifecycle events use the current capability width for their next render. A
live picker re-observes capability and width on every redraw and windows at
most seven choices; fallback renders the complete numbered choice list.
Narrow layouts degrade in this order: remove decorative alignment, stack
`Field` label/value pairs, reduce indentation, wrap content with visible
continuation structure, and window long selectable lists with position cues.
They must not drop semantic labels, status meaning, citations, item identity,
or committed content merely to preserve a wide layout.

Color is always redundant with words, labels, order, or symbols. Focus,
highlight, selected, disabled, loading, empty, error, cancelled, and completed
states remain distinguishable in plain output and under `TERM=dumb`.

### Advisory progress indicator

During advisory turns and consensus waits, a capable raw stream may show the fixed
four-glyph
cycle `◐`, `◓`, `◑`, `◒` at the active cell. The existing raw driver owns the
indicator state, clears it before each semantic or provider-result write, and
redraws it while the operation remains pending. Completion, cancellation, and
failure join the operation and indicator tasks, then clear the cell before
their normal records are rendered. `NO_COLOR` retains the same
glyphs because it disables decorative SGR only; the indicator adds no ANSI
styling, dynamic text, alternate screen, or background detection.

Callback construction and stream paths without raw capability—including
non-TTY, `TERM=dumb`, non-POSIX, missing, or uncertain capability—await the
same awaitable silently. The installed CLI's TTY guard still rejects
non-TTY shell entry before controller work. A provider/operation failure wins
over a simultaneous indicator failure; an indicator-only failure is converted
to the existing internal error after cleanup. No other view or operation may
reuse this animation lifecycle.

## Compatibility contract

Human interactive layout may intentionally change as its owning migration
slice lands; #83 and #84 have changed their migrated output groups in that
permitted way, and #123 adds splitter lines to every semantic `Panel`. Exact
interactive and callback text therefore changes for those components, while
component ordering, one-component/one-write callback cardinality, interaction,
runtime behavior, and established public Python calls remain compatible. The
only later additive public seam is the optional `write_error()` debug-log path
described below. The following contracts may not otherwise change as a side
effect.

### Machine-facing CLI

The noninteractive `init`, `smoke`, `session`, `directive`, `inspect`, and `run`
commands remain unstyled. Their success stdout, JSON schemas, key and record
ordering, and exit behavior remain unchanged. Except for implicit temporary
smoke, after CLI workspace initialization or discovery succeeds, a caught
noninteractive exception preserves its established first stderr line and adds
the fixed second line:
`Debug log (if available, workspace-relative): debug_logs/runtime.jsonl`.
This boundary is independent of diagnostic-handle acquisition or file
existence. Initialization and discovery failures remain hint-free. Returned
domain outcomes do not add the hint: smoke
invariant exit `1` and run deadlock/failed-result exits `3`/`4` retain their
established JSON. Argparse and other pre-workspace failures add no debug-log
hint. Interactive capability, width, color, or `NO_COLOR` settings must not
affect these contracts.

### `interactive_terminal` public Python surface

The package conversion preserves the supported import
`story_writing_agents.interactive_terminal` and this exact `__all__` order:

```python
[
    "AttachmentKind",
    "ComposeMode",
    "ConfirmationResult",
    "TerminalActionKind",
    "TerminalMessageKind",
    "InteractiveErrorCode",
    "TerminalAction",
    "InteractiveErrorSpec",
    "INTERACTIVE_ERROR_SPECS",
    "InteractiveError",
    "sanitize_terminal_text",
    "InteractiveTerminal",
]
```

Enum names and values remain exact:

| Enum | Values |
| --- | --- |
| `AttachmentKind` | `created`, `opened`, `reopened` |
| `ComposeMode` | `chat`, `run` |
| `ConfirmationResult` | `yes`, `no`, `interrupt`, `eof` |
| `TerminalActionKind` | `line`, `send`, `cancel`, `interrupt`, `eof` |
| `TerminalMessageKind` | `compose`, `session`, `conversation`, `routing`, `context`, `waiting`, `result`, `cancelled`, `hint` |
| `InteractiveErrorCode` | `tty_required`, `workspace_not_initialized`, `workspace_invalid`, `workspace_unsafe`, `workspace_version_unsupported`, `configuration_invalid`, `command_invalid`, `agent_invalid`, `session_invalid`, `session_exists`, `session_not_found`, `session_not_attached`, `session_paused`, `session_access_blocked`, `session_ownership_unknown`, `conversation_required`, `conversation_invalid`, `conversation_not_found`, `conversation_ambiguous`, `conversation_archived`, `conversation_create_failed`, `transcript_invalid`, `retrieval_invalid`, `retrieval_budget_exhausted`, `provider_preflight_failed`, `provider_exited`, `provider_timed_out`, `provider_protocol_error`, `draft_input_invalid`, `consensus_deadlock`, `consensus_failed`, `artifact_conflict`, `operation_cancelled`, `operation_interrupted`, `cleanup_failed`, `internal_error` |

The `routing` and `waiting` enum values remain compatibility identities for
their other established uses. The #111 advisory-turn path emits neither label;
its capable-only indicator is terminal-owned and produces no semantic record.

Dataclass schemas, frozen/slotted behavior, defaults, validation, and positional
versus keyword-only construction remain exact:

```text
TerminalAction(kind, *, text=None, line_count=0, character_count=0)
InteractiveErrorSpec(summary, hint)
InteractiveError(code, *, summary=None, hint=None)
```

The constructor and every public method signature documented in the
[interactive terminal reference](../reference/interactive-terminal.md#public-api)
remain compatible. In particular, callback construction remains:

```python
InteractiveTerminal(read_line, write_text, *, width=80, main_read_line=None)
```

`write_error()` alone adds the optional keyword-only
`debug_log_path: str | None = None`. Omission preserves the exact established
two-notice panel and existing callback behavior. Supplied nonblank, one-line,
strict-UTF-8 display text adds one sanitized third notice labelled `debug log`;
the terminal does not resolve or retain it. After workspace resolution, the
installed CLI and controller supply the fixed workspace-relative availability
notice. Pre-workspace errors omit the keyword and include no debug-log path.

Construction validates and retains borrowed callbacks but performs no stream
inspection, output, raw-mode change, capability query, resource acquisition,
or history persistence. Existing constructor validation remains exact:
callbacks are callable, `main_read_line` is callable or `None`, and `width` is
a non-Boolean integer of at least 20. `INTERACTIVE_ERROR_SPECS` retains its
immutable keys and default copy, and `sanitize_terminal_text()` retains its
public validation and visible-escape behavior. The package facade exports the
actual public objects, not wrapper subclasses or duplicate type definitions.
Public object identity, `isinstance` behavior, pickling/import lookup, and
canonical `story_writing_agents.interactive_terminal` module identity remain
compatible.

#83 adds `InteractiveSemanticOutput`, `Field`, `Message`, `Notice`, `Panel`,
`Record`, `Section`, `SemanticRole`, and `ViewState` as unlisted package-facade
attributes for internal controller wiring; #84 adds the existing `ListView`
identity to that same unlisted seam. #85 adds unlisted
`MainInputHistoryState`, `MainInputControlState`,
`append_main_input_history`, and `transition_main_input` bindings for the
compatibility facade. These names are not supported public API, do not change
`__all__` or the root package facade, and must not be imported from package
leaves by controller code. Constructing the semantic adapter validates and
retains an `InteractiveTerminal` writer without rendering, observing streams,
or acquiring a resource; each `write_component(component)` call renders and
writes exactly one component.

#86 adds package-private `ComposerState`, `InputPromptView`, and
`RenderedInputFrame` contracts. Those types remain outside the package facade,
root facade, public `__all__`, and closed `Component` union;
`ControlOutcomeKind.REJECTED` is likewise internal. The established public
result types and constructor remain exact, and existing method calls remain
compatible.

#79 adds package-private completion candidates/projections and main palette
state plus the unlisted facade bindings `transition_main_command_input` and
`read_main_command_line`. The model values are not `Component` members; only
their projected existing `SuggestionPalette` is rendered. These names remain
outside public `__all__` and the root facade. The established parser,
`InteractiveTerminal`, and line-editor public results and established calls
remain compatible.

#87 adds package-private `PickerControlState` and `PickerViewport` contracts
alongside the existing semantic `Picker`, plus the unlisted facade identities
`Choice`, `Prompt`, `ControlOutcome`, `ControlOutcomeKind`, and
`InteractivePickerInput`. `PickerControlState` and `PickerViewport` remain
outside the closed `Component` union. `InteractivePickerInput` is a
resource-free internal adapter over an `InteractiveTerminal`; its
`read_picker()` method is not supported public API. No root export, public
`__all__` entry, existing signature, or direct application consumer changes.

#162 adds the unlisted facade identities `InteractiveTextViewer`,
`TextViewerExitKind`, and `TextViewerResult`. `read_range()` accepts supplied
text plus display metadata and an optional initial line, then returns only a
typed exit and optional inclusive tuple. These names remain outside public
`__all__` and the root facade. The private document/state/viewport contracts
remain outside the closed `Component` union, and no application consumer or
supported signature changes.

### `interactive_line_editor` compatibility facade

`story_writing_agents.interactive_line_editor` remains importable with this
exact `__all__` order:

```python
[
    "SlashCommandSuggestion",
    "slash_command_suggestions",
    "InteractiveMainLineEditor",
]
```

`SlashCommandSuggestion(token, canonical_token)` remains a frozen, slotted
dataclass with the same validation. `InteractiveMainLineEditor` preserves its
constructor `(fallback_read_line, input_stream, output_stream)` and
`read_line(prompt) -> str`. The facade exports compatible object identities and
canonical `story_writing_agents.interactive_line_editor` module identity, but
contains no independent raw-mode, key-decoding, cursor, width, write, flush, or
restoration implementation. It delegates to one unified driver whose public
`read_line()` route owns that editor instance's isolated process-local main
history. Package-internal contextual reads remain history-free.

### Root package facade

The root `story_writing_agents` package facade remains unchanged. The terminal
package conversion adds no root-package import or re-export and changes no name
or order in `story_writing_agents.__all__`.

## Approved module boundary

`interactive_terminal` is an import-compatible package, and
`interactive_settings` is its separate internal consumer-side persistence
boundary. Responsibility and dependency direction are fixed as follows:

| Unit | Responsibility |
| --- | --- |
| `interactive_settings` | Package-internal immutable opening baseline and pending configuration, safe lexical persona catalog, validation, exact stale-state detection, changed-only canonical persistence, and reverse exact-byte rollback. It has no terminal, controller, runtime, backend, or parser dependency and no facade export. |
| `interactive_terminal.__init__` | Compatibility facade with the exact existing public surface, including the additive optional `write_error()` debug-log-path keyword, plus package-internal stream construction, semantic output, picker, cancellable-prompt, and supplied-text viewer seams. It owns no import-time resource or state. |
| `interactive_terminal._model` | Immutable semantic components, bounded main-history/control state, static and controller-owned main-choice palette state, composer and picker control state, bounded picker viewport, semantic input/render frames, key/focus values including Shift+Up/Down, and transition/render results. No I/O. |
| `interactive_terminal._controls` | Pure established line-input, main-command/history, dynamic main-choice, composer-line, confirmation-answer, cancellable-prompt, and picker transitions; bounded history; completion/choice projection; picker initialization/reconciliation, disabled-skipping live navigation, exact fallback grammar, and viewport selection. |
| `interactive_terminal._render` | Pure safe responsive output and semantic input renderers for plain and fixed ANSI profiles, including bounded live and complete-list fallback picker projections and the strict JSON agent-reply presentation projection. It sanitizes dynamic content before styling and returns render data without writing. |
| `interactive_terminal._reference_activation` | Private pure cursor-active provider-scope projection for `$`, `$$`, and exact `$${path}` markers; filesystem-source completion, selected-path correlation, exact marker/range replacement, and content-free fixed failure values. It performs no I/O and retains no state. |
| `interactive_terminal._viewer_model` | Immutable strict-UTF-8 supplied document, active/anchor state, height-bounded viewport, fixed notice, exit kind, and content-free result values; private 8 MiB source, 65,536 logical-line, and complete 16 MiB fallback-projection capacities with LF-only line derivation. No I/O. |
| `interactive_terminal._viewer_controls` | Pure initialization, clamped ordinary and anchored navigation, normalized commit and noncommit exits, exact ASCII fallback grammar, and contiguous viewport projection. |
| `interactive_terminal._viewer_render` | Pure bounded-prefix one-row-per-source-line live projection and complete numbered fallback; visible escaping, one-based numbers, plain/ANSI current and selection cues, cell-aware live ellipsis clipping, and no fallback clipping. |
| `interactive_terminal._input_lifecycle` | Irreversible per-driver `UNDECIDED` to `LIVE`/`FALLBACK` selection; exact live source/decoder identity; canonical ASCII/UTF-8/ISO-8859-1 and built-in strict/surrogateescape admission; one persistent exact-byte incremental reader with bounded decoded suffix, stable EOF, poisoning, interrupt reset, and synchronous input exclusion. It owns no stream closure, terminal, key, readiness, screen, or restoration resource. |
| `interactive_terminal._driver` | Sole owner of raw streams, capability/width/height detection, byte/key decoding integration including Shift+Arrow, pre-activation configured-control validation, fixed Escape readiness wait after any pending decoded suffix, complete-live-frame cleanup, cursor controls, viewer alternate screen, raw mode, restoration, writes, flushes, and one bounded process-local main-history store per instance. Its supported main/composition, history-free semantic prompt, picker, and viewer routes accept call-local collaborators; reference picker/viewer interactions stay under the same input latch and non-main input does not consult history. |
| `interactive_terminal._core` | `InteractiveTerminal`, established public-operation orchestration, semantic composer/confirmation view construction and event mapping, canonical static/dynamic main-choice adapter, package-internal stream factory, semantic render seam, resource-free `InteractiveSemanticOutput`, and unlisted resource-free main, picker, prompt, and supplied-text viewer adapters. It binds call-local reference scope/source-list/text collaborators and these inputs to the sole driver without owning another store/raw implementation. |
| `interactive_line_editor` | Standalone/public compatibility-only facade over the same supported main-command reader and one per-editor driver/history store. |

The allowed graph is:

```text
cli -> interactive_terminal package-internal stream factory -> terminal._core
interactive_controller._core -> interactive_terminal facade components/adapter
interactive_controller._core -> interactive_settings -> agents/config/errors/workspace
interactive_controller policy leaves -> terminal facade error contracts
terminal._core -> terminal._controls -> terminal._model
               -> terminal._render   -> terminal._model
               -> terminal._reference_activation -> terminal._model / writing_artifacts
               -> terminal._viewer_controls -> terminal._model / terminal._viewer_model
               -> terminal._viewer_render   -> terminal._model / terminal._viewer_model
               -> terminal._viewer_model
               -> terminal._driver   -> terminal._input_lifecycle
                                     -> terminal._model / terminal._reference_activation / terminal._viewer_model
               -> interactive_commands / agents
interactive_line_editor facade -> same terminal._core / terminal._driver
```

The real CLI calls the package-internal factory to construct one unified
stream-backed terminal. It does not construct or inject a separate line editor.
`interactive_line_editor` remains available to standalone/public compatibility
consumers and delegates to that same implementation and driver.

The controller core imports only the terminal facade; it composes typed output,
sends each established semantic component through the unlisted adapter, and
supplies disposable resume-choice snapshots through `InteractiveMainInput`.
The runtime-policy leaf returns frozen, validated inspection facts rather than
presentation strings. Other policy leaves use only the facade's error
contracts for their terminal dependency. Controller modules do not import
terminal package leaves. `terminal._controls`,
`terminal._render`, and `terminal._driver` share contracts through
`terminal._model`; the driver depends on the one-way `_input_lifecycle` leaf
for retained input ownership and does not reach through other siblings for
private state.
`terminal._core` consumes canonical command, route, and roster metadata from
`interactive_commands` and `agents`. The controller alone reads session and
conversation catalogs, derives ordering/highlights, freshly validates a
committed choice, and coordinates settings focus and presentation;
`interactive_settings` owns immutable settings candidates and persistence
without importing the controller or terminal. No terminal package leaf imports
CLI, controller, runtime orchestration, providers, or storage.

The viewer leaves import only terminal models. They do not import workspace,
writing-artifact, repository, runtime, backend, provider, controller, parser,
or composer code. `terminal._core` supplies fresh validated state to the driver
for each call and drops it in `finally`; the driver retains no viewer mode,
document, selection, or geometry after return. The sibling activation leaf
imports terminal models and the public writing-artifact facade only; it owns no
catalog or text read. The controller owns provider-span gating and repository
callbacks, while the driver owns same-latch nested picker/viewer coordination.

### Implemented module-index delta

The current [Python module index](../../PYTHON_MODULE_INDEX.md) records 54
importable production units. The terminal/reference and provider/diagnostic
deltas combine as follows:

```text
pre-#164 parent inventory                 47
private reference-activation leaf          +1
provider-adapter and diagnostic modules    +5
canonical filesystem-source adapter        +1
current total                             54
```

`_reference_activation` adds only downward edges to `_model` and the public
`writing_artifacts` facade; `_core` and `_driver` consume it. The controller,
terminal and project-root public facades are unchanged. The five other modules
are `provider_adapter`, `provider_process`, `codex_adapter`,
`antigravity_adapter`, and `debug_log`; they do not change the terminal graph.
The additional `writing_artifacts.filesystem_sources` module owns
catalog-independent canonical discovery and source capture beneath the existing
repository facade.
Earlier history, viewer, input-lifecycle, settings, writing-artifact, and runtime
integration deltas remain recorded in the canonical index rather than
recomputed here.

## Migration coverage

Issue #82 delivered the common models, renderer/capability profiles, driver
seam, package facade, and module-index reconciliation. Issues #83 and #84
delivered their output rows below without redistributing runtime or storage
responsibility, #85 delivered main-command history, and #86 delivered semantic
composer/confirmation controls within the existing driver/control boundary.
#79 delivered selectable main-command completion within that same boundary.
#87 delivered the generic package-internal picker foundation within the same
boundary. #94 delivered the initial persisted Codex model/reasoning defaults
through the existing workspace, configuration, and backend units without
changing that graph; the current schema extends those same settings paths with
Antigravity model/effort. #93 installed `/settings`, added one internal settings
module, and historically brought the graph to 33 units. #142 added
controller-owned dynamic resume choices and a universal capable-input cleanup
lifecycle without another module. #162 added three private viewer
leaves plus the private input-lifecycle leaf while preserving the public and
root facades. The lifecycle leaf owns the irreversible latch and persistent
decoder; the driver remains the sole input consumer and terminal-resource
owner. #164 added the private pure reference-activation leaf, preserved public
exports, and lets the controller borrow source-list/text callbacks without moving
repository ownership into the terminal.

### View groups

| Current view group | Semantic construction | Required states or constraints | Delivery issue |
| --- | --- | --- | --- |
| Shell scaffold | `Panel`, `Section`, `Prompt`, `Notice` | Idle/focused scope, plain structure, scrollback preserved | #83 (implemented output scaffold; main/subject prompts remain established input, composer/confirmation input implemented by #86) |
| Lobby/session/conversation orientation | `Panel`, `Section`, `Field`, `Notice` | Current workspace, attachment, focus, and next action remain textual | #83 (implemented) |
| Help and command reference | `Panel`, `Section`, `Field`, `ListView`, `Record` | Grouping, syntax, aliases, effects, examples, empty/error | #84 (implemented) |
| Session/conversation/agent lists and rosters | `ListView`, `Record`, `Field`, `Notice` | Counts, active/focused/archived/empty meaning and stable order | #84 (implemented) |
| Search results | `ListView`, `Record`, `Field`, `Notice` | Citation, snippet, active/archive state, truncation, empty/error | #84 (implemented) |
| Transcript | `ListView`, `Record`, `Field`, `Notice` | Event identity/order and LF content boundaries remain intact | #84 (implemented) |
| Focused history | `ListView`, `Record`, `Field`, `Notice` | Snapshot order, context accounting, empty/cancelled/error | #84 (implemented) |
| Advisory turns | `Message`, `Notice`, `Field` | Operation result, cancelled/completed/error; optional capable-only indicator during advisory/consensus wait; no `[routing]`/`[waiting]` record | #83 and #111 (implemented) |
| Retrieval progress and summaries | `Notice`, `Record`, `ListView`, `Field` | Loading, citations/context accounting, result/error/cancelled | #83 (implemented accepted views) |
| Consensus progress and summaries | `Panel`, `Section`, `Notice`, `Record`, `Field`, `Message` | Loading, debate identity, handoff/action metadata, agent messages, result/cancelled/completed/error | #83, #119 (implemented) |
| Interactive errors and recovery hints | `Notice`, `Field` | Trusted code, redaction, error state, next action, no raw diagnostics; post-workspace application errors supply one fixed sanitized debug-log availability notice, while pre-workspace errors include no debug-log path | #83, #131 terminal seam, and #132 application wiring (implemented) |
| Settings current/pending view | `Panel`, `Section`, `Field`, `Prompt`, `Picker`, `Notice` | Exact top-level order; changed-only Current/Pending confirmation context with `(Changed)` cues and explicit no-diff notice; nested Escape silent; transient cleanup and fallback honesty; disabled provider, validation, error, cancelled, and completed meaning; guarded two-file Save boundary | #93, #106, and #115 (implemented) |

### Control groups

| Control group | Semantic construction | Interaction owner | Delivery issue |
| --- | --- | --- | --- |
| Main one-line input and editing | `Prompt`, optional `SuggestionPalette` | Unified key/focus state, editable buffer, selectable completion/history coordination, parser-once commit | #85 and #79 (implemented) |
| Process-local main history | Main `Prompt` buffer state | Up/Down, 100 exact nonblank entries, draft/cursor restoration, non-main isolation | #85 (implemented) |
| Line-oriented composer | `Field`, `Notice`, `Prompt` | Ordered context, exact line collection, fixed preview, and semantic open/error/completed/cancelled events | #86 (implemented) |
| Confirmation | `Field`, `Prompt`, `Notice` | Ordered question/choices/default, invalid retry, no suggestions, and typed terminal events | #86 (implemented) |
| Main command/subcommand palette | `SuggestionPalette`, `Prompt`, `Notice` | Slash-open, Up/Down clamped highlight, Tab full-token replacement, Escape snapshot restoration and dismissal | #79 (implemented) |
| Fixed command values | `SuggestionPalette`, `Field` | Canonical finite grammar choices, including metadata-exposed agent IDs/aliases; palette activity has no effects beyond call-local input state | #79 (implemented) |
| Single-select and selectable-list choices | `Picker`, `ListView`, `Record`, `Notice` | Disabled-skipping clamped Up/Down highlight, Enter commit, Escape cancel, typed interrupt/EOF, bounded live/full fallback rendering, internal terminal states without a synthetic final control frame | #87 foundation, #93 and #142 consumers (implemented) |
| Independent ranged text viewer | Private viewer document/state/viewport/result values, not a `Component` | Ordinary versus anchored clamped navigation, inclusive commit, typed noncommit exits, height-bounded alternate-screen rendering, strict complete-text fallback, and no retained caller or composer state | #162 foundation; #164 provider-bound shell caller (implemented) |
| Artifact-reference activation | Private marker projection plus existing palette/picker/viewer controls | Provider-span-only `$` completion, direct `$${path}` viewer, `$$` picker/viewer, exact origin restoration, multiple ordered indicators, fixed content-free errors, and fallback second confirmation | #164 (implemented) |
| Live omitted-resume choices | `SuggestionPalette`, `Picker`, `Prompt`, `Field` | Exact capable command only; controller-owned ordered rows/highlight/fresh validation; Up/Down preview; one-Enter explicit commit; Escape dismissal; Tab no-op; submitted omitted command uses a live capable or numbered fallback picker only after a successful fresh read returns eligible rows; empty/error short-circuit | #142 (implemented) |
| Settings field and action navigation | `Panel`, `Prompt`, `Picker`, `Field`, `Notice` | Single focus; nested agent/provider and value pickers; transient text/numeric editors; explicit Save/Cancel; no write before guarded Save; universal capable cleanup and honest fallback visibility | #93, #106, #115, and #142 (implemented) |

Contextual non-main-prompt suggestions from completed issue #73 use the same
unified driver without moving parser, runtime, or storage authority into the
terminal design system. #86 uses that prerequisite; #79 builds on the same
foundation only for the main selectable route.
#93 consumes the implemented #87 picker behavior and #94 workspace
model/reasoning fields without broadening picker ownership. Its one internal
`interactive_settings` module owns snapshot validation and persistence.

## Verification and acceptance

Each delivery slice must add deterministic tests at the lowest owning boundary
and focused integration coverage. The complete program is accepted only when
all of the following are covered.

#82 covers public identity/signatures, model validation, current pure line
transitions, base reserved-key handling, safe plain/ANSI rendering, narrow physical
width, capability profiles, unified driver/factory ownership, compatibility
delegation, and the package graph. #83 covers its migrated output rows,
resource-free adapter, typed controller facts, one-write projection, and real
PTY ANSI/`NO_COLOR` behavior. #84 covers full/context help, roster and dense
lists, positioned one-record callbacks, exact empty/result/hint/context records,
LF preservation, and cancellation after partial output. #85 covers the bounded
isolated store, pure navigation, live/fallback commit boundaries, parser-once
behavior, non-main isolation, and restoration. #86 covers immutable composer
state, pure composer/confirmation transitions, semantic input view order,
plain/ANSI equivalence, safe fallback/live routing, history isolation, exact
events and results, and unchanged public/machine contracts. #79 covers exact
cursor-aware grammar projection, palette state, history coordination, Tab and
Escape behavior, semantic plain/ANSI rendering, fallback isolation, incremental
UTF-8/Escape decoding, parser-once commit, restoration, and unchanged
public/machine contracts. #87 covers picker initialization and reconciliation,
disabled-skipping bounded navigation, independent selection/highlight,
commit/cancel/interrupt/EOF outcomes, exact numbered fallback, responsive
plain/ANSI rendering, restoration, failure cleanup, and history/completion
isolation. #93 covers its implemented settings interaction, persistence, and
lifecycle snapshot contract. #94 covers the original five-key and legacy
schema handling; current coverage also requires the seven-field system and
per-agent schemas, provider-specific safe model/effort validation,
full-schema-first failure, precedence, backend-lifetime snapshots, exact
fresh/resume invocation arguments, initialization preservation, and unchanged
definition/thread/public contracts.
Issue #119 extends the same output boundary with per-phase handoff/action
metadata and a dedicated semantic `Panel` titled `Agent saying` for new agent
messages in each phase.
Issue #123 extends the shared renderer so every semantic `Panel` uses the same
responsive ASCII top-title and bottom splitters without changing component
models, callback boundaries, public APIs, or runtime behavior.
Issue #142 covers canonical resume grammar, disposable live choice snapshots,
session/conversation ordering and highlights, static-completion versus dynamic-
commit Enter behavior, history routing, fresh post-submit validation, capable
complete-frame cleanup across every input route and terminal outcome, ordinary
subject semantics, fallback visibility, persistent semantic results, and
unchanged public/internal action identities.
Issue #162 covers strict pre-I/O supplied-text validation, independent source,
logical-line, and complete-fallback capacities, LF-only line derivation,
immutable active/anchor state, ordinary and reverse anchored navigation,
normalized inclusive commit, exact ASCII complete fallback, bounded-prefix
plain/ANSI live rendering, cell-aware clipping, canonical byte-codec admission,
exact configured-control classification, irreversible input-mode selection,
persistent fragmented-code-point decoding across every input route, pending
suffix Escape handling, source/configuration drift and poison behavior,
history-free capable subject presentation, next-key resize observation,
alternate-screen and termios cleanup precedence, typed noncommit exits,
repeat-open disposal, dependency isolation, and unchanged public exports.

| Area | Required evidence |
| --- | --- |
| Capability profiles | Representative plain, fixed-ANSI, `NO_COLOR`, narrow, non-TTY, `TERM=dumb`, missing/uncertain and independent input/output capability combinations, 80-column fallback, and 120-column cap cases. |
| Rendering safety | Hostile ESC/OSC/control/bidi-format content is visibly sanitized before ANSI; local resets prevent bleed; removing decorative SGR exactly recovers the plain structure; ANSI is zero-width; wide/combining content and cursor positions use physical columns. |
| Semantic output | Every view group uses only the closed vocabulary; status remains understandable without color; one semantic record uses one callback write unless an explicit live-control exception is tested. |
| Panel framing | Every semantic `Panel` entry path uses the ASCII top-title and bottom splitters in plain, ANSI, and `NO_COLOR` output; wide, narrow, wrapped-title/state, and physical-cell widths preserve content and omit side borders; non-Panel components remain unframed; one Panel remains one callback write. |
| Agent reply presentation | Complete strict JSON object/array formatting only; two-space indentation; preserved key order and Unicode; unchanged fallback for malformed, embedded-prose, scalar, duplicate-key, non-finite, or unserializable content; direct/retrieval/transcript/history presentation only; stored/provider bytes unchanged; bold terminal-default `AGENT` styling with no background detection. |
| State transitions | Pure tests cover idle/focused/open/highlighted/selected/disabled/loading/empty/error/cancelled/completed states and invalid or boundary transitions. |
| History | Exact whitespace-preserving commits including invalid commands, duplicates, `/quit`, fallback, and nonempty VEOF; blank/interrupt/empty-VEOF/physical-EOF/failure exclusion; 100-entry oldest eviction; older/newer bounds; exact draft/cursor restoration; editable copies; per-driver isolation; non-main exclusion; no persistence; and post-restoration append. |
| Palette/history coordination | Slash opening with deterministic initial highlight and unchanged buffer; filtering; empty/error; canonical metadata projection including finite agent IDs/aliases; open-palette Up/Down clamped highlight navigation without replacement or history movement; Tab application of the highlighted candidate; Shift-Tab no-op; Escape full-snapshot restoration and exact-state dismissal; closed-palette Up/Down history; edited recall; and visible-buffer-only Enter commit. |
| Picker and live resume choices | Highlight-versus-selection, disabled-skipping clamped navigation, exact resume ordering/highlights, Tab no-op and one-Enter dynamic commit, Escape cancel/dismissal, typed interrupt/EOF, empty/all-disabled/one/long lists, exact fallback acceptance/rejection, stable identity reconciliation and fresh controller validation, selected-outside-window summary, no synthetic final control frame, complete capable cleanup on every returned outcome/failure, honest fallback visibility, and resize on each live redraw. |
| Permanent input lifecycle | Resource-free `UNDECIDED` construction; first-read irreversible `LIVE`/`FALLBACK`; sticky fallback and no post-live downgrade; stable source/decoder/configuration correlation; canonical ASCII/UTF-8/ISO-8859-1 plus built-in strict/surrogateescape admission; exact one-byte reads; fragmented code points; maximum four-code-point pending suffix before Escape readiness; clean and partial EOF; read/decode/shape poison; interrupt reset without mode change; synchronous reentry rejection; PENDIN-only tolerated configuration drift; capable history-free subject frames; and cross-prompt/viewer preservation without mixed wrapper/raw reads. |
| Ranged text viewer | Exact `str`, display-name, strict-UTF-8, private 8 MiB source, exact 65,536-line and 16 MiB complete-fallback boundaries, initial-line, empty/final-LF validation before terminal access; ordinary and Shift+Arrow clamped selection including reversal; configured VINTR/VEOF raw-byte disable/decode/round-trip classification and priority; singleton/range commit; distinct cancel/interrupt/EOF; one row per logical line; safe CR/U+2028/control display; bounded-prefix plain/ANSI current and selected cues; wide/combining/ellipsis behavior; suffix-independent redraw work; bounded height and next-nonexit-key resize; exact untruncated `START-END` or `/cancel` fallback with reverse normalization and visible retry; content-free results; close-on-select/cancel, repeat-open disposal; and no workspace/artifact/runtime/controller dependencies. |
| Settings | Two-snapshot current/pending separation; exact top-level field and canonical roster order; safe persona precedence and lexical order; numeric `0`, positive, blank, negative, fractional, and nonnumeric debate-round cases; selectable Codex/Antigravity routes with Codex default, inactive-provider setting preservation, and `gemini` rejection; provider-specific safe model text and blank/null inheritance; exact Codex and Antigravity effort enumerations including Codex `xhigh`; one focused control and deterministic focus return; nested and whole-panel cancellation; exact-byte and catalog conflict before writes; changed-only canonical system-then-agent save; per-file atomic replacement; failing-target-inclusive reverse exact-byte rollback and rollback uncertainty; pending retention; no write on open/edit/highlight/error/cancel; next-lifecycle-only application with higher live overrides; plain/ANSI/`NO_COLOR`/narrow/line fallback; hostile display content; and unchanged identity, thread, precedence, public, and machine contracts. |
| Advisory progress | One four-glyph cycle only for capable raw advisory and consensus awaitables; `NO_COLOR` glyph preservation; clear/redraw around semantic writes; clear and task joining before result/cancel/error; silent callback/non-TTY/`TERM=dumb`/uncertain fallback; operation-error precedence; indicator-only internal-error mapping; no `[routing]`/`[waiting]` records or reusable animation API. |
| Parser and mutation boundary | The exact visible committed main buffer reaches the established parser once; previews, recall, replacement, highlight, rerender, cancel, Ctrl-C, empty VEOF, and physical EOF cause no parser/runtime/storage call. |
| Artifact-reference activation | Provider-bound ordinary/routed/`/say`/`/run` and composer spans only; escaped-dollar inertness; fresh sorted canonical-filesystem projection; `$` whole-file completion; explicit `$${path}` viewer; `$$` picker then viewer; multiple ordered indicators; exact marker replacement/cursor; capable same-read return; fallback second confirmation and repeated marker handling; empty/stale/unreadable/unavailable/invalid content-free failures; exact origin/focus/mode restoration; no submit/history/residue; listing-to-view stale detection; independent current runtime capture without viewer-byte reuse; and per-agent enforcement after later normal submission. |
| Composer/confirmation | Ordered semantic input facts and focused prompt; equivalent width-aware plain/ANSI rendering; fallback-once and trusted-live routing; exact `/send`, `/cancel`, `/say`, yes/no/default, invalid retry, Ctrl-C, and EOF results; semantic composer events and hint cardinality; history isolation; reserved-key no-ops; no full-screen or persistent state. |
| Restoration | Complete-frame cleanup for every capable main/subject/composition/confirmation/picker/prompt submit, commit, cancellation, Escape return, EOF, Ctrl-C, setup failure, read/write/flush failure, and render error; viewer alternate-screen entry/exit pairing and raw-attribute restoration on every live outcome/failure; restoration-over-screen-over-active failure precedence; no fallback after `LIVE`; cursor visibility untouched; completion-only Enter remains inside the live read; restoration uncertainty is fatal. |
| Public compatibility | Exact exports and order, enum values, dataclass fields/defaults/options, established-call-compatible signatures including exact validation/default behavior for the additive `write_error()` keyword, exceptions, object/class identity, canonical modules, facade imports, callback construction, and no constructor resource acquisition. |
| Noninteractive regression | Golden byte/schema/order/exit checks for `init`, `smoke`, `session`, `directive`, `inspect`, and `run`, including the exact caught-exception hint after successful CLI workspace entry regardless of diagnostic-handle availability, no hint for argparse, initialization/discovery or other pre-workspace failures, implicit smoke, smoke invariant exit `1`, or run domain exits `3`/`4`, unchanged success output, and no SGR under any environment. |

Process-level POSIX PTY coverage remains required for the real raw-mode and
restoration boundary. Viewer PTY coverage includes real Shift+Arrow bytes,
next-key resize, repeated alternate-screen pairing, VINTR, and VEOF. Physical
EOF is deterministic driver coverage because a PTY hangup also closes the
observation channel. Unit tests with deterministic fake drivers cover pure
state, rendering, capability, physical EOF, resize-next-render, and failure
paths without claiming unsupported-platform terminal behavior.

Migration is complete only after implemented behavior, the exact reference and
safety pages, tests, and the Python module index agree. Availability wording
must be updated by the issue that actually delivers each behavior, not by this
design contract.
