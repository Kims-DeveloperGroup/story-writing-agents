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

What works now: Name the scenario that is delivered or verified and its useful result.
Say whether the behavior was observed, reported by another record, or only implemented
and awaiting observation.

What remains: Name unfinished work or checks and explain which user scenario cannot yet
be delivered or relied on. Do not turn an unexecuted check into a pass or hide it behind
a percentage.

Blocker or decision: State the cause, affected scenario, unblock condition, and decision
owner. Explain the practical impact. If there is no blocker, say so briefly. A timeout
is incomplete evidence; lack of CI is not success. Do not invent a blocker to fill a
section.

Next step: Name one concrete action and its owner, then state which scenario outcome or
evidence gap it resolves. Use a completion condition rather than an unsupported time
promise. For completed work, identify any remaining handoff or follow-up owner.

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

{{Named scenario, delivered or verified behavior, and a useful result.}}

## What remains

{{Remaining work and how it affects the user scenario or acceptance condition.}}

## Blocker or decision

{{Blocker, scenario impact, unblock condition, and owner, or no current blocker.}}

## Next step

{{Next action, its owner, and the scenario outcome or gap it resolves.}}

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

Verify valid replies after intermediate errors, reopening with marker recall, and
rejection of unsuccessful final results. These checks are needed to establish that users
can continue the same conversation without an invalid result appearing as a successful
reply.

## Blocker or decision

No current blocker. Verification remains pending.

## Next step

The test owner checks reply delivery, continuity, and rejection, then records the
observed outcomes against those criteria in Evidence.

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

Observe actual replies and marker recall on the same thread after reopening. Whether
users can retain earlier context after reopening remains unverified. Preserve the
completed local results.

## Blocker or decision

Hypothetical blocker: the test owner lacks a ready provider environment. This prevents
checking whether reopening preserves the user's earlier context. The root needs to
arrange that environment to close the gap.

## Next step

The root arranges the provider environment. The test owner then checks a fresh reply and
a reopened marker answer on the same thread, resolving the missing continuity evidence.

## Evidence links

[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) provides
the repair context. No historical source is claimed for this invented blocker. The
[later verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
illustrates the kind of linked result needed to close the coverage gap.

Completion variant: implementation and reported checks complete; publication pending

## Current status

Implementation is complete. The reported local checks passed. Publication is pending in
this hypothetical update.

## What works now

The historical report records successful replies and same-thread marker recall after
reopening. It reports 51 focused tests with zero skips, three actual adapter calls, and
six Story turns.

## What remains

Resolve the missing intermediate-error and rejection-case evidence before calling the
criteria complete. Review and integration also remain pending, so publication of the
repair is not yet confirmed. Then verify Issue closure and Project completion.

## Blocker or decision

No implementation blocker is identified in the supplied report. The reviewer needs to
assess the uncovered intermediate-error and rejection conditions and limits on prompt
visibility, size, and model confirmation before accepting the result.

## Next step

The root resolves the missing case evidence and completes publication checks. Once
confirmed, a fully published update would state: “Implementation complete; verification
accepted; PR merged, owning Issue closed, and Project Done,” with links to those
confirmed records. This wording is illustrative, not a claim about the historical
records.

## Evidence links

[Issue #621 verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
contains the reported local results.
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) contains
the implementation. The exact commands and raw private manifest are inaccessible from
the comment.
-->
