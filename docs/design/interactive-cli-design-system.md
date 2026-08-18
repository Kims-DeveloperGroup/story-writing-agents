# Interactive CLI design system contract

> **Status:** approved design for issues #82 through #87 and #79. This page
> defines migration requirements; it does not claim that the planned design is
> implemented. Until each slice lands, the
> [interactive terminal reference](../reference/interactive-terminal.md) and
> [terminal safety rules](../operations/terminal-safety.md) describe available
> behavior.

This contract governs every human-facing interactive view and control in the
installed shell. It preserves scrollback and the established command/runtime
boundaries while giving output, one-line input, line-oriented composition,
confirmation, suggestions, fixed choices, command history, and pickers one
semantic model.

The words **must**, **must not**, and **may** below constrain the delivery
issues. They do not change current production behavior by themselves.

## Goals and limits

The design must:

- represent content and interaction semantically before rendering;
- remain readable in structured plain text, including when color is disabled;
- use one raw-terminal owner and pure control transitions;
- keep preview and navigation free of runtime and storage effects;
- preserve public Python compatibility and noninteractive machine contracts;
  and
- use only the Python standard library.

This remains a scrollback-friendly, line-oriented CLI, not a full-screen TUI.
Mouse input, animation, the alternate screen, terminal queries, a
signal-driven background loop, user-selectable themes, persistent history,
fuzzy completion, and a full-screen composer are out of scope. Rendering must
not assume that earlier terminal rows can be revisited after an interaction is
committed.

## Closed semantic vocabulary

These are the only semantic view components. Delivery slices may compose them
but must not introduce an additional component kind without first revising
this contract.

| Component | Contract |
| --- | --- |
| `Prompt` | Names the active input request, its scope, and any concise instruction or default. It contains no input-side effects. |
| `Panel` | Groups a bounded related view or control without claiming the screen or erasing scrollback. |
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
| `loading` | Work has started and no terminal result is available yet. The view names the operation without relying on animation. |
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

## Input ownership and transition boundary

Exactly one terminal driver owns raw streams, capability and width detection,
raw-mode activation, key decoding, cursor control, writes, flushing, and
restoration. No editor, renderer, model, control transition, controller, or
compatibility facade may independently acquire or restore the terminal.

Controls receive normalized key events such as text, Enter, Backspace, Delete,
Left, Right, Home, End, Up, Down, Tab, Shift-Tab, Escape, Ctrl-C, and EOF. Their
transitions are pure:

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
invoke that parser.

For established output primitives, one semantic `Record`, `Message`, or
`Notice` produces one callback write including its final newline. A delivery
slice may make a narrower exception only for an explicitly documented and
tested live-control redraw/restoration sequence. It must not split one domain
record into multiple caller-visible callback records.

## Key and commit semantics

| Focused control | Navigation and editing | Commit | Cancel or terminal event |
| --- | --- | --- | --- |
| Main one-line input | Text, Backspace/Delete, Left/Right, and Home/End edit. Up/Down navigate history only while no palette is open. Slash-prefixed input may open a palette; Tab applies its highlighted completion. | Enter submits the exact visible buffer to the existing parser once. | Ctrl-C and EOF retain their established typed outcomes; no text is parsed. |
| `SuggestionPalette` anchored to main input | Opening highlights the first eligible candidate without editing. Up/Down moves the highlight among eligible candidates without replacement; Tab applies the highlighted candidate to the applicable span. Shift-Tab has no palette action. | Enter submits the current visible buffer, never an unapplied highlighted candidate. | Escape closes the palette and restores the prefix captured before the first completion replacement. |
| `Picker` | Up/Down changes highlight among enabled items. Highlight never selects or commits. | Enter selects and commits the highlighted enabled item. | Escape cancels. Ctrl-C and EOF leave without commit and retain the invoking API's typed semantics. |
| Line-oriented composer | Each ordinary line appends exact content. No raw full-screen editing or command-history traversal is introduced. | `/send` commits the exact LF-joined content when nonblank. | `/cancel`, Ctrl-C, and EOF preserve established discard outcomes and counts. |
| Confirmation | Existing accepted yes/no input and blank-default behavior remain authoritative. Invalid input rerenders a notice and asks again. | A valid answer or blank default returns the established `yes` or `no` result. | Ctrl-C and EOF return the established `interrupt` or `eof` result. |

