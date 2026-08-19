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
> lifecycle configuration snapshots, and guarded persistence. #105 adds
> presentation-only strict JSON formatting for agent replies and bold
> terminal-default agent styling. The
> [interactive terminal reference](../reference/interactive-terminal.md) and
> [terminal safety rules](../operations/terminal-safety.md) describe current
> observable behavior.

This contract governs every human-facing interactive view and control in the
installed shell. It preserves scrollback and the established command/runtime
boundaries while giving output, one-line input, line-oriented composition,
confirmation, suggestions, fixed choices, command history, and pickers one
semantic model.

The words **must**, **must not**, and **may** below constrain the delivery
issues. Requirements beyond the implemented #82, #83, #84, #85, #86, #79,
#87, #94, #93, #105, and #73 slices do not describe current production
behavior.

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
| #94 workspace Codex defaults | Implemented: the closed system-default schema accepts optional workspace fallback Codex model and reasoning values, new workspaces initialize both to null, legacy files remain valid and byte-preserved, and each backend validates and snapshots effective invocation values at construction. |
| #93 settings UI | Implemented: global alias-free `/settings` edits isolated current/pending snapshots of workspace defaults and per-agent provider, model, and reasoning choices through shared picker and prompt controls. Save validates an exact opening baseline and persona catalog, writes only changed configuration documents in deterministic order, and attempts reverse exact-byte rollback after a write failure. The workflow adds no supported public save API. |
| #105 agent reply presentation | Implemented: direct, retrieval, transcript, and focused-history agent replies use a presentation-only strict JSON object/array projection with exact unchanged fallback cases and two-space Unicode-preserving output; stored/provider content remains unchanged, and the `AGENT` role uses bold terminal-default foreground styling without background detection. |

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

These are the only semantic view components. #82 implements their immutable,
package-private models and deterministic rendering. Delivery slices may compose
them into public views but must not introduce an additional component kind
without first revising this contract.

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

#82 implements that ownership boundary. Its pure line-input transition handles
the established insertion, deletion, cursor, Enter, Ctrl-C, and EOF behavior.
#85 adds the pure main-input/history transition and one bounded store to each
stream-backed driver. #86 adds pure composer-line and confirmation-answer
transitions plus a history-free semantic input route. #79 composes canonical
grammar projection with the main transition and semantic palette projection.
#87 adds a separate generic picker state, transition, bounded viewport, and
dedicated history-free driver route; it does not reuse editable-line or main
completion state. #93 consumes that route for `/settings` and adds one
history-free cancellable prompt route through the same sole driver.
Up/Down navigate history only on capable main reads with a closed palette; an
open palette consumes them for highlight navigation. Up/Down, Tab, Shift-Tab,
and Escape remain no-ops for composition and confirmation. Shift-Tab remains a
main no-op.

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

The following table is the complete interaction contract. The existing
one-line editing, semantic composition and confirmation controls, #85
main-command history, #79 selectable palette, and package-internal #87 picker
foundation are implemented. #93 invokes the picker and cancellable prompt
through package-internal seams.

