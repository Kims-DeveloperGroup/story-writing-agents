# Session lifecycle design

Session lifecycle remains fully active for management even though public story
execution is unavailable.

<a id="canonical-lifecycle-state"></a>

## State axes

| Durable ↓ / access → | `claimable` | `held_here` | `blocked_by_live_local_owner` | `ownership_unknown` |
| --- | --- | --- | --- | --- |
| `absent` | Create it. | Invalid | Invalid | Invalid |
| `ready` | Resume it. | Manage/read it. | Close the local owner. | Verify and preserve the claim. |
| `paused` | Reopen it. | Valid claim, but durably paused. | Close the local owner. | Verify and preserve the claim. |

Durable state says whether a session is absent, ready, or paused. Process access
says whether this process owns the claim. The axes are independent. A ready,
claimable session after `/quit` is durable but not attached.

`SessionLifecycleState` carries session ID, durable/access enums, paired owner
facts, and ordered offset-aware timestamps. `provider_work_is_ready()` is pure:
valid provider configuration plus `ready` plus `held_here`. In this integration
stage that fact is diagnostic/configuration readiness, not evidence that a
public provider command exists.

## Defaults and current scope

- Default session is a saved fallback for a later command/process.
- Current attachment is the session whose claim this shell holds.
- Default conversation is saved within one session.
- Current focus is the active conversation used by scoped reads.

Defaults prove no current ownership. `/status` presents these fields
separately, along with durable/access state, storage, provider readiness, and an
applicable next action.

## Transition mechanism

```mermaid
sequenceDiagram
    participant C as Controller
    participant B as AgentBackend
    participant G as Claim guard
    participant M as Lifecycle manifest
    C->>B: validate configuration and pre-read lifecycle
    B->>G: lock and inspect claim
    alt absent or proved-dead local claim
        G->>G: create/replace this process claim
    else live local or unknown owner
        G-->>C: typed failure; preserve claim
    end
    B->>M: re-read under access
    opt create, reopen, or pause
        B->>M: create/replace manifest
    end
    C->>B: correlated post-read
    opt release
        B->>G: compare token and remove own claim
    end
```

Identity and pre/post facts must correlate. Failure compensates only a newly
acquired claim; it never deletes another process's claim.

<a id="story-session-transition-assessment"></a>

## Story-session transition assessment

The package has one shared, content-safe assessment that the noninteractive
lifecycle commands and interactive controller consume before entering a
destination, leaving a source, or pausing a story session. The assessment is
evidence, not authority: it never acquires or releases ownership, performs a
transition, calls a provider, applies an artifact, changes Git, or reconstructs
a confirmation capability. `AgentBackend.assess_session_transition()`
validates the operation type, delegates once, and returns the assessor's exact
result; each caller permits only an operation named by that result.

Active and idle sessions use different evidence paths:

1. With both an eligible persisted active conversation default and its
   same-provenance collaboration store, the assessor delegates exactly once to
   the recovery reconciler and preserves the exact nested recovery result.
   Proposed, reviewed, awaiting-application, committed, and rejected work
   permits all three transitions; applied-not-committed or staged work permits
   destination attachment only, so the user can inspect it without releasing
   or pausing its owner. An absent, missing, or archived persisted default is
   not active evidence and falls through to the idle path.
2. With neither active input, it first requires lifecycle `ready + held_here`.
   It then observes session resources and complete history, followed by
   external Git and linked-worktree status, and repeats the same four reads as
   generation B. Only equal, clean, fully closed owner facts produce
   `idle_ready`. First-read failure, owner uncertainty, second-read failure, or
   drift fails closed with the responsible owner and no permitted operation.

The orchestration keeps four responsibilities separate:

| Owner boundary | Responsibility during a transition |
| --- | --- |
| Lifecycle (`AgentBackend`) | Read durable/access state, acquire or release the exact claim, and create/reopen/pause the manifest. |
| Git resources (`workspace.initialization`) | Provision and verify Create's exact branch, sibling worktree, and internal ref; inspect existing resources/history for later assessment. |
| Conversation (`cli_context` plus configured storage) | Resolve the persisted default, accept it only when its catalog record is active, and provide the same-provenance event store. |
| Recovery (`SessionTransitionAssessor` / reconciler) | Classify active or idle facts, name the responsible failure owner, and return an ordered operation set without performing it. |

