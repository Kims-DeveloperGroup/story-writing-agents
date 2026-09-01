# Interactive terminal component

The terminal component renders sanitized semantic records and collects
TTY/fallback management input. The current controller uses it for startup,
authentication/settings, session/conversation management, local inspection,
pickers, confirmations, and read-only viewers. It does not expose a current
provider-bound prompt span.

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
and generic composition/agent/run presentation seams. A method's presence on
this generic component does not make it a current shell command. The controller
does not call the composition/agent/run seams for user story execution.

The retained public methods are:

| Purpose | Methods |
| --- | --- |
| Read management input | `read_main`, `request_conversation_subject`, `request_confirmation` |
| Render scope and management data | `write_lobby_orientation`, `write_session_orientation`, `write_conversation_scope`, `write_conversation_list_entry`, `write_conversation_search_hit`, `write_conversation_transcript_event`, `write_agent_roster`, `write_status`, `write_help`, `write_error` |
| Generic execution presentation, without a current controller consumer | `compose`, `write_agent`, `write_run` |

For argument and return types, inspect the public class signature; concrete
streams remain implementation details. Session/conversation command behavior
is documented in [Story sessions](../guides/story-sessions.md), and retained
provider-exchange inspection is documented in
[Agent exchanges](../guides/agent-exchanges.md).

Concrete stream/contextual builders, pickers, text viewers, external viewers,
semantic record types, and transition functions are package-internal
implementation details unless another reference explicitly names them.

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
identity. In this integration stage, attached management input does not invent
one. Supplying an agent without a session is invalid component use.

Capable terminals provide exact command completion for the retained parser
grammar. Fallback streams use ordinary line reads. Submitted management input
is recorded according to the terminal's bounded main-input history rules;
picker answers and sensitive settings/authentication input remain isolated.

## Unavailable story input

The parser/controller returns a typed command-invalid error containing this
exact sentence:

```text
Direct story execution is unavailable in this integration stage. Session, conversation, history, search, and inspection remain available.
```

Semantic rendering can add terminal-specific headings, colors, and hints. The
sentence is the stable output contract. No user/agent chat block, provider
progress, reference picker, transcript append, or artifact output follows.

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

The independent text viewer and artifact-source picker components remain in
the terminal implementation for future composition, but current main input has
no provider-text activation scope. Typing `$` or `$$` therefore does not open
them.

`/exchanges` uses a different hosted external viewer. After attachment, the
controller chooses an immutable scope/agent snapshot and opens the installed
isolated read-only JSONL viewer. Closing it returns to the same shell scope.
It cannot call or replay a provider.

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