| Focused control | Navigation and editing | Commit | Cancel or terminal event |
| --- | --- | --- | --- |
| Main one-line input | Text, Backspace/Delete, Left/Right, and Home/End edit. Up/Down navigate history only while no palette is open. Slash-prefixed input may open a palette; Tab applies its highlighted completion. | Enter, or configured VEOF with a nonempty buffer, submits the exact visible buffer to the existing parser once. | Ctrl-C, configured VEOF on an empty buffer, and physical EOF retain their established typed outcomes; no text is parsed. |
| `SuggestionPalette` anchored to main input | Opening highlights the first eligible candidate without editing. Up/Down moves the highlight among eligible candidates without replacement; Tab applies the highlighted candidate to the applicable span. Shift-Tab has no palette action. | Enter submits the current visible buffer, never an unapplied highlighted candidate. | Escape closes the palette and restores the prefix captured before the first completion replacement. |
| `Picker` | Up/Down changes highlight among enabled items. Highlight never selects or commits. | Enter selects and commits the highlighted enabled item. | Escape cancels. Ctrl-C and EOF leave without commit and retain the invoking API's typed semantics. |
| Settings `Prompt` | Text and ordinary line-edit keys edit one history-free value. Completion keys have no effect. | Enter commits the exact buffer for field validation. | Escape returns to the parent without changing the pending value; Ctrl-C and EOF cancel the whole settings workflow. |
| Line-oriented composer | Each ordinary line appends exact content. Up/Down, Tab, Shift-Tab, and Escape are no-ops; no full-screen editing or command-history traversal is introduced. | `/send` commits the exact LF-joined content when nonblank. | `/cancel`, Ctrl-C, and EOF preserve established discard outcomes and counts. |
| Confirmation | Existing accepted yes/no input and blank-default behavior remain authoritative. Up/Down, Tab, Shift-Tab, and Escape are no-ops. Invalid input marks the choices as erroneous, writes one hint, and asks again. | A valid answer or blank default returns the established `yes` or `no` result. | Ctrl-C and EOF return the established `interrupt` or `eof` result. |

`/say /send` and `/say /cancel` in composition continue to append literal
`/send` and `/cancel` lines. The composer remains line-oriented; preview,
highlight, and history behavior supplied by contextual suggestions must not
change `/send`, `/cancel`, `/say`, Ctrl-C, or EOF semantics.

### Main-command history

> **Implemented by #85:** history is available on capable stream-backed main
> reads. The ordinary-line fallback records eligible commits but does not
> provide live arrow navigation; a later capable read on the same driver can
> recall them.

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

> **Implemented by #79:** capable main reads provide deterministic selectable
> root, subcommand, and finite fixed-option completion without changing parser,
> runtime, history, fallback, or public API contracts.

A leading slash automatically opens `SuggestionPalette` at a recognized
grammar position. Root candidates are the 21 main-available command tokens in
registry order. Help offers those 21 plus `/compose`, `/send`, and `/cancel`;
agent-valued positions expose the four preferred prompt aliases—`lead`, `plot`,
`character`, and `execution`—in route order. The parser continues to accept all
ten declared route aliases. Other implemented finite positions expose their
static subcommands or options. Runtime values such as session, conversation,
debate, query, prompt, subject, and file-path text are never candidates.
Fixed-only positions retain an open empty palette when a prefix has no match;
mixed fixed/free positions close the palette for a free-value slot.

Candidate and active-token matching are exact and case-sensitive. ASCII space
and tab alone delimit the cursor-aware replacement span. Completion replaces
the full active token, including its suffix when the cursor is in the middle,
and inserts no whitespace. Help accepts a bare active token for slash-prefixed
replacement, so `/help ru` may complete to `/help /run`. Filtering, opening,
empty results, highlight, replacement, and dismissal are deterministic pure
transitions.

Opening the palette highlights the first eligible candidate deterministically
without changing the editable buffer. While the palette is open, Up and Down
move the highlight among eligible candidates and clamp at the first and last
candidate; they do not replace text or traverse history. Tab applies the
highlighted candidate by replacing the applicable buffer span. Shift-Tab does
not navigate or apply the palette. Applying a highlighted candidate is
completion only, not parser or runtime commit. Tab is a no-op for an empty
palette, a palette without a highlight, or an already exact replacement. The
first changing Tab retains the full pre-completion buffer and cursor snapshot;
edits within the same grammar position keep that snapshot, while a grammar
position change resets it. Escape restores the retained snapshot when one
exists, closes the palette, and latches that exact line state so it stays
dismissed until an edit or history change. Without a snapshot it leaves the
current buffer unchanged. Enter always commits the visible buffer through the
existing parser once and never implicitly applies an unapplied highlighted
candidate. When the palette is closed, Up/Down resume older/newer history
traversal. Recalled text remains editable and may reopen a palette based on its
current buffer.