The assessor does not claim access while checking and does not release access
afterward; lifecycle ownership must already be held. Enter permission only
means the caller may proceed with attachment. Leave and pause permission means
the caller may proceed through the existing release mechanism. Runtime content
writes still occur only after the existing lead/user confirmation path where
that path requires confirmation. The assessor itself retains no proof path,
digest, provider content, process identity, handle, confirmation choice, or
capability. See the
[canonical operation matrix and user examples](../operations/recovery-and-limitations.md#session-transition-assessment).

## Commands and next actions

| Facts | Command | Next action |
| --- | --- | --- |
| Absent | `/session create NAME` or `story session create SESSION` | Provision the exact Git resources, create the lifecycle, and assess entry. |
| Ready + claimable | `/session resume [NAME]` or `story session reopen [SESSION]` | Acquire, assess entry, then attach/save the default only when allowed. |
| Paused | Same resume/reopen forms | Reopen to ready, assess entry, then attach/save the default only when allowed. |
| Live local owner | `session_access_blocked` | Close the owner normally, then retry. |
| Unknown owner | `session_ownership_unknown` | Verify ownership; preserve the claim. |
| Attached, no focus | `/conversation new` or `/conversation resume` | Create or choose a conversation for scoped reads. |
| Attached and focused | Management/inspection ready | Use history/search/transcript/status; story execution remains unavailable. |

Noninteractive create/reopen saves the default only after entry is allowed,
then releases temporary access and prints ready/claimable JSON with an
`assessment`. Pause retains the default and reports paused/claimable only when
`pause_session` is allowed. A denied assessment prints JSON and exits 2; a
lifecycle-only denial has `assessment: null`. Lifecycle commands have no
`--session` option; their optional positional session uses the saved default
where allowed.

<a id="shell-transitions-and-compensation"></a>

## Shell switching and compensation

Bare `story` resumes a ready default and asks before reopening a paused default.
`story chat SESSION` creates an absent session or resumes an existing one.
Interactive `/session resume` never creates.

Create performs lifecycle absence check, provider configuration validation,
Git-resource provisioning/verification, lifecycle creation and correlated
post-read, destination-focus load, entry assessment, default persistence, then
in-memory attachment. Reopen performs lifecycle pre-read, configuration
validation, acquire or durable reopen, correlated post-read, focus load, and
entry assessment before persistence or attachment. No provider story turn runs
during any assessment.

Switching first completes that destination path through `enter_destination`.
It then rereads the still-attached source and assesses `leave_source`. Only
after both permissions exist does it persist the destination default, release
the source, and commit the in-memory attachment/focus. Destination acquisition,
focus, assessment, or default-write failure releases the destination and
preserves the source when release can be proved. A source denial releases only
the destination, leaves the source attached, and preserves the source default.
The destination default is never persisted before source leave permission.

The sequence is not one transaction. A successful paused-to-ready destination
transition is not rolled back if a later focus or default step fails. Inspect
persisted state because an atomic default replacement can complete before a
reported failure. Source-release failure after that replacement is fatal
`cleanup_failed`; the controller does not roll the default back.

For comparison, the noninteractive order is exact:

| Command | Ordered boundary calls |
| --- | --- |
| Create | lifecycle absence read → configuration validation → Git-resource provision/verify → lifecycle create/acquire → correlated lifecycle read → idle entry assessment → default save if allowed → release |
| Reopen | lifecycle pre-read → configuration validation → acquire ready or reopen paused → correlated lifecycle read → persisted active-default lookup → entry assessment → default save if allowed → release |
| Pause | lifecycle pre-read → acquire only if claimable → persisted active-default lookup → pause assessment → durable pause/release if allowed; otherwise release only a newly acquired claim |
| Interactive switch | destination acquire/reopen and correlated read → destination default/focus lookup → destination entry assessment → source held-state read → source leave assessment → destination-default save → source release → in-memory attachment/focus commit |

`/quit` and EOF release without pausing. Confirmed `/session pause` rereads the
held lifecycle, assesses the current focused conversation (or idle evidence),
and only then persists paused, clears current scope, and releases. Denial leaves
the source attached. Both paths retain defaults.

## Conversation compensation

Conversation creation/resume saves the selected default before changing current
focus. A reported save failure leaves in-memory focus unchanged but can follow
an atomic persisted replacement. Archiving focused content chooses another
active default or `null`; rollback is attempted only while ready access remains
proved.

Conversation catalog mutations create no collaboration event. `/history` is
therefore `empty` for a new conversation, and `/search` returns no hit for its
subject because search covers event fields only. The selected schema-v2
inspection path remains empty until an authorized writer appends real workflow
events.

## Artifact viewer relationship

Artifact source pickers and the independent text viewer remain generic terminal
components, not durable lifecycle states. The current controller exposes no
provider-text input span, so main-input `$`/`$$` does not enter those components
and creates no reference operation.

## Live-composer operation boundary

The terminal package also exposes one package-internal live composer for later
controller integration. It runs an injected confirmed operation and an
ordinary line editor under the driver's existing input latch. The driver owns
raw input, fixed progress redraw, one output stream, and terminal restoration;
runtime callbacks own the durable boundary plus cancellation-and-join proof.
The component neither infers a commit from presentation nor performs a domain
write.

Accepted input is resolved operation-first:

```text
operation finishes first -> operation_completed + preserved editor state
submit at PRECOMMIT      -> cancel and join -> replan, or recovery_required
submit at POSTCOMMIT     -> wait only       -> next_turn, or recovery_required
Ctrl-C / EOF             -> interrupted / eof (never submission)
```

The immutable editor state carries the exact buffer and cursor through every
redraw and terminal outcome. A submitted slash prefix remains literal text.
At `PRECOMMIT`, only a proved `joined` result may expose that text as `replan`;
an exception, invalid result, or `uncertain` join closes as
`recovery_required` without replay text. At `POSTCOMMIT`, cancellation is
forbidden: the operation must finish before `next_turn` is returned, and a
failed or cancelled finalization becomes `recovery_required`. The raw driver
is the sole terminal writer throughout the span.

An incapable terminal does not start a concurrent blocking reader. It waits
for the operation, returns `operation_completed`, and preserves the initial
draft for a later ordinary read. This fallback preserves semantic continuity,
but it does not provide live editing.

These are terminal/runtime handoff contracts, not session lifecycle states.
They do not make public story execution available, reconstruct confirmation
authority, or decide durable completion. The controller integration and
semantic rendering that consume these results remain separate work.

See [Story sessions](../guides/story-sessions.md),
[Backend and sessions](../reference/backend-and-sessions.md), and
[recovery](../operations/recovery-and-limitations.md).
