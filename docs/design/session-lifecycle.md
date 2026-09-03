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

The package now has one shared, content-safe assessment for a later controller
to consult before entering a destination, leaving a source, or pausing a story
session. It is package-internal and is not yet consumed by an installed
command. The assessment is evidence, not authority: it never acquires or
releases ownership, performs a transition, calls a provider, applies an
artifact, or changes Git. #528 remains responsible for presenting and
enforcing it in the runtime lifecycle.

Active and idle sessions use different evidence paths:

1. With both a conversation ID and collaboration store, the assessor delegates
   exactly once to the #527 reconciler. It preserves the exact nested recovery
   result. Proposed, reviewed, awaiting-application, committed, and rejected
   work permits all three transitions; applied-not-committed or staged work
   permits destination attachment only, so the user can inspect it without
   releasing or pausing its owner.
2. With neither active input, it first requires lifecycle `ready + held_here`.
   It then observes session resources and complete history, followed by
   external Git and linked-worktree status, and repeats the same four reads as
   generation B. Only equal, clean, fully closed owner facts produce
   `idle_ready`. First-read failure, owner uncertainty, second-read failure, or
   drift fails closed with the responsible owner and no permitted operation.

The assessor does not claim access while checking and does not release access
afterward; lifecycle ownership must already be held. Enter permission only
means a later controller may proceed with attachment. Leave and pause
permission means that controller may proceed through the existing release
mechanism. The assessor itself retains no proof path, digest, provider content,
process identity, handle, or capability. See the
[canonical operation matrix and user examples](../operations/recovery-and-limitations.md#session-transition-assessment).

## Commands and next actions

| Facts | Command | Next action |
| --- | --- | --- |
| Absent | `/session create NAME` or `story session create SESSION` | Create a session. |
| Ready + claimable | `/session resume [NAME]` or `story session reopen [SESSION]` | Resume/attach it. |
| Paused | Same resume/reopen forms | Make it ready and attach it. |
| Live local owner | `session_access_blocked` | Close the owner normally, then retry. |
| Unknown owner | `session_ownership_unknown` | Verify ownership; preserve the claim. |
| Attached, no focus | `/conversation new` or `/conversation resume` | Create or choose a conversation for scoped reads. |
| Attached and focused | Management/inspection ready | Use history/search/transcript/status; story execution remains unavailable. |

Noninteractive create/reopen saves the default, releases its temporary access,
and prints ready/claimable JSON. Pause retains the default and reports
paused/claimable. Lifecycle commands have no `--session` option; their optional
positional session uses the saved default where allowed.

<a id="shell-transitions-and-compensation"></a>

## Shell switching and compensation

Bare `story` resumes a ready default and asks before reopening a paused default.
`story chat SESSION` creates an absent session or resumes an existing one.
Interactive `/session resume` never creates.

Switching performs destination validation/acquisition, optional durable reopen,
post-validation, destination-focus load, default persistence, source release,
then in-memory attachment/focus commit. The sequence is not one transaction. A
successful paused-to-ready transition is not rolled back if a later focus or
default step fails. Later failure releases a newly acquired destination when
possible and leaves in-memory scope unchanged; inspect persisted state because
an atomic replacement can complete before a reported failure.

`/quit` and EOF release without pausing. Confirmed `/session pause` persists
paused, clears current scope, and releases. Both retain defaults.

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

See [Story sessions](../guides/story-sessions.md),
[Backend and sessions](../reference/backend-and-sessions.md), and
[recovery](../operations/recovery-and-limitations.md).