Fixed command arguments are palette candidates only when the existing grammar
exposes a finite canonical set. They do not add parser rules, authorization,
runtime actions, or fuzzy matching. Confirmation choices remain owned by the
confirmation control rather than the main-command palette. Finite choices
include the preferred agent prompt aliases for agent-target positions when
exposed by canonical command-route metadata; all ten declared route aliases
remain parser-compatible, and the palette does not duplicate those aliases or
otherwise duplicate parser grammar.

### Picker semantics

> **Implemented by #87 as a package-internal foundation and consumed by #93:**
> picker state, controls, rendering, driver input, callback adaptation, and
> terminal outcomes remain internal. The installed `/settings` workflow uses
> them without adding a supported public picker API.

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

Commit and Escape render one final `completed` or `cancelled` frame, with the
chosen item both selected and highlighted on commit. Interrupt, EOF, and active
failures clear the transient display without a synthetic terminal-state frame.
The sole driver restores captured attributes after normal and exceptional live
outcomes. Picker reads never consult or mutate main history or completion state,
parse a command, or call runtime, provider, filesystem, persistence, subprocess,
storage, or network code.

### Persisted Codex defaults

> **Implemented by #94:** `config/system_defaults.json` has a closed five-key
> schema: required `language` and `persona`, optional
> `max_debate_rounds`, and optional `codex_model` and
> `codex_reasoning_effort`. New workspaces include null Codex values; existing
> valid three-key files remain valid and initialization preserves their exact
> bytes.

A non-null explicit `AgentBackend(model=...)` or CLI `--codex-model` wins over
all persisted model values. Otherwise each agent's optional values in
`config/agent_configs.json` inherit the corresponding workspace system default
and then the provider default. No reasoning CLI or constructor override is
added. Model text is preserved without trimming or catalog lookup and must be
nonempty, not start with `-`, and contain no Unicode control, format, or
surrogate character. Reasoning is null or exactly `minimal`, `low`, `medium`,
`high`, or `xhigh`; actual `xhigh` support is model/provider-dependent. The
complete system-default and agent-configuration pair is validated and
snapshotted at backend construction; later saves apply only to the next
backend.

Every backend validates the complete persisted schema before applying an
explicit model and snapshots both effective values at construction. The
optional `--model MODEL` and
`--config model_reasoning_effort="LEVEL"` Codex arguments occur in that order
immediately after `exec` and before any `resume` marker. They do not alter
agent definitions, fingerprints, bindings, provider-thread identity, lifecycle
artifacts, or request payloads. Editing the file affects only the next backend
construction. #94 itself added no save API, `/settings` route, or
active-backend refresh; #93 adds the internal interactive workflow while
preserving those public and lifecycle boundaries.

### Settings panel

> **Implemented by #93:** global alias-free `/settings` provides this
> interaction and guarded persistence workflow without adding a supported
> settings/save object API. #87 supplies the generic internal picker foundation,
> and #94 supplies the validated workspace Codex fallback model/reasoning
> fields.

The settings view edits two persistent documents through validated Current and
Pending configuration snapshots: the `config/system_defaults.json` defaults
described by
[Agent configuration](../reference/configuration/agent-configuration.md), and the existing
per-agent provider, model, and reasoning selections in
`config/agent_configs.json`. The system snapshot contains required `language`
and `persona`, optional `max_debate_rounds`, and the optional workspace Codex
fallback model/reasoning values. Provider, model, and reasoning selection is
per agent. A successful save writes the canonical five-key system object and
the canonical agent object. The view does not change identity, thread
ownership, configuration precedence, or either implemented schema.

