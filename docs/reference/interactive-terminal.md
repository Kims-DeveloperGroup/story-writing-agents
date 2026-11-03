# Interactive terminal component

The terminal component renders sanitized semantic records and collects
TTY/fallback input. The current controller uses it for startup,
authentication/settings, session/conversation management, local inspection,
pickers, confirmations, read-only viewers, and ordinary Lead conversation.

<a id="public-api"></a>
<a id="composition-live-suggestions"></a>

## Public facade

The `story_writing_agents.interactive_terminal` package exports:

```text
AttachmentKind
ComposeMode
ConfirmationResult
TerminalActionKind
TerminalMessageKind
InteractiveErrorCode
TerminalAction
InteractiveErrorSpec
INTERACTIVE_ERROR_SPECS
InteractiveError
sanitize_terminal_text
InteractiveTerminal
```

`InteractiveTerminal` defines semantic reads/writes used by the controller,
including `read_main`, subject/confirmation input, lobby/session/conversation
orientation, catalog/search/transcript rows, roster/status/help/error output,
and agent-targeted CHAT composition plus result-presentation seams. A method's
presence on this generic component does not make it a current shell command.
The controller does not expose a direct story-run entry point.

The retained public methods are:

| Purpose | Methods |
| --- | --- |
| Read main/management input | `read_main`, `request_conversation_subject`, `request_confirmation` |
| Render scope and management data | `write_lobby_orientation`, `write_session_orientation`, `write_conversation_scope`, `write_conversation_list_entry`, `write_conversation_search_hit`, `write_conversation_transcript_event`, `write_agent_roster`, `write_status`, `write_help`, `write_error` |
| Render durable collaboration | `write_collaboration_events` with an ordered event tuple, optional restart cursor, and optional record details |
| Agent-targeted conversation composition | `compose` with `ComposeMode.CHAT` and an agent ID |
| Result presentation, without a direct-run controller entry | `write_agent`, `write_run` |

`ComposeMode` contains only `CHAT`. There is no RUN composition mode, `/run`
command, or noninteractive `story run` subcommand. Users converse with the Lead;
automation uses the stepwise Python collaboration API described in the
[runtime API](runtime-api.md).

For argument and return types, inspect the public class signature; concrete
streams remain implementation details. Session/conversation command behavior
is documented in [Story sessions](../guides/story-sessions.md), and retained
provider-exchange inspection is documented in
[Agent exchanges](../guides/agent-exchanges.md).

Concrete stream/contextual builders, pickers, text viewers, external viewers,
semantic record types, and transition functions are package-internal
implementation details unless another reference explicitly names them.

## Durable collaboration presentation

`write_collaboration_events(events, after_sequence=N, show_details=False, history_events=None)`
validates that every event belongs to one session/conversation and arrives in
strict durable sequence order before writing anything. It skips sequences at or
before `N`, writes one semantic panel for each later event, and returns the last
rendered sequence. Reusing that cursor after restart therefore cannot duplicate
an already presented event.

Every proposal panel includes the exact proposal ID/revision/digest, purpose,
complete specialist assignments, `${path}` or `${path}#Lx-Ly` source
indicators, user impact, closed side effects, and bounded round/call cost.
Confirmation, artifact application, and local Git commit panels retain their
own exact request identities and explicitly state what the confirmation cannot
authorize. Artifact operations show paths, digests, hunk counts, and bounded
byte counts—not source or replacement bytes. Local commit results never imply a
paired remote push. The prepared commit panel says `Git index: not staged`;
staging exists only inside the separately confirmed atomic local-commit
operation, so there is no misleading standalone staged state between Apply and
Commit.

`show_details=True` adds event, turn, conversation, batch, and timestamp facts.
It never removes the semantic content shown by compact mode. Plain, fixed-ANSI,
`NO_COLOR`, narrow, and fallback output all use the same semantic component
tree, so color and terminal capability cannot change meaning. Unknown payload
types, mixed scopes, and duplicate or out-of-order sequences fail closed before
output.

The optional `history_events` supplies the complete validated canonical call
context when the displayed events are a bounded selection. This permits exact
usage totals without estimating from a partial window. Detailed history retains event/turn/sequence identity independently of Message
details. The terminal opens no history storage itself.

