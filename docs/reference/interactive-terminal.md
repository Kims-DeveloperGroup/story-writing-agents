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

`/exchanges` uses a different hosted external viewer. After attachment, the
controller chooses an immutable scope/agent snapshot and opens the installed
isolated read-only JSONL viewer. Story validates the pinned 0.5.0 API and
`ViewerSpec.input_protocol="keys"` capability before terminal acquisition;
the older 0.1.1 and 0.2.0 APIs are incompatible. The viewer owns key bindings,
ordinary-line grammar, a single full-text search draft, goto drafts, editing,
and cancellation. Main-view `h/j/k/l` moves character focus, Enter toggles its
container fold,
arrows select records, and `?` opens help. Search and `n`/`N` reveal individual
occurrences through folded ancestors. ANSI uses readable inverse focus; plain
output reserves an extra caret row. Story transports decoded physical text/keys
and owns terminal
modes, geometry, frame presentation, and restoration. Closing the view returns
to the same shell scope. It cannot call or replay a provider. For `/exchanges`,
Story requires alternate-screen support. In line mode it shows a content-free
notice instead of displaying exchange records in scrollback.

Main-view `J`/`K` selects the nearest object's next/previous sibling key and
scrolls to it while preserving folds and search counts. Cursor emphasis blinks
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