The presentation uses one `Panel` with `Section`, `Field`, and `Notice` values,
plus one focused `Picker` or `Prompt`. It is not a new component or a
general-purpose form abstraction. One section shows validated current values;
another shows independent pending values, and the active control renders with
that context. Any implementation need for a new semantic component must first
revise the closed vocabulary through the repository's modularity process.

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
| Providers | A nested agent picker follows canonical `ROSTER` order, then a Provider/Model/Reasoning effort picker edits only that agent's pending `agent_configs` values. Codex is enabled. Reserved Gemini is visible but disabled for new selection; a pre-existing Gemini value remains visible and may be preserved or changed to Codex for remediation. The UI neither invents providers nor changes roster identity. |
| Agent Model | A nested free-text `Prompt` uses the implemented safe-model validator; blank stores null and inherits the workspace system model. It performs no network lookup and does not offer a model catalog. |
| Agent Reasoning effort | A nested picker offers Inherit, `minimal`, `low`, `medium`, `high`, and `xhigh`; Inherit uses the workspace system reasoning value, then the provider default. The UI does not add a CLI reasoning flag. |

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
- Enter on Save validates both complete pending snapshots, then re-reads both
  persisted documents and the persona catalog before any write. Any exact-byte
  or catalog change from the validated opening baseline fails closed with no
  write. A valid, unchanged baseline crosses the sole mutation boundary: each
  changed file is replaced atomically in system-then-agent order, and any write
  failure triggers best-effort reverse-order rollback to the exact original
  bytes of every attempted target, including the target that reported failure.
  Success reports `completed`. Validation, conflict, replacement, or rollback
  failure reports `error` and retains the pending values for retry or
  cancellation. This is a guarded two-file update, not a global transaction or
  a power-loss-safe transaction across both files.
- Enter on Cancel, Escape from the top-level settings picker, Ctrl-C, or EOF
  returns `cancelled` without writing. Opening, navigating, highlighting,
  editing, validation preview, nested cancellation, and rerendering likewise
  have no filesystem, runtime, provider, storage, or configuration effect.

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
for the existing `Picker`. Capable real CLI streams may therefore show their
structured fixed-ANSI form, while `NO_COLOR` retains the same semantic text.
Plain line fallback owns no main palette or completion keys; internal picker
input instead uses its complete numbered fallback contract. Neither the
controller nor the terminal composer has an internal generic `write_status`
call site.

| Profile | Selection | Output and input behavior |
| --- | --- | --- |
| Capable interactive ANSI | Real CLI with trusted TTY capability and non-`dumb` `TERM`; `NO_COLOR` absent for decoration | Structured layout plus fixed foreground/emphasis SGR. Safe functional cursor controls may support the focused live control. |
| Capable interactive plain | Same interactive capability with `NO_COLOR` present | The identical semantic structure without decorative SGR. Safe functional cursor controls may remain because `NO_COLOR` governs color, not editing capability. |
| Plain line fallback | Non-TTY, `TERM=dumb`, missing capability, or any uncertain capability result | Structured plain records; semantic composition/confirmation prompts pass once through the ordinary line callback. Internal picker reads use complete numbered prompts and exact line grammar. No raw mode, cursor movement, live overlay, or terminal mutation. |

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

- only fixed foreground colors and emphasis may be decorative SGR; the `AGENT`
  role uses bold with the terminal-default foreground;
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

## Compatibility contract

Human interactive layout may intentionally change as its owning migration
slice lands; #83 and #84 have changed their migrated output groups in that
permitted way. The following contracts may not change as a side effect.

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
result types, constructor, and method signatures remain exact.

#79 adds package-private completion candidates/projections and main palette
state plus the unlisted facade bindings `transition_main_command_input` and
`read_main_command_line`. The model values are not `Component` members; only
their projected existing `SuggestionPalette` is rendered. These names remain
outside public `__all__` and the root facade. The established parser,
`InteractiveTerminal`, and line-editor public results and signatures remain
exact.