The pure facade function `project_conversation_messages(events, *,
history_events=None)` returns `(source_event, Message)` pairs, one per visible
response. Full historical context supplies exact usage and finalization
relationships; the source event retains its sequence for paging. It opens no
storage, invokes no provider, and grants no historical confirmation authority.

The controller restores the latest 20 user, Lead, and specialist messages after
a conversation focus change, following scope orientation and before the next
input prompt. Startup restoration, session switches, and archive-successor
selection use the same path. Selection uses the full session/conversation
identity, so renaming or selecting the current focus does not repeat history.
The controller validates one complete snapshot, projects messages before
selecting its suffix, and supplies full historical context for exact response
attribution and deduplication. It reports `Restored N of M messages.` Truncated
windows include `/transcript` guidance; `/history` retains detailed event
inspection. Empty or diagnostics-only histories produce no restoration output.
A handled read failure or interruption is not retried at every prompt.
Restoration neither calls providers nor executes historical confirmation controls.

## Message frames, Markdown, and response metadata

Live conversation, saved history, transcripts, and search excerpts use distinct
speaker-aware message frames. A message body is separated from its labeled
metadata footer by a divider. Full frames use the shared Box presentation with
rounded Unicode corners and continuous borders, reusing the existing
provider-output viewer's box geometry. Proposal disclosures, confirmation
identities, Apply/Commit controls, and recovery guidance remain complete outside
compact response metadata. The [confirmation presentation](#confirmation-decisions) reuses this geometry without turning its controls into speaker messages.

Frames are left-aligned and use
`min(120, max(1, terminal_columns - 1))` physical cells. The complete layout
starts at 40 available frame columns; smaller terminals use compact decoration
and stacked fields that fit the window. Compact messages use a speaker header
and `│` body rail; the rail is omitted below three cells. Width is observed
before new output and live redraws. The inline conversation timeline retains
complete semantic messages and reflows them on resize. Line-fallback output
remains ordinary terminal scrollback.

User headers and borders use magenta, Lead uses cyan, and specialists retain
their role accents. Metadata labels/dividers use blue, with subdued values.
Context, status, prompts, commands, and selected controls use semantic accents.
Labels and frame structure retain the same meaning without color.
Plain and `NO_COLOR` output preserve the Unicode box without ANSI decoration.

Message prose supports headings, emphasis, lists, quotations, visible link
destinations, inline code, and fenced code. Code whitespace and strict JSON
presentation remain literal. HTML, images, unsupported syntax, and parser
failures remain readable as sanitized text. Source text stored in conversation
history is unchanged.

Response metadata includes outcome, measured elapsed time when available, and
input/output tokens reported by the provider's response usage object. Counts
include distinct submitted calls for that response's exact invocation, including
corrective retries; specialist replies show their own invocation's usage.
Additional per-call public responses show the individual call's usage.
Each footer identifies its usage scope as `physical call` or
`invocation (N calls)`. A provider response keeps that call's acceptance outcome;
the host's final response keeps its own workflow outcome and invocation total.
For example, a last call can show measured tokens and `accepted` while the final
question shows `unavailable` because an earlier call cannot be attributed.
Missing or ambiguous counters display `unavailable`, while measured zero remains
`0`. Counts are never estimated from message text or payload bytes, and cached
input is not added to the provider's reported input count. Older records without
usage remain readable. The existing Message details setting expands diagnostics.

Illustrative output and counts:

```text
╭─ Lead ───────────────────────────────────────────────────────────────╮
│ Review workflow                                                      │
│ 1. Ask Lead to propose a review.                                     │
│ 2. Inspect the scope, then choose Confirm.                           │
├─ Metadata ───────────────────────────────────────────────────────────┤
│ Outcome: answer | Elapsed: 00:00:03                                  │
│ Usage scope: invocation (1 call)                                     │
│ Input tokens: 128 | Output tokens: 64                                │
╰──────────────────────────────────────────────────────────────────────╯
```

## Confirmation decisions

Shell binary questions, settings Apply/Discard, Lead preparation and specialist
proposals, reviewed artifact Apply, local Commit, and revision input share the
message frame's rounded borders, width, padding, and compact behavior. The
canonical rules are in the [design-system confirmation contract](../design/interactive-cli-design-system.md#confirmation-presentation-and-decisions).
The internal adapter is not a new supported Python API: public
`request_confirmation(message, default=...)` retains its legacy yes/no,
explicit blank default, typed outcomes, and reserved-key behavior.

The shell frame groups the actual action summary, specialist assignments when
present, cost/impact, decision choices, and `KEYS`. Numbered region labels and
bold headings supplement separators. Specialist `[P] PLOT`, `[C] CHARACTER`,
and `[E] EXECUTION` markers use the same yellow, green, and blue identity
accents as their messages, with a short connected role rail. Long descriptions
and paths retain ordinary body color. Plain/NO_COLOR retains the markers,
roles, rails, region names, and active `>`; color never establishes authority
or success.

| Confirmation | Initial shell choice | Exact authorized effect |
| --- | --- | --- |
| Session resume/pause, current-conversation archive | NO/return | The named management operation only |
| Settings Apply/Discard | NO/return | Apply or discard the current editing candidate only |
| Lead preparation | Unselected | The disclosed bounded preparation only |
| Specialist work | Unselected | The disclosed assignments and synthesis only |
| Apply | Unselected | The exact reviewed artifact candidate only |
| Commit | Unselected | The exact local commit candidate only |

Use Up/Down to choose and Enter to accept. Initial Enter on an unselected action
shows one hint and starts nothing. Esc leaves a proposal unconfirmed; Reject
records its rejection. Revise opens the revision editor; whitespace-only input
shows one error in that editor, and Esc returns to the same pending choices
with Revise focused. Ctrl-C/EOF preserve the caller's existing terminal/exit
semantics and never create approval.

A three-reviewer layout reads as follows. Counts here are illustrative; actual
values always come from the validated request:

```text
[01 SUMMARY]
Same immutable candidate · 3 parallel reviews → Lead synthesis
[02 SPECIALISTS · 3]
[P] PLOT       causal structure · up to 2 calls
[C] CHARACTER  character logic · up to 2 calls
[E] EXECUTION  narrative rules · up to 2 calls
[03 COST & IMPACT]
[COST] Up to 7 additional calls: specialists 6 + Lead 1
[SCOPE] Provider review/synthesis only
[SEPARATE APPROVAL] Apply and Commit each need a new decision.
[04 DECISION · Pending]
  1. Confirm
  2. Reject
  3. Revise
KEYS  Up/Down: choose · Enter: select · Esc: leave unconfirmed
```

Each role's complete task, references/ranges, modes and bounds remain disclosed.
Only final candidate review claims a shared immutable candidate; preparation
and drafting retain their actual different assignments. There is one whole
proposal decision, rather than per-role checkboxes. Revise requests a fresh
valid proposal when scope changes.

F6 reviews complete current instructions, sources, premises, costs and exact
request/proposal identities without deciding. Message details does not hide
required approval information. Closing review returns to unchanged input.
Very short terminals show a dense role/task/call summary and retain full
disclosure in the active conversation; if safe complete decision controls
still cannot fit, the shell asks for resize or Esc and blocks execution.
Below 40 available frame cells, the presentation uses the message-style compact
header/rail; already printed native scrollback is not rewritten on resize.

Line fallback prints the same facts and current grammar. Binary shell questions
accept current ordinals or y/yes/n/no and default to NO. Action decisions accept
current ordinals or their exact scoped tokens: confirm/reject/revise,
apply/reject/revise, or commit/reject/revise. Trim/casefold applies only to this
internal confirmation route. Empty action input retries without deciding;
`/cancel` leaves without a decision. A specialist/Apply/Commit prompt does not
interpret `yes` as approval, and ordinary main-prompt text remains Lead input.
Generic picker and public legacy confirmation grammars remain unchanged.

Settings comparison includes changed Current/Pending values and exact
current-shell/next-shell activation guidance in capable and line-fallback
modes. NO/Esc retains pending edits. Capable confirmation frames clear on
return and failure; fallback leaves its ordinary printed records visible.
Confirmation input is not a new User message or main-history entry.


<a id="startup-welcome-and-authentication"></a>
<a id="main-prompt-live-suggestions"></a>

## Main prompt

The prompt reflects management scope:

```text
story[lobby]>
story[SESSION]>
```

Each rendered prompt ends with one ASCII space after `>`; the code block omits
that invisible trailing cell.

An agent suffix is shown only when the controller supplies a real active agent
identity. Ordinary input still routes to the Lead without requiring a suffix
or selector. Supplying an agent without a session is invalid component use.

Capable terminals provide exact command completion for the retained parser
grammar. Fallback streams use ordinary line reads. Submitted management input
is recorded according to the terminal's bounded main-input history rules;
picker answers and sensitive settings/authentication input remain isolated.

In a capable terminal, pressing Enter on empty or Unicode-whitespace-only
main input clears the field and keeps the prompt and cursor in place. A single
inline alert below the input says `Enter a message or slash command.` Repeated
Enter replaces that alert without submitting input or adding history. Editing
the buffer or recalling history clears the alert. Enter still accepts a
highlighted suggestion before attempting submission.
This rule applies only to main input; multiline composition retains blank and
whitespace-only lines.

Alerts fit the available terminal rows; clipped feedback ends with an ellipsis.

Submitted command text clears after success or failure. Recoverable command
errors, including invalid syntax, missing session/conversation prerequisites,
and top-level `/auth` failures, appear below the next empty prompt as inline
alerts. Successful commands retain their normal output without an alert.
Nonblank submitted commands remain available in history, including failed
attempts. Detailed provider/workflow results and nested settings diagnostics
keep their existing presentation.

Fallback input shows equivalent notices as ordinary text and re-prompts. It
does not erase terminal echo or promise a fixed physical cursor position.

## Command input language

On supported local macOS and Linux desktop terminals, a leading `/` selects an
available English OS input source before the next input character is read.
The command keeps that selection through spaces and completion. Submitting or
cancelling the input, or removing its leading slash, returns to the shell's
default input source. Completing a suggestion with Enter or dismissing its
palette with Escape is not itself a submission or cancellation.

The default is resolved once when the shell's input session starts: an
authoritative OS default is preferred; otherwise the source active at startup
is remembered for that shell run. Restoration is therefore not fixed to Korean
and does not capture a new default before every command. A shell restart
captures a new startup fallback. The `/settings` Language value continues to
configure agent responses, rather than selecting the keyboard input source.
Selecting a source already active is skipped. You can still change the source
manually within a command, for example to enter a Korean search term; the shell
does not force English again after every key.

This changes OS source selection, not received characters. Pasted text,
arguments, history, and ordinary messages remain literal. Input-source control
is released before native authentication or a borrowed view takes input and is
reconciled when main input resumes. EOF, interruption, input failures, and shell
exit also attempt to restore the default without replacing the original error.

macOS uses the native input-source service. Linux uses an available IBus or
Fcitx5 installation; the selected framework, English source, and restore target
must be identifiable. Fcitx5 additionally requires a readable configured group
whose first keyboard has a recognized US/GB layout, so temporary direct input
can preserve the remembered non-English engine. Input-source selection does not
reproduce an IME's unfinished composition or every internal conversion mode.
Non-TTY and line fallback input, SSH/WSL/remote sessions, tmux/screen, unavailable
or ambiguous input methods, and missing English sources retain manual switching.
Native Windows support is outside this feature. No additional Python package
is required.

## Ordinary lead input

After session attachment and active conversation focus, a nonblank line that
does not start with `/` is passed literally to `RuntimeSession.lead_turn()`.
The terminal renders validated `Outcome`, Lead message, evidence/proposal,
`Next action`, and state guidance. A proposal opens an exact
Confirm/Reject/Revise picker. Cancelling it leaves the proposal unconfirmed;
ordinary free text never confirms work. See [Confirmation decisions](#confirmation-decisions) for defaults, scoped fallback grammar, revision retry, and cancellation.

No session or no focused conversation yields the existing typed prerequisite
error without a Lead call. Retired execution commands remain unavailable;
`/run` is unknown and no `story run` command exists.

<a id="exact-search-and-transcript-records"></a>

## Structured history, search, and transcript records

`/history`, `/search`, `/transcript`, and the focused-history section of
`/status` render the active schema-v2 service without a provider call.
History rows identify sequence, turn, step, kind, direction, actors, and closed
readable fields. Restoration and transcript views contain only message frames,
including substantive exploration ideas and specialist alternatives. Provider
accounting, physical call observations, validation details, and structured
workflow controls remain in diagnostic inspection. This distinction does not
depend on the Message details setting. Search rows identify:

```text
Status: active
Subject (display only): Focused collaboration
Citation: conversation c_12ab34cd56, event proposal_event, sequence 1, field proposal.purpose, range 0:19
Snippet: consult specialists before recommending the next action.
```

The subject is orientation only. `/search` matches event fields, and its exact
result notice is `Search returned N of M matching readable fields.` An explicitly
bounded transcript reports `Transcript page returned N messages.` and, when more
are available, `Continue with /transcript HANDLE COUNT --from SEQUENCE.` These
transcript components appear only inside its transient viewer.

History/status state fields are `Last sequence`, `Current state`, `User
meaning`, `Next safe action`, and `Next action meaning`. See
[Conversation transcripts](conversation-transcripts.md) for every exact state
meaning. Rendering never turns a projected next action into executable
authority.

<a id="generic-internal-picker-foundation"></a>
<a id="independent-ranged-text-viewer"></a>
<a id="hosted-external-jsonl-viewer"></a>

## Pickers and viewers

Generic live pickers remain active for omitted session/conversation resume,
settings, authentication, and agent-exchange inspection. Capable terminals use
live selection; line fallback uses bounded numbered choices. Escape/cancel
restores the caller's scope without turning a choice into a domain mutation.
Shell workflow decisions opt into [Confirmation decisions](#confirmation-decisions); their scoped grammar does not change ordinary picker input.

In focused Lead input, `$` opens the canonical artifact-source picker. `$$`
opens the same picker followed by the independent text viewer; Shift+Up/Down
selects a range, and closing the viewer restores the composer. The controls
insert `${path}` or `${path}#Lstart-Lend`; file bytes are read later through
runtime authorization rather than embedded by the control.

`/transcript` opens an isolated, scrollable message view of one validated
conversation snapshot. Without `COUNT`, it includes all conversation messages
from sequence 1 or `--from`; an explicit count limits visible messages after
filtering and deduplication. `--from` remains a stored-event sequence, and
continuation points to the next undisplayed message's sequence. Message frames,
request/result notices, and continuation hints all remain inside the viewer.
Arrows or `j`/`k` scroll, `b`/Space move to the previous/next screen, and `g`/`G`
reach the beginning/end (`G` is Shift+G). Page Up/Down and Home/End remain aliases.
`q`, Escape, Ctrl-C, and EOF close the view. Closing restores the preceding shell
screen and clears transcript content, matching `/exchanges`. Unsupported
terminals receive a content-free notice rather than printed transcript rows.
The terminal owns the viewport and drops it after the call; navigation never
reads storage or changes conversation focus.

The borrowed external host paints and clears individual physical rows, bounds
output to the live terminal size, and avoids bottom-right wrapping. Cleanup
clears viewer rows before leaving the alternate screen, so repainting does not
archive those rows into ordinary shell history.

`/exchanges` uses the installed isolated JSONL viewer after Story chooses an
immutable scope/agent snapshot. Story validates the pinned 0.6.3 four-name API,
the `ViewerTerminal` transport constructor, and owned-call keyword arguments
before terminal acquisition. The low-level handoff borrows streams and Story's
persistent character reader under the existing input latch; it preserves the
lobby echo guard and rejects nested input or progress overlap.

The viewer owns raw mode, the alternate screen, cursor visibility, SGR mouse
capture, geometry, rendering, input decoding, and restoration. Wheel reports
move character focus one line; left clicks select visible JSON cells.
`h/j/k/l`, Enter folding, arrows for records, search, `n`/`N`, and `?` retain
their behavior. Story retains no viewer draft or frame. Closing returns to the
same shell scope, with earlier accepted messages still visible and no exchange
frames retained. It cannot call or replay a provider. The owned call requires
transient display; unavailable and callback-only terminals receive a
content-free notice.

The existing generic `InteractiveExternalViewer.open(operation)` borrowed-host
route remains available to internal callers. `/exchanges` instead calls
`open_owned(operation)`; that route leaves terminal modes to the library.

On capable terminals, Story keeps input echo disabled across shell input
transitions so buffered commands cannot be printed outside their transient
frames. Native login temporarily receives the original terminal settings;
leaving the shell restores them. Callback-only and line-fallback terminals
retain their existing input behavior.

Up/Down cycle records with first/last/only-record footer notices and reset the
viewport/cursor. All `h/j/k/l` keys reveal adjacent pages at viewport edges.
Page Up/Down (`b`/Space) scroll within a record before cycling records; Page Up
lands on the previous page’s last JSON row (the previous record’s final page
at a record boundary), Page Down on the next page’s first row. Vertical cursor
movement retains its preferred column. Folds and search counts remain unchanged.

Main-view `J`/`K` cycles through the nearest object's sibling keys and scrolls
to them while preserving folds and search counts. Endpoint arrival shows
`First sibling.`, `Last sibling.`, or `Only sibling.` in the footer; Escape
dismisses the notice before clearing active search. Cursor emphasis blinks
every 500 ms during idle; input restores the visible phase, and help/prompts pause
blinking. The raw host emits `idle` without changing its five-method interface.
Partial UTF-8 and buffered input survive these nonfatal polling pauses.

## Retained agent-output history

Capable terminals preserve completed turns in ordinary terminal scrollback.
Each user message keeps its JSON/progress frames in their original chronological
positions, followed by the resulting replies. Sending another message adds
another turn below it. Long final answers remain readable in full after the
turn finishes; terminal height does not insert empty rows between messages.

This abbreviated example illustrates the ordering, not the frame heights:

```text
User A: Review my outline.
  JSON output A1
  JSON output A2
Lead: Review of the outline ...
User B: Check character consistency.
  JSON output B1
Lead: Consistency findings ...

Main prompt > next message_
```

The existing `/progress` retention rules determine which previews remain.
Transient progress is replaced in its original slot when it becomes retained
JSON. Confirmations and retries stay with their originating user message.
While a turn is awaiting a decision, its disclosures remain in the active
presentation; F6 can review their full content. Completing the turn preserves
its messages and retained previews in native scrollback once. The archived
JSON frames are fixed snapshots; use F6 to scroll their contents.

Messages and retained previews remain available for review throughout the
current shell, including across conversation and session switches. Opening an
older frame does not reload files or call a provider. Review state is discarded
on shell exit; restored messages use the same complete output presentation.

The mouse wheel normally scrolls the terminal's own scrollback. Press `F6` to
open conversation review on a temporary screen and focus the newest visible
JSON frame, or the timeline when no frame is visible. `Tab` switches navigation
between the timeline and a visible frame. Arrows, paging keys, `Home`, and `End`
act on that target. At a frame boundary, further vertical movement continues
through the timeline. Keyboard review remains available when the terminal
cannot enable pointer interaction.

When pointer interaction is available, left-click a frame's header, border, or
body to change focus. The wheel over a JSON frame scrolls its contents; the left
gutter scrolls the whole timeline. The selected-control accent, `Focused` marker,
and `>` gutter marker identify frame focus even with `NO_COLOR` or clipped
borders. A `T` in the gutter marks whole-timeline focus.

Resizing reflows review content while keeping each JSON frame with its own user
input. Completed native output is not printed again when the terminal changes
size. Press `Escape`, `Enter`, or `F6` to return to the unchanged draft and cursor
or pending decision without submitting or confirming it. Printable text returns
to input and preserves that first character. Navigation does not add command
history entries or change conversation focus, and temporary review copies do not
enter native scrollback.

Inline pickers and prompts keep their disclosures available during confirmation.
If a decision and its context cannot fit, enlarge the terminal or press Escape
to leave without confirming. These controls remain transient. Native
authentication and external viewers have exclusive terminal ownership; returning
restores the draft with fresh terminal geometry.
Very short, narrow, or unsupported terminals retain safe ordinary output behavior.
Ordinary input never re-enables mouse capture merely because older frames exist.

## Safety

`sanitize_terminal_text` preserves ordinary Unicode/newlines and neutralizes
terminal control/ANSI injection. Capable redraws do not enter an alternate
screen. `NO_COLOR`, non-TTY, dumb-terminal, unsupported-platform, reduced-motion,
and fallback paths preserve semantic meaning without requiring topology or
decoration parity.

Secrets, credentials, native authentication output, raw diagnostics, and
artifact bytes are not automatically echoed by semantic status/error records.
The exchange viewer is read-only but not a redaction boundary.

See the [interactive parser](interactive-command-parser.md),
[story-session guide](../guides/story-sessions.md), and
[terminal safety](../operations/terminal-safety.md).
