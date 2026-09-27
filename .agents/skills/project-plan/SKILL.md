---
name: project-plan
description: >-
  Root-only, explicit-invocation Project planning skill that prepares the owning Issue's
  detailed implementation plan and readiness metadata, then stops before implementation.
---

# Project plan

Use when the user explicitly invokes `$project-plan`, or when the root coordinator loads it
under an active `$project-workflow` invocation. Only the root may load or execute this skill;
never delegate GitHub operations or lifecycle authority.

Invocation authorizes in-scope Issue/Project planning records and native relationships. It
ends at planning readiness and does not authorize branch/worktree creation, tracked-file
edits, commits, pushes, PRs, review requests, integration, or cleanup. A natural language
planning request does not invoke this authority by itself.

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md), especially the
[planning and implementation boundary](../../../GITHUB_WORKFLOW.md#planning-and-implementation-boundary).
Before authoring records, read the
[authoring guide](../github-task-lifecycle/references/issue-pr-authoring.md) and each relevant
raw template. Preserve all visible headings and their meanings. Keep dependencies subject to
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md); invocation grants no policy exception.

## Prepare the plan

1. Inspect current behavior, the user's objective, scope, constraints, acceptance criteria,
   and verification needs. Ask only when a missing requirement materially changes the result.
2. Record the planning gate with its reason. While pending, write only authorized Issue/Project
   planning records; a blocked gate is read-only. Search open and closed Issues before creating
   anything. Reuse the owning Issue and successful record IDs instead of duplicating them.
3. Write the detailed plan in the owning Issue's existing `## Implementation plan` section.
   Name affected components, intended behavior and interface changes, implementation order,
   relevant failure cases, and verification mapped to acceptance criteria. Keep
   `## Implementation summary` brief and the template's verification section current.
4. Keep one delivery unit on one Issue. Use native sub-Issue/dependency relations only when
   justified. Never create a sole child or wrapper; known children must be at least two
   independently useful, necessary siblings, created and verified together.
5. Complete Project #1 membership, type/area labels, native Issue Requirement, Project Size
   and Iteration, milestone, owner, accepted base, dependencies, and status. Verify an
   executable leaf is unblocked `Ready` before first implementation. For resumed work,
   verify equivalent readiness without
   regressing its current status. Preserve successful mutations and repair only missing state.
6. Record a passed gate only after the plan and required readiness state are verified,
   accounting for any explicit maintainer-approved mechanical exception. Scope expansion,
   missing material decisions, or unmet dependencies stop dependent writes; report the owner/action.

## Finish and hand off

Report the owning Issue and Project item, the detailed plan's Issue-section link, accepted
base, verified scope/criteria, readiness metadata and dependencies, gate result, and any
exceptions or blockers. Stop before branch/worktree creation or implementation. A standalone
invocation does not advance to `$project-implement`; the full coordinator may continue under
its existing authority. Workflow planning authority never overrides Codex Plan Mode's
read-only restriction.
