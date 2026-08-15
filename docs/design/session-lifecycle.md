# Session lifecycle design

> **Availability:** the controller manages sessions, conversations, routed
> turns, local reads, and selected-context consensus. History and standalone
> artifact commands remain unavailable.

Key terms: **active**—provider work allowed; **attached**—this process owns the
lease; **selected conversation**—a saved record; **sticky target**—the
default responder; **compensation**—restoring and proving earlier state.

<a id="three-independent-facts"></a>

## Four independent facts

| Fact | Persists? | Meaning |
| --- | --- | --- |
| Session active | Yes, in its manifest | Provider work is allowed. |
| Session selected | Yes, in CLI context | Bare `story` knows which session to try. |
| Session attached | No, through a lease | This backend temporarily owns it. |
| Conversation selected | Yes, per story session | The shell knows the current subject. |

Selection never proves lease ownership. Quit detaches; end makes the session
inactive. Both retain saved selections.

## Entering the lobby

The single-use controller owns final detach and close. Explicit entry creates,
attaches, or resumes the named session without confirmation. Bare entry opens
the outer lobby with no saved session, attaches an active one, or asks to resume
an inactive one with yes as default.

After acquisition, missing, `null`, missing-record, or archived conversation
selection enters the inner lobby; stale IDs are not rewritten. Malformed or
unsafe selection storage is fatal on initial entry.

Session acquisition preflights routes and executable discovery but starts no
provider. Conversation commands, agent roster/target changes, session list,
and session end are provider-free.

## Target and turn gates

`/agents` is global. `/to` and `/agent` require attachment but not conversation
selection; they set the sticky target. Ordinary and `/say` messages use that
target, while `@AGENT` selects one responder without changing it. Content also
requires a selected active conversation and blocks the command loop for one
runtime turn. Conversation create, use, rename, and archive preserve the target.

## Attach and switch ordering

For an initial attachment or switch to destination B, the controller:

1. acquires and validates B's active owned lease;
2. loads and validates B's saved conversation, if any;
3. saves B as the story-session selection when it changed;
4. detaches source A when switching; and
5. commits B in memory, resets the visible agent alias to `lead`, and renders.

Failure before completing step 3 releases B and preserves A. If A cannot detach
after B was saved, B stays selected, no B orientation appears, close is
attempted, and the controller exits `2` with `cleanup_failed`.

## Conversation lifecycle and compensation

```mermaid
flowchart TD
    accTitle: Subject conversation selection and archive compensation
    accDescr: Attachment restores an active choice or opens the inner lobby. Selected archive saves a successor or null; failed saving either proves the restored original or exits.
    A[Attach story session] --> B{Saved active conversation?}
    B -->|No, stale, or null| L[Conversation lobby]
    B -->|Yes| S[Selected conversation]
    L -->|New: create, then save ID| S
    L -->|Use: resolve active ID, then save| S
    S -->|Rename or list| S
    S --> C{Archive selected?}
    C -->|No or Ctrl-C| S
    C -->|Yes| D[Archive metadata]
    D --> E{Save first active successor or null}
    E -->|Successor| S
    E -->|Null| L
    E -->|Write fails| F[Unarchive and verify original selection and lease]
    F -->|Proved| S
    F -->|Uncertain| X[Exit 2 and close best effort]
```

`new` writes exact display metadata, then selection; save failure leaves the new
record and prior effective choice. `use` saves a resolved active ID before
memory changes. `subject` renames the selected active record; `list` is read-only.
None changes the sticky target.

Nonselected archive needs no confirmation and does not reselect. Selected
archive defaults to no; yes selects the first other active catalog record or
`null`. Save failure recovers only if unarchive, original durable selection,
and the owned active lease are proved; otherwise it is fatal.

## End, detach, and close

`/session end` defaults to no. Yes deactivates, releases the lease, retains both
selections, clears in-process conversation state, resets the target to lead,
and returns to the outer lobby. No, blank, or Ctrl-C keeps the attachment; EOF
exits without ending.

`/quit`, `/exit`, and main-prompt EOF detach without deactivation, then close
the backend. Detach or close failure changes the final status to `2`. Exact
lease rules are in [Backend and sessions](../reference/backend-and-sessions.md);
schemas are in [Session storage](../reference/configuration/session-storage.md).
