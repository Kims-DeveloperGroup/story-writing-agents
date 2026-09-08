---
name: Work item
about: Describe a useful outcome, concrete examples, and a plan for completing it.
title: ""
labels: ""
assignees: ""
---

<!--
Purpose: Help a reader understand the intended result and decide whether the work is
complete.
When to use: Create or substantially update an Issue for a feature, fix, workflow
change, or documentation task.

Read the authoring guide and this entire source, including the example, before writing:
[Authoring guide](../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md).

Objective: State the useful behavior in one or two sentences. Name who benefits and what
they can do. Keep implementation vocabulary out unless it is essential to the result.

Use cases and expected results: Give realistic inputs and their expected outputs or
observable results. Identify constructed examples. A workflow or internal change can use
a contributor action and resulting repository state; do not invent a screen. Use a small
table only when it makes multiple cases easier to compare.

Implementation plan: List the intended steps and explain how each contributes to the
objective. Include affected components, tests, and documentation. Keep this plan current
when approved scope changes.

Acceptance criteria: Use checkboxes for observable completion conditions, including
material failure or compatibility cases. Planned checks are not completed evidence.

Boundaries and dependencies: State what is included, excluded, or constrained. Identify
prerequisites, their owners, and decisions still needed. Describe risks that could
change the plan. Link native parent and dependency relationships; prose links do not
create them.

Related work and verification plan: Link the parent or initiative, canonical Project,
dependencies, related work, and approved base when applicable. Explain how each
acceptance condition will be checked and where detailed evidence will be recorded. For
an initiative, name integration criteria, children, and execution order. For a leaf,
identify its parent or say it is standalone, and include test and documentation work.
Use Project fields for planning metadata rather than copying a metadata wall into the
objective.

Replace every double-brace placeholder. Before submission, remove all comments,
frontmatter, and example material. Preserve required content; omit only genuinely
optional empty sections. Re-read the finished draft for a clear goal, examples, plan,
and verification gaps.
-->

## Objective

{{Useful behavior or outcome in one or two sentences.}}

## Use cases and expected results

{{Realistic input or contributor action, followed by the expected output or observable result.}}

## Implementation plan

1. {{Change and the result it enables.}}
2. {{Tests and documentation needed to complete the change.}}

## Acceptance criteria

- [ ] {{Observable completion condition.}}
- [ ] {{Relevant failure or compatibility condition.}}

## Boundaries and dependencies

{{Included work, exclusions, constraints, prerequisites, and decisions or risks.}}

## Related work and verification plan

{{Descriptive links to related work, parent or standalone context, Project, and approved base.}}

{{Checks mapped to the acceptance criteria and the destination for detailed evidence.}}

<!--
Historical worked example: an Issue-style reconstruction of the Antigravity repair
delivered in
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622), tracked
in [Issue #621](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621).
The objective, sequence, and interaction below illustrate authoring; they are not a
quotation of the original Issue or an observed transcript. The example describes planned
checks, not newly executed evidence.

## Objective

Make Story conversations using Antigravity return the assistant's final reply reliably.
A user should also be able to reopen a conversation and continue with its earlier
context.

## Use cases and expected results

The following input and output wording is constructed to explain expected behavior. It
is not a recorded transcript and contains no private story content.

```text
Input: Remember the marker SAMPLE-27 for this conversation.
Expected reply shown: I'll remember SAMPLE-27.
Action: Reopen the same conversation.
Input: Which marker did I give you?
Expected reply shown: SAMPLE-27.
```

A turn that ends with a valid final success should display its reply even if
intermediate processing reported errors. Before the repair, those intermediate errors
could cause the turn to be rejected without a Lead reply. Final error results must still
remain errors.

## Implementation plan

1. Read Antigravity's final result with `--output-format json`. JSON-mode discovery
  probes ignored text on standard input, so supply the complete prompt through
  `--print`.
2. Preserve strict response validation and existing Codex and shared behavior.
3. Check adapter behavior and conversation continuity, including a fresh turn and a
  reopened turn for each supported model in the verification report.
4. Document the argument exposure and size limits, plus the model-attestation limits of
  the final JSON.

## Acceptance criteria

- [ ] A successful Antigravity turn displays its final assistant reply.
- [ ] A repaired turn with a valid final result is accepted; invalid final results
  remain rejected.
- [ ] A reopened conversation preserves the same thread and recalls its earlier marker.
- [ ] Exchanges, schema checks, accounting, and exit behavior remain valid.
- [ ] Focused adapter, parity, common, and Codex checks pass without claiming
  unsupported model attestation.

## Boundaries and dependencies

The repair concerns the Antigravity adapter with the observed CLI version 1.1.27. It
does not redesign Codex or shared provider behavior.

Passing the prompt as an argument exposes it to process-argument inspection and
operating-system argument-size limits. The final JSON omits initialization and step
attestation, so it cannot independently prove the resolved model. Those limits must
remain visible in the review.

## Related work and verification plan

[Issue #621](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621) is
the historical work item;
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622) contains
the resulting repair. This reconstruction does not establish their original parent,
Project item, or approved base; retrieve those planning records before using it as an
active Issue.

Use focused tests for final-response handling and unchanged provider behavior. Use
actual adapter calls and fresh/reopened Story turns to check replies, marker recall,
thread continuity, exchanges, schema, accounting, and exit behavior.

The later
[verification report for Issue #621](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
reports 51 focused tests, three adapter calls, and six Story turns. Its raw private
manifest and exact test-ID commands are unavailable from the comment, so it supports a
historical summary, not a fully reproducible rerun selection.
-->
