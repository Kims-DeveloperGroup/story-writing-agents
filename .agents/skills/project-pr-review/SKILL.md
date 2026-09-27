---
name: project-pr-review
description: >-
  Root-only, explicit-invocation Project PR and review skill that creates or updates the PR,
  handles managed reviews and in-scope fixes, and stops at verified closure before merge.
---

# Project PR and review

Use when the user explicitly invokes `$project-pr-review`, or when the root coordinator loads
it under an active `$project-workflow` invocation. Only the root may load or execute this
skill; never delegate GitHub operations or lifecycle authority.

Invocation authorizes in-scope PR/Project records, managed review requests and reconciliation,
scoped local branch/worktree restoration for the existing Development-linked PR branch,
review fixes, their verification, coherent commits, and normal pushes. It does not authorize
integration, Issue closure, Project completion, delivery-branch deletion, or delivery-worktree
removal. Local CI may create and clean up one owned disposable detached validation checkout
under the canonical fallback; this exception never removes the delivery worktree.
A standalone invocation never advances to `$project-complete`.

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md),
[COMMIT_POLICY.md](../../../COMMIT_POLICY.md), and
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md). Before authoring records, read the
[authoring guide](../github-task-lifecycle/references/issue-pr-authoring.md) and each raw
PR, evidence, or status template being used. Preserve all visible headings and meanings.

## Verify prerequisites and open the PR

1. Inspect the owning Issue's accepted scope and detailed plan, Project item, accepted base,
   Development-linked PR branch, current pushed head, and verification. A local worktree is
   optional for GitHub-only PR/review actions; its absence does not block those actions.
   Record or revalidate the planning gate. Reuse an existing PR and successful records;
   preserve numbered review history. Missing prerequisites stop dependent writes and identify
   the required owner/action; this stage does not automatically execute planning or initial
   implementation. Preserve unrelated work and reject scope expansion without its decision.
2. Create or update the PR from the raw template. Verify the base and native Development and
   Project links. Use `Refs #N` unless a completing `main` PR may use `Closes #N`.
   Draft PRs may hold incomplete work; mark ready only when scope, description, affected
   verification, docs, final diff, base, and native links are current.
3. At ready-for-review, follow
   [review routing and closure](../../../GITHUB_WORKFLOW.md#asynchronous-review-and-ci):
   record `Review rationale`, choose exactly one provider selector, add `workflow:managed`,
   and move the item to `In review`. Request through the managed workflow while affected CI
   runs. Balanced uses the Reviewers UI; never silently substitute another effort/provider.
   Confirmed Copilot budget exhaustion uses the canonical Codex fallback after reconciling
   non-delivery. If Actions cannot execute, use the root-owned local CI and direct review
   [fallback](../../../GITHUB_WORKFLOW.md#actions-non-execution-fallback), preserving request
   deduplication, numbered history, provider snapshots, and all findings.

## Reconcile and fix

1. Before local fixes, verify the repository, existing Development-linked remote branch,
   accepted base, current PR head, and local porcelain. Reuse its verified local tracking
   branch/worktree, or restore a local tracking branch and one sibling worktree from that
   existing remote branch. Verify path, branch, HEAD, and ancestry before editing. Preserve
   unrelated changes; this authority creates no new remote delivery branch or initial scope.
   Reconcile feedback into one batch against actual scope and the current diff. Apply and
   verify confirmed in-scope defects, then create coherent commits and push normally.
   Use AGENTS routing for development work; subagents receive no lifecycle authority.
   Runtime-code fixes require affected canonical tests; runtime/test changes require full CI.
   Other fixes use static validation and diff review under
   [TEST_POLICY.md](../../../TEST_POLICY.md).
2. Medium findings and small in-scope improvements are optional through round 2. From round 3,
   accept only confirmed High/Critical defects, missing or incorrect agreed requirements,
   and material defects. Medium findings never block completion. Use canonical severity,
   disposition, evidence, reply, and thread-resolution rules; never resolve unfinished work.
3. Preserve numbered rounds across pushes, base changes, reopening, and provider switches.
   Do not request after every push. Reconcile outstanding or uncertain requests before another
   request; further rounds need material uncertainty and evidence. Never routinely request
   both providers or switch to evade a finding. Continue independent work while review/CI runs.
4. Stop at verified closure: accepted criteria, evidence-backed resolution/rejection of every
   confirmed High/Critical finding, passing current applicable CI evidence (hosted or canonical
   local fallback), all enforced required checks/approvals, and inspection
   of the final diff. An additional clean AI review is unnecessary. Audit every blocking
   finding and all required conversation-resolution protections.

## Finish and hand off

Preserve the provider-reviewed base/head SHA separately from later local verification and
current validation. Report Issue/Project/PR links, current base/head, numbered rounds and finding
closure, verification, approvals/checks, and remaining owner/actions. Record managed closure
through the canonical workflow, or a root-authored manual record during confirmed Actions
non-execution, and stop before merge. Keep hosted failures and the execution location explicit.
If prerequisites or enforced required checks remain unmet, report that state without claiming verified closure. The full coordinator may
continue to integration only after closure is verified. Codex Plan Mode remains read-only.
