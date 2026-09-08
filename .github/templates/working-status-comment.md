<!--
Purpose: Give a reader a quick, accurate view of progress and the next action.
When to use: Post or update a working-status comment when meaningful progress, a
blocker, a needed decision, or completion changes the task's state.

Read the authoring guide and this entire source, including the three example variants,
before writing:
[Authoring guide](../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md).

Current status: State the meaningful status and its scope in one or two sentences.
Distinguish implementation complete, verification complete, and task complete. Use
workflow policy for actual Project transitions; a comment does not change native status.

What works now: Describe delivered or verified behavior in familiar language. Give a
concrete result when it helps, and say whether it was observed, reported by another
record, or still inferred.

What remains: Name unfinished implementation, verification, review, integration, or
cleanup steps relevant to completion. Do not turn an unexecuted check into a pass or
hide it behind a percentage.

Blocker or decision: State the cause, impact, decision or unblock condition, owner, and
next action. If there is no blocker, say so briefly. A timeout is incomplete evidence;
lack of CI is not success. Do not invent a blocker merely to fill a section.

Next step: Name one concrete next action and its owner. Include a useful completion
condition rather than an unsupported time promise. For completed work, state the handoff
or follow-up owner when one remains.

Evidence links: Link the relevant Issue, PR, or detailed Evidence record with
descriptive labels. Keep logs, hashes, command manifests, and budget details in Evidence
rather than crowding the status summary. State when the evidence is historical, partial,
private, or unavailable.

Replace every double-brace placeholder. Remove comments and examples before posting.
Omit only genuinely optional empty sections; preserve meaningful blockers and
verification gaps. Re-read the comment for what works, what remains, and who acts next.
The illustrative sequence below is not a record of actual historical status transitions.
-->

## Current status

{{Current state and its scope in one or two sentences.}}

## What works now

{{Delivered or verified behavior and a useful concrete result.}}

## What remains

{{Work and checks still needed for completion.}}

## Blocker or decision

{{Blocker, impact, unblock condition, and decision owner, or no current blocker.}}

## Next step

{{Next action, its owner, and the condition that completes it.}}

## Evidence links

{{Descriptive links to the relevant work and detailed evidence, including evidence limits.}}

<!--
Illustrative worked example: three hypothetical status updates grounded in
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) and the
[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359).
The sequence, intermediate states, blocker, and proposed owners are constructed
examples. They are not observed comments, historical transitions, or fresh verification
results.

Progress variant

## Current status

The Antigravity repair is implemented and ready for focused verification. The task is
still in progress.

## What works now

The repair is implemented to show a valid final assistant reply even after intermediate
errors. That behavior still awaits observation in this illustrative stage.

## What remains

Run the selected adapter and compatibility checks, then verify actual replies and
conversation continuity after reopening. Record material limits for review.

## Blocker or decision

No current blocker. Verification remains pending.

## Next step

The test owner runs the selected checks and records their results in Evidence.

## Evidence links

[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) supplies
the implementation context for this example. The later
[verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
is a historical source, not evidence already available at this hypothetical progress
stage.

Blocked variant

## Current status

Implementation is ready, but verification is blocked. The task cannot be declared
complete while the required actual conversation checks remain unexecuted.

## What works now

In this hypothetical stage, local checks support the final-reply handling. Replies and
continuity in actual Story conversations remain unobserved.

## What remains

Verify actual replies, same-thread continuity, and marker recall across the selected
fresh and reopened Story turns. Preserve the local results while arranging the missing
checks.

## Blocker or decision

Hypothetical blocker: the test owner lacks a ready provider environment, so actual
conversation checks cannot run. The root needs to arrange that environment before this
coverage gap can close.

## Next step

The root arranges the missing environment. The test owner then resumes the remaining
checks and updates the Evidence record.

## Evidence links

[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) provides
the repair context. No historical source is claimed for this invented blocker. The
[later verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
illustrates the kind of linked result needed to close the coverage gap.

Completion variant: implementation/verification complete; publication pending

## Current status

Implementation is complete. Local verification is reported passed. Publication is
pending in this hypothetical update.

## What works now

The historical report records successful replies and same-thread marker recall after
reopening. It reports 51 focused tests with zero skips, three actual adapter calls, and
six Story turns.

## What remains

Complete review and integration, then verify Issue closure and Project completion. The
missing exact historical selection remains an evidence limit for the review.

## Blocker or decision

No implementation blocker is identified in the supplied report. The reviewer still needs
to assess the evidence gaps and the argument and model-attestation limits.

## Next step

The root completes the publication checks. Once confirmed, a fully published update
would state: “Implementation complete; verification accepted; PR merged, owning Issue
closed, and Project Done,” with links to those confirmed records. This wording is
illustrative, not a claim about the historical records.

## Evidence links

[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
contains the reported local results.
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) contains
the implementation. The exact commands and raw private manifest are inaccessible from
the comment.
-->