#87 adds package-private `PickerControlState` and `PickerViewport` contracts
alongside the existing semantic `Picker`, plus the unlisted facade identities
`Choice`, `Prompt`, `ControlOutcome`, `ControlOutcomeKind`, and
`InteractivePickerInput`. `PickerControlState` and `PickerViewport` remain
outside the closed `Component` union. `InteractivePickerInput` is a
resource-free internal adapter over an `InteractiveTerminal`; its
`read_picker()` method is not supported public API. No root export, public
`__all__` entry, existing signature, or direct application consumer changes.

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
| `interactive_terminal.__init__` | Compatibility facade with the exact existing public surface plus package-internal stream construction, semantic output, picker-adapter, and cancellable-prompt seams. It owns no import-time resource or state. |
| `interactive_terminal._model` | Immutable semantic components, bounded main-history/control state, main-command completion/palette state, composer and picker control state, bounded picker viewport, semantic input/render frames, key/focus values, and transition/render results. No I/O. |
| `interactive_terminal._controls` | Pure established line-input, main-command/history, composer-line, confirmation-answer, cancellable-prompt, and picker transitions; bounded history; completion projection; picker initialization/reconciliation, disabled-skipping live navigation, exact fallback grammar, and viewport selection. |
| `interactive_terminal._render` | Pure safe responsive output and semantic input renderers for plain and fixed ANSI profiles, including bounded live and complete-list fallback picker projections and the strict JSON agent-reply presentation projection. It sanitizes dynamic content before styling and returns render data without writing. |
| `interactive_terminal._driver` | Sole owner of raw streams, capability/width detection, incremental byte/key decoding, fixed Escape readiness wait, cursor controls, raw mode, restoration, writes, flushes, and one bounded process-local main-history store per instance. Its supported main, history-free semantic prompt, and dedicated picker routes accept call-local collaborators; settings input does not consult history or completion state. |
| `interactive_terminal._core` | `InteractiveTerminal`, established public-operation orchestration, semantic composer/confirmation view construction and event mapping, canonical command-completion adapter, package-internal stream factory, semantic render seam, resource-free `InteractiveSemanticOutput`, and unlisted resource-free `InteractivePickerInput` and `InteractivePromptInput`. It binds picker and cancellable-prompt transitions to the sole driver without owning another store/raw implementation. |
| `interactive_line_editor` | Standalone/public compatibility-only facade over the same supported main-command reader and one per-editor driver/history store. |

The allowed graph is:

