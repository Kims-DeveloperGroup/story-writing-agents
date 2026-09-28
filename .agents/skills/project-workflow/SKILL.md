---
name: project-workflow
description: >-
  Root-only, explicit-invocation coordinator that runs the independent Project planning,
  implementation, PR/review, and completion skills through the full in-scope lifecycle.
---

# Project workflow

Use this skill only when the user explicitly invokes `$project-workflow`. Only the root agent
may load or execute it; never delegate its GitHub operations or lifecycle authority. A natural
language request to implement, open a PR, or continue does not invoke this skill by itself.

Invocation authorizes the root to complete the full in-scope lifecycle without pausing for
step-by-step approval: Issue/Project planning, native relationships, Development-linked branch
and worktree, staging, commit, push, PR, review rounds, gated squash merge, closure,
Project completion, and safe cleanup. The authority ends at the accepted scope and never
covers force operations, bypassed protections, unrelated work, Project-schema changes,
dependency-policy exceptions, or destructive cleanup that lacks verified recoverability.

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md),
[COMMIT_POLICY.md](../../../COMMIT_POLICY.md), and
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md). Before authoring records, read the
[authoring guide](../github-task-lifecycle/references/issue-pr-authoring.md) and each raw
template. Preserve all visible Issue, PR, evidence, and status-comment headings and semantics.

## Coordinate the stages

Load each independent skill when its stage is reached. The active invocation supplies the
root's full-lifecycle authority; no separate stage invocation or approval checkpoint is needed.

| Order | Skill | Required handoff before continuing |
| --- | --- | --- |
| 1. Plan | [project-plan](../project-plan/SKILL.md) | Detailed Issue plan, verified readiness, and passed planning gate. |
| 2. Implement | [project-implement](../project-implement/SKILL.md) | Verified accepted change, coherent commits, and normal push. |
| 3. PR/review | [project-pr-review](../project-pr-review/SKILL.md) | Verified review closure and passing current required checks/approvals. |
| 4. Integrate/close/cleanup | [project-complete](../project-complete/SKILL.md) | Verified integration/closure and safe cleanup, with preserved state reported. |

Revalidate existing handoff evidence when resuming; reuse verified records/resources instead
of duplicating them. The detailed implementation plan lives in the owning Issue's
`## Implementation plan` section. A standalone `$project-implement` invocation may prepare
missing planning prerequisites directly; the coordinator still keeps the four stages explicit.

Stop dependent writes for material ambiguity, scope expansion, blocked prerequisites, or
missing exceptional authority. Do not advance past an unmet stage gate or claim completion
while required work remains. Report the exact owner/action and preserve successful state.
The workflow does not override Codex Plan Mode's read-only restriction.