`/say /send` and `/say /cancel` in composition continue to append literal
`/send` and `/cancel` lines. The composer remains line-oriented; preview,
highlight, and history behavior supplied by contextual suggestions must not
change `/send`, `/cancel`, `/say`, Ctrl-C, or EOF semantics.

### Main-command history

History is process-local UI state owned by the unified terminal core. It is not
stored in a workspace, transcript, configuration file, environment variable,
or provider session.

- It contains at most 100 exact nonblank committed main-prompt submissions.
- Whitespace-only input, Ctrl-C, EOF, previews, and uncommitted buffers are not
  entries. Duplicate submissions remain separate entries.
- Appending the 101st entry removes only the oldest entry.
- While no suggestion palette is open, Up recalls older entries and Down recalls
  newer entries. An open palette consumes Up/Down for highlight navigation
  without changing the history cursor or draft.
- The buffer present when navigation begins is the draft. Down past the newest
  history entry restores that exact draft and exits navigation.
- A recalled value is copied into the editable buffer. Edits never mutate the
  stored history entry. A later navigation key may replace the edited copy;
  Down past newest still restores the original pre-navigation draft.
- Committing an edited recalled value submits that visible value once and, if
  nonblank, appends it as a new exact history entry.

### Main-command palette and fixed choices

A leading slash may automatically open `SuggestionPalette`. Candidate command,
subcommand, and fixed-value data comes from canonical command metadata, not a
second handwritten registry. Filtering, opening, empty results, highlight,
replacement, and dismissal are deterministic pure transitions.

Opening the palette highlights the first eligible candidate deterministically
without changing the editable buffer. While the palette is open, Up and Down
move the highlight among eligible candidates and clamp at the first and last
candidate; they do not replace text or traverse history. Tab applies the
highlighted candidate by replacing the applicable buffer span. Shift-Tab does
not navigate or apply the palette. Applying a highlighted candidate is
completion only, not parser or runtime commit. The control retains the
pre-completion prefix before the first replacement so Escape can restore it
exactly. Enter always commits the visible buffer through the existing parser
once and never implicitly applies an unapplied highlighted candidate. When the
palette is closed, Up/Down resume older/newer history traversal. Recalled text
remains editable and may reopen a palette based on its current buffer.

Fixed command arguments are palette candidates only when the existing grammar
exposes a finite canonical set. They do not add parser rules, authorization,
runtime actions, or fuzzy matching. Confirmation choices remain owned by the
confirmation control rather than the main-command palette. Finite choices
include canonical agent identifiers and accepted aliases for agent-target
positions when exposed by canonical command-route metadata; the palette
projects that metadata and does not duplicate parser grammar.

### Picker semantics

A `Picker` is single-select. Up/Down moves highlight without changing the
selected value or calling a consumer. Enter commits only an enabled highlighted
item; Escape cancels. Disabled items remain visible but are skipped by
navigation and cannot be committed. If no enabled item exists, the picker has
an explicit empty or disabled state and Enter has no commit outcome.

The picker retains stable highlight/selection by semantic item identity across
rerenders and next-render resize handling. Long lists may render a responsive
window with current-position and total-count cues, but must not use an
alternate screen or erase scrollback. One-item, long-list, disabled, empty,
cancelled, EOF, Ctrl-C, and error behavior must remain deterministic.

## Rendering and capability policy

The planned implementation has three observable capability profiles:

| Profile | Selection | Output and input behavior |
| --- | --- | --- |
| Capable interactive ANSI | Real CLI with trusted TTY capability and non-`dumb` `TERM`; `NO_COLOR` absent for decoration | Structured layout plus fixed foreground/emphasis SGR. Safe functional cursor controls may support the focused live control. |
| Capable interactive plain | Same interactive capability with `NO_COLOR` present | The identical semantic structure without decorative SGR. Safe functional cursor controls may remain because `NO_COLOR` governs color, not editing capability. |
| Plain line fallback | Non-TTY, `TERM=dumb`, missing capability, or any uncertain capability result | Structured plain records and ordinary line input. No raw mode, cursor movement, live overlay, or terminal mutation. |

