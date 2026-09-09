---
name: Work item
about: Describe a useful outcome, concrete examples, and a plan for completing it.
title: ""
labels: ""
assignees: ""
---

<!--
Purpose: Help a reader understand the intended result and how the work will achieve it.
When to use: Create or substantially update an Issue for a feature, fix, workflow
change, or documentation task.

Read the authoring guide and this entire source, including the example, before writing:
[Authoring guide](../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md).

Objective: State the useful behavior in one or two sentences. Name who benefits and what
they can do. Keep implementation vocabulary out unless it is essential to the result.

Use cases and expected results: Name each scenario that makes the objective concrete.
Give realistic inputs or actions and their expected outputs, including relevant failure
cases. Identify constructed examples. Internal work can show a contributor action and
repository result without inventing a screen. Scenario names are sufficient; optional
short IDs can make relationships easier to follow.

Implementation summary: Give one short paragraph or a few bullets describing the planned
logic or changes that enable those scenarios. Keep detailed steps in the final
Implementation plan section; this summary is not a workflow, test, or log diary.

Acceptance criteria: Each checkbox must name its scenario and an observable result or
failure condition. Cover every use case. If using IDs, define each once and reference
only known IDs consistently. Planned checks are not completed evidence.

Boundaries and dependencies: State what is included, excluded, or constrained. Identify
prerequisites, their owners, and decisions still needed. Explain how risks or limits
affect the scenarios. Link native parent and dependency relationships; prose links do
not create them.

Related work and verification plan: Link the parent or initiative, canonical Project,
dependencies, related work, and approved base when applicable. Map checks to the named
scenarios and acceptance criteria, and link detailed evidence. For an initiative, name
integration criteria, children, and execution order. For a leaf, identify its parent or
standalone status and include test and documentation work. Keep planning metadata in
Project fields rather than the objective.

Implementation plan: Keep this as the last section. For each ordered step, name the
affected logic or component, necessary technical details, and the scenario and criterion
it enables. Include tests and documentation where needed. Cover every use case without
unrelated steps, and update the plan when approved scope changes.

Replace every double-brace placeholder. Before submission, remove all comments,
frontmatter, and example material. Omit only genuinely optional empty sections. Re-read
for a clear objective, concrete scenarios, a brief logic summary, and a final detailed
plan whose steps and criteria connect to those scenarios.
-->

## Objective

{{Useful behavior or outcome in one or two sentences.}}

## Use cases and expected results

{{Named scenarios with realistic inputs or actions and expected results.}}

## Implementation summary

{{Brief planned logic or changes that enable the scenarios.}}

