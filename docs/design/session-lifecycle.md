# Session lifecycle design

> **Availability:** durable sessions, leases, and the installed TTY session
> lobby are implemented. The lobby manages attachment only; it performs no chat
> turn, conversation-history, transcript, search, or artifact work.

Key terms: **active**—durable permission for provider work; **selected**—the
workspace's saved default session; **attached**—this backend owns the temporary
lease; **lobby**—the controller state with no attachment.

## Three independent facts

| Fact | Persists? | Meaning |
| --- | --- | --- |
| Active | Yes, in the session manifest | Provider work is allowed. |
| Selected | Yes, in CLI context | Bare `story` knows which session to try. |
| Attached | No, represented by a lease | One backend temporarily owns the session. |

A selected session may be inactive, and quitting may leave an active session
selected but unattached. Selection never proves lease ownership.

## Entering the lobby

The controller is single-use. Construction performs no I/O; starting it gives
the controller responsibility for final detach and backend close.

| Entry | Result |
| --- | --- |
| Explicit missing session | Create, attach, and select it without confirmation. |
| Explicit active session | Attach and select it without confirmation. |
| Explicit inactive session | Resume, attach, and select it without confirmation. |
| Bare entry, no selection | Show the unattached lobby. |
| Bare entry, selected active session | Attach it without rewriting unchanged selection. |
| Bare entry, selected inactive session | Ask to resume, defaulting to yes; no or Ctrl-C stays in the lobby, EOF exits. |

Recoverable ID, existence, contention, inactive-race, and preflight errors leave
the controller in its current safe state. Workspace corruption, unknown state,
or cleanup uncertainty stops the controller with exit code `2`.

Create, resume, and attach first resolve every agent's provider route and check
that the configured executable is discoverable. This preflight starts no
provider process and does not verify login, network access, or model validity.
Listing and ending require no provider preflight.

## Attach and switch ordering

An initial attachment is committed in this order:

1. acquire and validate an active, owned destination lease;
2. save the destination as CLI selection; and
3. commit controller state and render orientation.

Switching from source A to destination B adds one step:

1. acquire and validate B;
2. save B as selection;
3. detach A; and
4. commit B and render its orientation.

Acquisition failure leaves A and its selection unchanged. Selection-write
failure releases B before reporting the failure, so A remains attached. This
destination-first order avoids abandoning the working session merely because a
new one was unavailable.

There is one bounded fatal exception. If A cannot detach after B was saved,
rolling selection back to A would point at a session whose ownership is now
uncertain. The controller therefore keeps B selected, renders no B orientation,
clears its in-process attachment marker, asks backend close to release owned
leases, and exits `2` with `cleanup_failed`. It does not claim that either
attachment is safe.

## End, detach, and close

`/session end` requires an attachment and defaults to no. Blank, no, or Ctrl-C
keeps the session active and attached. EOF leaves it active, then follows normal
exit cleanup. Confirmed yes makes the manifest inactive, releases its lease,
retains selection, and returns to the lobby.

If end fails, the controller continues only when a provider-free summary proves
the same session is still active and owned. Any other result is cleanup
uncertainty and exits `2`.

Main-prompt Ctrl-C cancels only that input. `/quit`, `/exit`, or EOF detaches a
known session without deactivating it, then closes the backend. Detach or close
failure makes the final status `2`; close is still attempted and raw failure
text is never displayed.

The backend owns manifests, leases, and the claim guard; the controller owns
only transition order and its in-process attachment ID. Exact lease states and
reclamation rules are in [Backend and sessions](../reference/backend-and-sessions.md).
Schemas and selection persistence are in
[Session storage](../reference/configuration/session-storage.md).
