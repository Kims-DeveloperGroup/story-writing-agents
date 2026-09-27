---
name: project-implement
description: >-
  Root-only, explicit-invocation Project implementation skill that prepares missing planning
  prerequisites, isolates accepted work, verifies it, commits, and pushes without a separate
  project-plan invocation or plan-approval checkpoint.
---

# Project implement

Use when the user explicitly invokes `$project-implement`, or when the root coordinator
loads it under an active `$project-workflow` invocation. Only the root may load or execute
this skill; never delegate GitHub operations or lifecycle authority.

Invocation authorizes necessary in-scope Issue/Project preparation, native relationships,
Development-linked branch/worktree creation, implementation, verification, staging, coherent
commits, and normal push. It does not authorize PR creation, external review requests,
integration, closure, or cleanup. It requires neither a prior `$project-plan` invocation nor
a separate approval of the prepared plan. A natural language implementation request does not
invoke this stage's delivery authority by itself.

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md),
[COMMIT_POLICY.md](../../../COMMIT_POLICY.md), and
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md). Before authoring records, read the
[authoring guide](../github-task-lifecycle/references/issue-pr-authoring.md) and each relevant
raw template. Preserve all visible headings and meanings. Invocation never grants force
operations, bypassed protections, unrelated scope, Project-schema changes, dependency-policy
exceptions, or destructive cleanup authority.

## Establish readiness without a separate planning invocation

1. Accept a clear objective or existing owning Issue; inspect the repository and current work.
   Record the planning gate and search open and closed Issues before creating anything.
2. Reuse and validate the owning Issue's detailed `## Implementation plan`. If missing or
   incomplete, prepare it directly using the raw Issue template and the canonical
   [planning and implementation boundary](../../../GITHUB_WORKFLOW.md#planning-and-implementation-boundary).
   Cover affected components, intended behavior/interfaces, order, relevant failure cases,
   and verification against acceptance criteria. Keep the summary brief.
3. Prepare missing native relationships and planning metadata: Project #1 membership,
   type/area labels, native Issue Requirement, Project Size and Iteration, milestone, owner,
   accepted base, dependencies, and status. Keep one delivery unit on one Issue; never create
   a sole child or wrapper. Verify an unblocked executable leaf is `Ready` before first
   implementation; preserve appropriate status for resumed work.
4. While pending, perform only authorized Issue/Project planning writes. Record a passed gate
   after readiness is verified, then continue automatically. Routine plan/metadata preparation
   is not an approval checkpoint. Do not infer a metadata or policy exception: material
   ambiguity, scope expansion, missing exceptional authority, or blocked dependencies stops
   dependent writes and requires the missing decision or prerequisite.

## Isolate and implement

1. Create `issue-<number>-<slug>` through the Issue Development link from the accepted base,
   then create one sibling worktree. Reuse verified existing resources when resuming.
2. Verify path, repository, native Development link, branch, base/HEAD ancestry, and porcelain
   before editing. Preserve unrelated changes and other worktrees. Move the item to
   `In progress`; this is the implementation phase, after planning readiness.
3. Implement only accepted scope. Use `runtime_implementer` or `test_engineer` only under
   AGENTS routing; those agents never receive this skill's GitHub or lifecycle authority.
4. Runtime code and runtime agent definition (`config/agents/`) changes require affected
   runtime tests through the canonical runner, with exact selectors and JSON evidence.
   Runtime and test changes require full CI under
   [TEST_POLICY.md](../../../TEST_POLICY.md). Policy, docs, templates, skills,
   development-agent configuration (`.codex/agents/`), and index-only changes use static
   validation and diff review.
   If Actions cannot execute, run the same applicable CI locally under the canonical
   [fallback](../../../GITHUB_WORKFLOW.md#actions-non-execution-fallback), without another
   confirmation. Retain exact source identity and complete evidence; never reduce selected
   lanes, erase actual validation failures, or waive enforced hosted checks/approvals.
5. Review the final diff, stage only the accepted change, and create coherent commits under
   COMMIT_POLICY. Push normally. Never reset, clean, rebase, force-push, or overwrite unrelated
   work to manufacture a clean state. New scope repeats the planning gate.

## Finish and hand off

Report the Issue/Project and detailed plan link, worktree, branch/native link, accepted base,
current HEAD, verification commands/results and evidence, commit/push state, and preserved
changes or blockers with their owner/action. Stop before PR creation or review; a standalone
invocation does not advance to `$project-pr-review`. The full coordinator may continue under
its existing authority. Codex Plan Mode remains read-only even when this skill is invoked.