The non-TTY profile does not bypass the installed shell's documented
[TTY guard](../operations/terminal-safety.md#the-tty-guard-comes-first). It
governs module/callback consumers and safe fallback after an entry boundary
has permitted interaction; bare `story` and `story chat` retain their current
guard and `tty_required` behavior.

Callback-based `InteractiveTerminal` construction remains plain, uses its
supplied width contract, and acquires no raw resource. Capability and stream
inspection occurs only when a real CLI interaction begins.

The driver evaluates input-source and output-destination capabilities
independently. A successful capability result for one destination never stands
in for the other, and live raw editing is enabled only when every capability
required by that operation is established. Cursor row/column calculations use
the actual physical display columns of the destination, including the
sanitized text's zero-, one-, and two-cell measurements.

The ANSI policy is intentionally fixed:

- only fixed foreground colors and emphasis may be decorative SGR;
- backgrounds, dim, blink, OSC, terminal queries, and untrusted control
  sequences are forbidden;
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

The real CLI detects terminal width for the next render or input operation,
falls back to 80 columns when no reliable positive width is available, and
caps detected width at 120 columns. A resize is observed on the next
render/input; no signal-driven resize loop is added. Narrow layouts degrade in
this order: remove decorative alignment, stack `Field` label/value pairs,
reduce indentation, wrap content with visible continuation structure, and
window long selectable lists with position cues. They must not drop semantic
labels, status meaning, citations, item identity, or committed content merely
to preserve a wide layout.

Color is always redundant with words, labels, order, or symbols. Focus,
highlight, selected, disabled, loading, empty, error, cancelled, and completed
states remain distinguishable in plain output and under `TERM=dumb`.

## Compatibility contract

Human interactive layout may intentionally change as its owning migration
slice lands. The following contracts may not change as a side effect.

### Machine-facing CLI

The noninteractive `init`, `smoke`, `session`, `directive`, `inspect`, and `run`
commands remain unstyled. Their stdout/stderr bytes, JSON schemas, key and
record ordering, and exit behavior must not change. Interactive capability,
width, color, or `NO_COLOR` settings must not affect them.

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
restoration implementation. It delegates to the unified terminal
implementation.

### Root package facade

The root `story_writing_agents` package facade remains unchanged. The terminal
package conversion adds no root-package import or re-export and changes no name
or order in `story_writing_agents.__all__`.

## Approved module boundary

`interactive_terminal.py` becomes an import-compatible
`interactive_terminal/` package. Responsibility and dependency direction are
fixed as follows:

| Unit | Responsibility |
| --- | --- |
| `interactive_terminal.__init__` | Compatibility facade with the exact existing public surface plus one package-internal real-CLI stream factory. It owns no import-time resource or state. |
| `interactive_terminal._model` | Immutable semantic components, control states, key/focus values, and transition/render results. No I/O. |
| `interactive_terminal._controls` | Pure input, history, palette, composer, confirmation, and picker state transitions. |
| `interactive_terminal._render` | Pure safe responsive renderers for plain and fixed ANSI profiles. It sanitizes dynamic content before styling and returns render data without writing. |
| `interactive_terminal._driver` | Sole owner of raw streams, capability/width detection, key decoding, cursor controls, raw mode, restoration, writes, and flushes. |
| `interactive_terminal._core` | `InteractiveTerminal`, public-operation orchestration, and bounded process-local UI state such as command history. It coordinates leaves without owning another raw-terminal implementation. |
| `interactive_line_editor` | Standalone/public compatibility-only facade over the same unified terminal implementation and driver. |

The allowed graph is:

```text
cli -> interactive_terminal package-internal stream factory -> terminal._core
interactive_controller._core -> interactive_terminal facade
interactive_controller policy leaves -> terminal facade error contracts
terminal._core -> terminal._controls -> terminal._model
               -> terminal._render   -> terminal._model
               -> terminal._driver   -> terminal._model
               -> interactive_commands / agents
interactive_line_editor facade -> same terminal._core / terminal._driver
```

The real CLI calls the package-internal factory to construct one unified
stream-backed terminal. It does not construct or inject a separate line editor.
`interactive_line_editor` remains available to standalone/public compatibility
consumers and delegates to that same implementation and driver.

The controller core imports only the terminal facade; its policy leaves use
only the facade's error contracts for their terminal dependency. Controller
modules do not import terminal package leaves. `terminal._controls`,
`terminal._render`, and `terminal._driver` share contracts through
`terminal._model`; they do not reach through siblings for private state.
`terminal._core` consumes canonical command, route, and roster metadata from
`interactive_commands` and `agents`. No terminal package leaf imports CLI,
controller, runtime orchestration, providers, or storage.

### Planned module-index delta

The current [Python module index](../../PYTHON_MODULE_INDEX.md) accurately
records 27 importable production units. Issue #82, not this design-only slice,
owns the implementation and index reconciliation:

```text
current interactive_terminal module                  -1
planned interactive_terminal package and five leaves +6
planned total                                         32
```

The six planned entries are the package plus `_model`, `_controls`, `_render`,
`_driver`, and `_core`. #82 must also revise the existing
`interactive_line_editor` and `cli` entries, and revise controller or
`interactive_commands` dependency/responsibility metadata if their completed
imports or canonical-command-metadata roles change. The index must continue to
describe observed implementation, never this plan, so it must remain at 27
until the package conversion lands.

## Migration coverage

Issue #82 first delivers the common models, renderer/capability profiles,
driver seam, package facade, and module-index reconciliation. The following
issues then migrate every current view and control without redistributing
runtime or storage responsibility.

### View groups

| Current view group | Semantic construction | Required states or constraints | Delivery issue |
| --- | --- | --- | --- |
| Shell scaffold | `Panel`, `Section`, `Prompt`, `Notice` | Idle/focused scope, plain structure, scrollback preserved | #83 |
| Lobby/session/conversation orientation | `Panel`, `Section`, `Field`, `Notice` | Current workspace, attachment, focus, and next action remain textual | #83 |
| Help and command reference | `Panel`, `Section`, `Field`, `ListView`, `Record` | Grouping, syntax, aliases, effects, examples, empty/error | #84 |
| Session/conversation/agent lists and rosters | `ListView`, `Record`, `Field`, `Notice` | Counts, active/focused/archived/empty meaning and stable order | #84 |
| Search results | `ListView`, `Record`, `Field`, `Notice` | Citation, snippet, active/archive state, truncation, empty/error | #84 |
| Transcript | `ListView`, `Record`, `Field`, `Message`, `Notice` | Event identity/order and LF content boundaries remain intact | #84 |
| Focused history | `ListView`, `Record`, `Field`, `Message`, `Notice` | Snapshot order, context accounting, empty/cancelled/error | #84 |
| Advisory turns | `Message`, `Notice`, `Field` | Routing, waiting/loading, result, cancelled/completed/error | #83 |
| Retrieval progress and summaries | `Notice`, `Record`, `ListView`, `Field` | Loading, citations/context accounting, result/error/cancelled | #83 |
| Consensus progress and summaries | `Panel`, `Section`, `Notice`, `Record`, `Field` | Loading, debate identity, result/cancelled/completed/error | #83 |
| Interactive errors and recovery hints | `Notice`, `Field` | Trusted code, redaction, error state, next action, no raw diagnostics | #83 |

### Control groups

| Control group | Semantic construction | Interaction owner | Delivery issue |
| --- | --- | --- | --- |
| Main one-line input and editing | `Prompt`, optional `Panel`/`Notice` | Unified key/focus state, editable buffer, parser-once commit | #85 |
| Process-local main history | Main `Prompt` buffer state | Up/Down, 100 exact nonblank entries, draft restoration | #85 |
| Line-oriented composer | `Panel`, `Prompt`, `Section`, `Notice` | Exact line collection and established send/cancel events | #86 |
| Confirmation | `Panel`, `Prompt`, `Field`, `Notice` | Fixed yes/no/default validation and typed terminal events | #86 |
| Main command/subcommand palette | `SuggestionPalette`, `Prompt`, `Notice` | Slash-open, Up/Down highlight, Tab replacement, Escape restoration | #79 |
| Fixed command values | `SuggestionPalette`, `Field` | Canonical finite grammar choices, including metadata-exposed agent IDs/aliases; preview has no effects | #79 |
| Single-select and selectable-list choices | `Picker`, `ListView`, `Record`, `Notice` | Up/Down highlight, Enter commit, Escape cancel, disabled/empty/error | #87 |

Contextual non-main-prompt suggestions remain dependent on external issue #73.
#86 and #79 integrate that prerequisite without moving parser, runtime, or
storage authority into the terminal design system.

## Verification and acceptance

Each delivery slice must add deterministic tests at the lowest owning boundary
and focused integration coverage. The complete program is accepted only when
all of the following are covered.

| Area | Required evidence |
| --- | --- |
| Capability profiles | Representative plain, fixed-ANSI, `NO_COLOR`, narrow, non-TTY, `TERM=dumb`, missing/uncertain and independent input/output capability combinations, 80-column fallback, and 120-column cap cases. |
| Rendering safety | Hostile ESC/OSC/control/bidi-format content is visibly sanitized before ANSI; local resets prevent bleed; removing decorative SGR exactly recovers the plain structure; ANSI is zero-width; wide/combining content and cursor positions use physical columns. |
| Semantic output | Every view group uses only the closed vocabulary; status remains understandable without color; one semantic record uses one callback write unless an explicit live-control exception is tested. |
| State transitions | Pure tests cover idle/focused/open/highlighted/selected/disabled/loading/empty/error/cancelled/completed states and invalid or boundary transitions. |
| History | Blank exclusion, duplicate preservation, 100-entry eviction, older/newer bounds, exact draft restoration, editable copies, and process isolation. |
| Palette/history coordination | Slash opening with deterministic initial highlight and unchanged buffer; filtering; empty/error; canonical metadata projection including finite agent IDs/aliases; open-palette Up/Down clamped highlight navigation without replacement or history movement; Tab application of the highlighted candidate; Shift-Tab no-op; Escape prefix restoration; closed-palette Up/Down history; edited recall; and visible-buffer-only Enter commit. |
| Picker | Highlight-versus-selection, enabled/disabled navigation, Enter commit, Escape cancel, empty/one/long list, stable rerender identity, and resize on next operation. |
| Parser and mutation boundary | The exact visible committed main buffer reaches the established parser once; previews, recall, replacement, highlight, rerender, cancel, Ctrl-C, and EOF cause no parser/runtime/storage call. |
| Composer/confirmation | Existing `/send`, `/cancel`, `/say`, yes/no/default, invalid input, Ctrl-C, and EOF results; no full-screen or persistent state. |
| Restoration | Normal submit, commit, cancellation, Escape, EOF, Ctrl-C, setup failure, read/write/flush failure, render error, and restoration uncertainty, with raw attributes restored whenever acquired. |
| Public compatibility | Exact exports and order, enum values, dataclass fields/defaults/options, signatures, exceptions, object/class identity, canonical modules, facade imports, callback construction, and no constructor resource acquisition. |
| Noninteractive regression | Golden byte/schema/order/exit checks for `init`, `smoke`, `session`, `directive`, `inspect`, and `run`, with no SGR under any environment. |

Process-level POSIX PTY coverage remains required for the real raw-mode and
restoration boundary. Unit tests with deterministic fake drivers cover pure
state, rendering, capability, resize-next-render, and failure paths without
claiming unsupported-platform terminal behavior.

Migration is complete only after implemented behavior, the exact reference and
safety pages, tests, and the Python module index agree. Availability wording
must be updated by the issue that actually delivers each behavior, not by this
design contract.
