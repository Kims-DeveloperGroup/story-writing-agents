---
name: project-workflow
description: >-
  Root-only, explicit-invocation workflow that completes an in-scope GitHub project task from
  Issue planning through PR review, merge, closure, and cleanup.
---

# Project workflow

Use this skill only when the user explicitly invokes `$project-workflow`. Only the root agent
may load or execute it; never delegate its GitHub operations or lifecycle authority. A natural
language request to implement, open a PR, or continue does not invoke this skill by itself.

Invocation authorizes the root to complete the full in-scope lifecycle without pausing for
step-by-step approval: Issue/Project planning, native relationships, Development-linked branch
and worktree, staging, commit, push, PR, milestone reviews, gated squash merge, closure,
Project completion, and safe cleanup. The authority ends at the accepted scope and never
covers force operations, bypassed protections, unrelated work, Project-schema changes,
dependency-policy exceptions, or destructive cleanup that lacks verified recoverability.

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md),
[COMMIT_POLICY.md](../../../COMMIT_POLICY.md), and
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md). Before authoring records, read the
[authoring guide](../github-task-lifecycle/references/issue-pr-authoring.md) and each raw
template. Preserve all visible Issue, PR, evidence, and status-comment headings and semantics.

## 1. Plan

1. Record the planning gate and search open and closed Issues before creating anything.
2. Reuse or create the owning Issue. Build native sub-Issue/dependency relations only when
   justified; never create a sole child or wrapper.
3. Add Project #1 metadata: type/area labels, Requirement, Size, Iteration, milestone, owner,
   accepted base, dependencies, and status. Verify an executable leaf is unblocked `Ready`.
4. If scope expands beyond invocation, stop writes and obtain the missing decision.

## 2. Isolate and implement

1. Create `issue-<number>-<slug>` through the Issue Development link from the accepted base.
2. Create one sibling worktree and verify path, repository, branch, base/HEAD ancestry, and
   porcelain. Move the Issue to `In progress`.
3. Implement only accepted scope. Use `runtime_implementer` or `test_engineer` only under
   AGENTS routing; those agents never receive this skill's GitHub authority.
4. Runtime code and runtime agent definition (`config/agents/`) changes require affected
   runtime tests through the canonical runner. Policy, test, docs, development-agent
   configuration (`.codex/agents/`), and index-only changes use static validation.
5. Review the diff, then stage and create coherent commits under COMMIT_POLICY. Push normally;
   never force-push.

## 3. Open and review the PR

1. Create or update the PR from the raw template. Verify the base and native Development and
   Project links. Use `Refs #N` unless a completing `main` PR may use `Closes #N`.
2. When ready, add `workflow:managed`, record the head SHA, move the item to `In review`, and
   request Copilot and Codex concurrently with affected CI. Copilot uses Lite unless the
   change is high-risk, when Balanced is appropriate.
3. Continue nondependent work while event-driven checks run. Batch unresolved Copilot High
   and Codex P0/P1 findings with required human feedback into one fix set.
4. After material fixes, push once, record the new head SHA, and request one final concurrent
   review/CI milestone. Do not re-request after each push.
5. Verify required approvals/checks and that review evidence matches the current head SHA.
   Lower-severity AI feedback is nonblocking and may become linked follow-up work.

## 4. Integrate and close

1. Enable auto-merge or merge queue only after current-head gates pass.
2. Use GitHub **Squash and merge**. Include the owning Issue number in the squash commit.
3. Verify the merged SHA on the target, Issue closure, Project `Done`, milestone state,
   Development links, and remote branch disposition. A child PR never closes its parent.
4. Post or update the preserved status/evidence templates when a durable record is useful.

## 5. Clean up

Inventory worktree, branch, HEAD, porcelain, integration, and recoverability. Remove only a
clean, integrated worktree without force; delete local and remote branches only after their
state is verified. Preserve any unexpected changes and report the exact recovery owner.
Confirm absence and summarize Issue, Project, PR, merge SHA, closure, cleanup, and residual
follow-ups.