```text
cli -> interactive_terminal package-internal stream factory -> terminal._core
interactive_controller._core -> interactive_terminal facade components/adapter
interactive_controller._core -> interactive_settings -> agents/config/errors/workspace
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

The controller core imports only the terminal facade; it composes typed output
and sends each established semantic component through the unlisted adapter.
The runtime-policy leaf returns frozen, validated inspection facts rather than
presentation strings. Other policy leaves use only the facade's error
contracts for their terminal dependency. Controller modules do not import
terminal package leaves. `terminal._controls`,
`terminal._render`, and `terminal._driver` share contracts through
`terminal._model`; they do not reach through siblings for private state.
`terminal._core` consumes canonical command, route, and roster metadata from
`interactive_commands` and `agents`. The controller alone coordinates settings
focus and presentation; `interactive_settings` owns immutable candidates and
persistence without importing the controller or terminal. No terminal package
leaf imports CLI, controller, runtime orchestration, providers, or storage.

### Implemented module-index delta

The current [Python module index](../../PYTHON_MODULE_INDEX.md) records 33
importable production units after #93's settings-module addition:

```text
former interactive_terminal module                    -1
interactive_terminal package and five package leaves  +6
interactive_settings                                   +1
current total                                          33
```

The six entries are the package plus `_model`, `_controls`, `_render`,
`_driver`, and `_core`. The existing `interactive_line_editor` and `cli`
entries now describe compatibility delegation and unified factory construction;
the #85, #79, and #87 reconciliations add history, completion, and picker
responsibilities and tests to existing entries without adding an importable
unit or dependency edge. #93 adds only `interactive_settings` and its approved
controller-to-settings and settings-to-agents/config/errors/workspace edges.
Controller policy leaves still consume only facade error contracts.

## Migration coverage

Issue #82 delivered the common models, renderer/capability profiles, driver
seam, package facade, and module-index reconciliation. Issues #83 and #84
delivered their output rows below without redistributing runtime or storage
responsibility, #85 delivered main-command history, and #86 delivered semantic
composer/confirmation controls within the existing driver/control boundary.
#79 delivered selectable main-command completion within that same boundary.
#87 delivered the generic package-internal picker foundation within the same
boundary. #94 delivered persisted Codex model/reasoning defaults through the
existing workspace, configuration, and backend units without changing that
graph. #93 installs `/settings`, adds one internal settings module, and brings
the graph to 33 units.

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
| Advisory turns | `Message`, `Notice`, `Field` | Routing, waiting/loading, result, cancelled/completed/error | #83 (implemented) |
| Retrieval progress and summaries | `Notice`, `Record`, `ListView`, `Field` | Loading, citations/context accounting, result/error/cancelled | #83 (implemented accepted views) |
| Consensus progress and summaries | `Panel`, `Section`, `Notice`, `Record`, `Field` | Loading, debate identity, result/cancelled/completed/error | #83 (implemented) |
| Interactive errors and recovery hints | `Notice`, `Field` | Trusted code, redaction, error state, next action, no raw diagnostics | #83 (implemented) |
| Settings current/pending view | `Panel`, `Section`, `Field`, `Prompt`, `Picker`, `Notice` | Exact top-level order; two current/pending snapshots; disabled provider, validation, error, cancelled, and completed meaning; guarded two-file Save boundary | #93 (implemented) |

### Control groups

| Control group | Semantic construction | Interaction owner | Delivery issue |
| --- | --- | --- | --- |
| Main one-line input and editing | `Prompt`, optional `SuggestionPalette` | Unified key/focus state, editable buffer, selectable completion/history coordination, parser-once commit | #85 and #79 (implemented) |
| Process-local main history | Main `Prompt` buffer state | Up/Down, 100 exact nonblank entries, draft/cursor restoration, non-main isolation | #85 (implemented) |
| Line-oriented composer | `Field`, `Notice`, `Prompt` | Ordered context, exact line collection, fixed preview, and semantic open/error/completed/cancelled events | #86 (implemented) |
| Confirmation | `Field`, `Prompt`, `Notice` | Ordered question/choices/default, invalid retry, no suggestions, and typed terminal events | #86 (implemented) |
| Main command/subcommand palette | `SuggestionPalette`, `Prompt`, `Notice` | Slash-open, Up/Down clamped highlight, Tab full-token replacement, Escape snapshot restoration and dismissal | #79 (implemented) |
| Fixed command values | `SuggestionPalette`, `Field` | Canonical finite grammar choices, including metadata-exposed agent IDs/aliases; palette activity has no effects beyond call-local input state | #79 (implemented) |
| Single-select and selectable-list choices | `Picker`, `ListView`, `Record`, `Notice` | Disabled-skipping clamped Up/Down highlight, Enter commit, Escape cancel, typed interrupt/EOF, bounded live/full fallback rendering, empty/disabled/error | #87 (implemented foundation; `/settings` consumer added by #93) |
| Settings field and action navigation | `Panel`, `Prompt`, `Picker`, `Field`, `Notice` | Single focus; nested agent/provider and value pickers; text/numeric editors; explicit Save/Cancel; no write before guarded Save | #93 (implemented) |

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
lifecycle snapshot contract. #94 covers exact five-key and legacy schema
handling, safe model and fixed reasoning validation, full-schema-first failure,
model precedence, backend-lifetime snapshots, exact fresh/resume invocation
arguments, initialization preservation, and unchanged definition/thread/public
contracts.

| Area | Required evidence |
| --- | --- |
| Capability profiles | Representative plain, fixed-ANSI, `NO_COLOR`, narrow, non-TTY, `TERM=dumb`, missing/uncertain and independent input/output capability combinations, 80-column fallback, and 120-column cap cases. |
| Rendering safety | Hostile ESC/OSC/control/bidi-format content is visibly sanitized before ANSI; local resets prevent bleed; removing decorative SGR exactly recovers the plain structure; ANSI is zero-width; wide/combining content and cursor positions use physical columns. |
| Semantic output | Every view group uses only the closed vocabulary; status remains understandable without color; one semantic record uses one callback write unless an explicit live-control exception is tested. |
| Agent reply presentation | Complete strict JSON object/array formatting only; two-space indentation; preserved key order and Unicode; unchanged fallback for malformed, embedded-prose, scalar, duplicate-key, non-finite, or unserializable content; direct/retrieval/transcript/history presentation only; stored/provider bytes unchanged; bold terminal-default `AGENT` styling with no background detection. |
| State transitions | Pure tests cover idle/focused/open/highlighted/selected/disabled/loading/empty/error/cancelled/completed states and invalid or boundary transitions. |
| History | Exact whitespace-preserving commits including invalid commands, duplicates, `/quit`, fallback, and nonempty VEOF; blank/interrupt/empty-VEOF/physical-EOF/failure exclusion; 100-entry oldest eviction; older/newer bounds; exact draft/cursor restoration; editable copies; per-driver isolation; non-main exclusion; no persistence; and post-restoration append. |
| Palette/history coordination | Slash opening with deterministic initial highlight and unchanged buffer; filtering; empty/error; canonical metadata projection including finite agent IDs/aliases; open-palette Up/Down clamped highlight navigation without replacement or history movement; Tab application of the highlighted candidate; Shift-Tab no-op; Escape full-snapshot restoration and exact-state dismissal; closed-palette Up/Down history; edited recall; and visible-buffer-only Enter commit. |
| Picker | Highlight-versus-selection, disabled-skipping clamped navigation, Enter commit, Escape cancel, typed interrupt/EOF, empty/all-disabled/one/long lists, exact fallback acceptance/rejection, stable identity reconciliation, selected-outside-window summary, one final completed/cancelled frame, cleanup on interrupt/EOF/failure, and resize on each live redraw. |
| Settings | Two-snapshot current/pending separation; exact top-level/language/reasoning/roster order; safe persona precedence and lexical order; numeric `0`, positive, blank, negative, fractional, and nonnumeric cases; Codex-enabled/reserved-Gemini-disabled provider behavior including preservation/remediation; implemented safe model text and blank/null provider default; exact reasoning enumeration with model/provider-dependent `xhigh` support; one focused control and deterministic focus return; nested and whole-panel cancellation; exact-byte and catalog conflict before writes; changed-only canonical system-then-agent save; per-file atomic replacement; failing-target-inclusive reverse exact-byte rollback and rollback uncertainty; pending retention; no write on open/edit/highlight/error/cancel; next-lifecycle-only application with higher live overrides; plain/ANSI/`NO_COLOR`/narrow/line fallback; hostile display content; and unchanged identity, thread, precedence, public, and machine contracts. |
| Parser and mutation boundary | The exact visible committed main buffer reaches the established parser once; previews, recall, replacement, highlight, rerender, cancel, Ctrl-C, empty VEOF, and physical EOF cause no parser/runtime/storage call. |
| Composer/confirmation | Ordered semantic input facts and focused prompt; equivalent width-aware plain/ANSI rendering; fallback-once and trusted-live routing; exact `/send`, `/cancel`, `/say`, yes/no/default, invalid retry, Ctrl-C, and EOF results; semantic composer events and hint cardinality; history isolation; reserved-key no-ops; no full-screen or persistent state. |
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
