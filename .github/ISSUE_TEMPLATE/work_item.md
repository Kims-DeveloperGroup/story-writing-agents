---
name: Work item
about: Describe a useful outcome, concrete examples, and a plan for completing it.
title: ""
labels: ""
assignees: ""
---

<!--
Read the authoring guide and this complete source before writing:
../../.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md

Write for a contributor who has not read earlier discussion. Explain the current limitation,
the useful outcome, concrete actions and results, issue-specific completion conditions, and
how the affected components will produce them. Common workflow rules do not replace these
details.

Keep one delivery unit on one Issue. Use native Project, sub-Issue, dependency, Development,
milestone, and owner fields as their authoritative records; prose explains the work and why
a relation matters.

Use paragraphs, lists, or a small table according to the work. No particular table, number
of scenarios, or number of components is required. Preserve every visible heading and its
meaning. Remove this comment, unused placeholder items, and every placeholder before
publication.

During initial planning, identify unresolved material facts or choices with their impact
and next owner/action. Label new designs as proposals until established; do not present an
unverified design as an accepted decision or invent existing paths, test IDs, measurements,
or completed checks. Before first implementation, resolve material planning gaps and verify the
existing readiness requirements in GITHUB_WORKFLOW.md. Routine implementation choices do
not require a separate approval.

Keep the detailed plan in Implementation plan. When updating an existing Issue, preserve
its material requirements, examples, failure cases, constraints, and verification mappings.
Summarize important issue-specific decisions above; do not replace the detailed plan with
generic instructions to inspect, move code, run tests, and merge.

Runtime or test changes follow the canonical verification policy. Documentation, templates,
skills, development-agent configuration, and module-index-only changes use static validation
and diff review. Distinguish planned verification from observed results.
-->

## Objective

{{Explain the current limitation and the useful behavior or repository outcome in one or two sentences. Identify whose work or which operation improves. Qualify unverified assumptions rather than presenting them as observed facts.}}

## Use cases and expected results

{{Name concrete scenarios. For each, state the relevant starting conditions, a realistic user or contributor action, and the expected observable result. Include a material failure or recovery case when relevant. Label constructed examples as illustrative. For a refactor, show a representative maintenance or verification action and state the behavior or boundary that must remain unchanged.}}

## Implementation summary

<!-- Record Design impact: required|not_required and the reason. For required work,
link the accepted target/intent/criteria under docs/design/figma-workflow.md. -->

{{Briefly explain the issue-specific mechanisms or responsibility changes that produce the expected results. Surface the most important design decision or preserved boundary here. Distinguish confirmed decisions from proposals; label new paths or APIs as proposed. Keep the detailed steps below.}}

[Detailed implementation plan](#implementation-plan)

## Acceptance criteria

- [ ] {{A named scenario has the stated observable result, with a clear way to verify it.}}
- [ ] {{A separate, relevant failure, recovery, compatibility, or preserved-boundary condition has the stated observable result. Remove this item if it adds no independent completion judgment.}}

{{Add criteria for the actual delivery as needed. Common CI, review, and integration requirements remain applicable, but do not substitute them for issue-specific criteria. Use stable criterion labels when they help connect a substantial plan and its evidence.}}

## Boundaries and dependencies

{{Identify included and excluded work, affected responsibilities, applicable constraints, material risks, and prerequisites. Explain why a dependency matters and link the actual related Issue when known; internal plan identifiers alone are insufficient navigation. Native relations remain authoritative.}}

{{If initial planning has unresolved material facts or choices, state what is unresolved, what it affects, and the next owner/action. Distinguish these gaps from routine implementation choices. Resolve material gaps under the existing readiness policy before first implementation. Omit this paragraph when none remain.}}

## Related work and verification plan

{{Link the owning context or parent and relevant native records. Identify the accepted base and the inspected source revision used for factual design claims. Describe relevant dependencies without duplicating their authoritative native metadata.}}

{{Map scenarios and acceptance criteria to planned verification. Name inspected existing test IDs or selectors where relevant, or the specific static checks for non-runtime work. Explain material selection or expansion conditions. Identify the Evidence destination. Do not imply a planned, historical, unexecuted, or stale check has passed.}}

{{Reference canonical common policies instead of copying their complete contents. If a numeric constraint or historical measurement is necessary here, state its unit, scope, source revision, and whether it is an applicable constraint or a baseline observation. Omit unrelated metrics and policy detail.}}

## Implementation plan

1. {{Name an affected component and its current responsibility. Describe the intended change, resulting responsibility or interface, material preserved boundary, and the scenario or criterion it enables.}}
2. {{Describe the next necessary component change or integration step, its ordering reason, and any relevant failure or recovery handling. Mark proposed paths or APIs as proposals until established.}}
3. {{Map the implemented changes to the planned verification and necessary documentation or index updates. Retain detailed scenarios, mappings, examples, and constraints that are necessary to implement and review the work.}}

{{Scale and repeat these steps to the work. A small change may need only one concrete step. For a substantial refactor, explain responsibility ownership and interactions between affected components; include a mapping table only when it improves comprehension. Do not substitute the common inspect/test/review/merge procedure for this implementation plan.}}
