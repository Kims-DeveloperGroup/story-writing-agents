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

`write_collaboration_events(events, after_sequence=N, show_details=False)`
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

## Ordinary lead input

After session attachment and active conversation focus, a nonblank line that
does not start with `/` is passed literally to `RuntimeSession.lead_turn()`.
The terminal renders validated `Outcome`, Lead message, evidence/proposal,
`Next action`, and state guidance. A proposal opens an exact
Confirm/Reject/Revise picker. Cancelling it leaves the proposal unconfirmed;
ordinary free text never confirms work.

No session or no focused conversation yields the existing typed prerequisite
error without a Lead call. Retired execution commands remain unavailable;
`/run` is unknown and no `story run` command exists.

<a id="exact-search-and-transcript-records"></a>

## Structured history, search, and transcript records

`/history`, `/search`, `/transcript`, and the focused-history section of
`/status` render the active schema-v2 service without a provider call.
History/transcript rows identify sequence, turn, step, kind, direction, actors,
and closed readable fields. Search rows identify:

```text
Status: active
Subject (display only): Focused collaboration
Citation: conversation c_12ab34cd56, event proposal_event, sequence 1, field proposal.purpose, range 0:19
Snippet: consult specialists before recommending the next action.
```

The subject is orientation only. `/search` matches event fields, and its exact
result notice is `Search returned N of M matching readable fields.` A
transcript reports `Transcript page returned N records.` and, when more are
available, `Continue with /transcript HANDLE COUNT --from SEQUENCE.`

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

In focused Lead input, `$` opens the canonical artifact-source picker. `$$`
opens the same picker followed by the independent text viewer; Shift+Up/Down
selects a range, and closing the viewer restores the composer. The controls
insert `${path}` or `${path}#Lstart-Lend`; file bytes are read later through
runtime authorization rather than embedded by the control.

`/exchanges` uses the installed isolated JSONL viewer after Story chooses an
immutable scope/agent snapshot. Story validates the pinned 0.6.1 four-name API,
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