[Detailed implementation plan](#implementation-plan)

## Acceptance criteria

- [ ] {{Named scenario and observable completion condition.}}
- [ ] {{Named scenario and relevant failure or compatibility condition.}}

## Boundaries and dependencies

{{Scope, prerequisites, and limits or decisions that affect the scenarios.}}

## Related work and verification plan

{{Descriptive links to related work, parent or standalone context, Project, and approved base.}}

{{Checks mapped to scenarios and criteria, with the destination for detailed evidence.}}

## Implementation plan

1. {{Affected logic, planned change, and scenario and criterion it enables.}}
2. {{Next necessary step and its mechanism, scenario, and criterion.}}

<!--
Historical worked example: an Issue-style reconstruction of the Antigravity repair
in [PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622),
tracked in
[Issue #621](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621).
The plan, interaction, and UC/AC labels below are illustrative authoring choices, not
identifiers or text retrieved from Issue #621. This is not an observed transcript or
newly executed evidence.

## Objective

Make Story conversations using Antigravity return the assistant's final reply reliably.
A user should also be able to reopen a conversation and continue with its earlier
context.

## Use cases and expected results

The input/output wording below is constructed to explain the expected behavior and
contains no private story content.

**UC1:** Receive a valid final reply. Even if intermediate processing reported errors,
a turn ending in a valid final success should show its reply. Before the repair, those
intermediate errors could cause rejection without a Lead reply.

```text
Input: Remember the marker SAMPLE-27 for this conversation.
Expected reply shown: I'll remember SAMPLE-27.
```

**UC2:** Reopen and recall. After UC1, reopening the same conversation should preserve
its thread and earlier context.

```text
Action: Reopen the same conversation.
Input: Which marker did I give you?
Expected reply shown: SAMPLE-27.
```

**UC3:** Reject a final error or invalid result. If the provider's final result reports
an error or fails strict response validation, the turn must remain unsuccessful rather
than appearing as a successful assistant reply.

## Implementation summary

Read Antigravity's final JSON result and supply the complete prompt through its print
argument. Preserve strict response validation and existing conversation handling so a
valid final success can supply the reply while final errors and invalid results remain
rejected.

[Detailed implementation plan](#implementation-plan)

## Acceptance criteria

- [ ] **AC1:** After intermediate stream errors, a valid final success still displays
  its assistant reply. (UC1)
- [ ] **AC2:** Reopening preserves the same thread and returns the earlier marker when
  asked. (UC2)
- [ ] **AC3:** Final errors and results that fail strict validation remain unsuccessful;
  neither is presented as a successful assistant reply. (UC3)
- [ ] **AC4:** Fresh and reopened successful turns finish cleanly and leave valid
  recorded exchanges and accounting details for inspection, preserving the existing
  schema and exit checks. (UC1, UC2)

## Boundaries and dependencies

The repair concerns the Antigravity adapter with the observed CLI version 1.1.27.
Codex and shared provider behavior remain unchanged.

Passing prompts as arguments makes their text visible to process-argument inspection
and subject to operating-system argument-size limits; confidential or large prompts
still need those constraints considered. Final JSON omits initialization and step
attestation, so it cannot independently confirm that the requested model handled the
turn.

## Related work and verification plan

[Issue #621](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621) is
the historical work item;
[PR #622](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/622)
contains the repair. This reconstruction does not establish their original parent,
Project item, or approved base; retrieve those records before using it as an active
Issue.

Map focused final-result checks to AC1 and AC3. Map actual replies and fresh/reopened
Story turns to AC1, AC2, and AC4, while adapter, parity, common, and Codex checks cover
the affected behavior and compatibility boundary.

The later
[verification report](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/621#issuecomment-5578471359)
reports 51 focused tests, three adapter calls, and six Story turns. Its exact test-ID
commands and raw private manifest are inaccessible from the comment. Successful replies
are reported, but separate results for AC1's intermediate-error condition and AC3's
rejection condition are not established. The aggregate count cannot turn those planned
checks into observed passes.

## Implementation plan

1. Change the Antigravity adapter's final-result handling to request
  `--output-format json` and consume a valid final success despite intermediate errors.
  This allows the user to receive the completed reply. (UC1; AC1)
2. Supply the complete prompt through `--print` so the requested input reaches the CLI.
  JSON-mode discovery probes ignored text on standard input; that finding is distinct
  from the original rejected-reply failure. Document the argument visibility and size
  limits alongside this transport choice. (UC1; AC1)
3. Preserve strict response validation, existing conversation identity, and exchange,
  schema, accounting, and exit handling. Keep Codex and shared behavior unchanged. These
  constraints let reopening retain context without accepting an unsuccessful final
  result. Document that final JSON cannot independently attest the resolved model.
  (UC1, UC2, UC3; AC2, AC3, AC4)
4. Verify reply delivery and rejection with focused adapter checks; retain parity,
  common, and Codex compatibility checks. Use actual adapter calls and fresh/reopened
  Story turns for each selected model to check reply, marker, thread, and metadata.
  Record outcomes against the relevant criteria, leaving inaccessible individual
  results unestablished rather than inferring them from the aggregate count.
  (UC1, UC2, UC3; AC1, AC2, AC3, AC4)
-->
