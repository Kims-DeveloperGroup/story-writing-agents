---
name: project-complete
description: >-
  Root-only, explicit-invocation Project completion skill that audits integration readiness,
  manually squash-merges, verifies closure, and performs recoverable cleanup.
---

# Project complete

Use when the user explicitly invokes `$project-complete`, or when the root coordinator loads
it under an active `$project-workflow` invocation. Only the root may load or execute this
skill; never delegate GitHub operations or lifecycle authority.

Invocation authorizes in-scope integration, Issue closure, Project completion, durable
status/evidence records, and safe branch/worktree cleanup after verified integration.
It does not authorize implementation fixes, new review requests, force operations, bypassed
protections, unrelated scope, Project-schema changes, or dependency-policy exceptions.

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md),
[COMMIT_POLICY.md](../../../COMMIT_POLICY.md), and
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md). Before authoring records, read the
[authoring guide](../github-task-lifecycle/references/issue-pr-authoring.md) and each raw
evidence/status template being used. Preserve all visible headings and meanings.

## Integrate and close

1. Verify the owning Issue's accepted scope/detailed plan, Project item, accepted base,
   PR/current head, native Development links, and verified review closure. Record or revalidate
   the planning gate. Missing prerequisites stop dependent writes and identify the owner/action;
   do not automatically invoke another stage. Resume an already merged PR at post-merge
   verification instead of attempting integration again.
2. Immediately before integration, recheck accepted base, current head, changed behavior since
   provider review, every review thread/blocking finding, required approvals/checks, and
   mergeability. If fresh verification or review fixes are needed, stop integration and report
   the action needed from PR/review. Do not enable auto-merge or merge queue.
3. Manually use GitHub **Squash and merge**. Include the owning Issue number in the squash
   commit. Never bypass protection or treat historical/stale checks as a current pass.
4. Verify the merged SHA on the target, Issue closure, Project `Done`, milestone disposition,
   Development links, and remote branch disposition. A child PR never closes its parent;
   parents close only after all required children are integrated. Keep failed checks visible
   and repair authorized post-merge record updates without repeating successful operations.
5. Post or update preserved status/evidence templates when a durable record is useful.

## Clean up and finish

1. Inventory worktree, branch, HEAD, porcelain, PR/Issue state, integration, recoverability,
   and cleanup authority. Preserve parent/release worktrees until their integration completes.
2. Remove only a clean, integrated worktree without force. Delete local and remote branches
   separately only after their state and recoverability are verified. Never reset, clean,
   rebase, force-push, or force-remove to manufacture safe cleanup.
3. Preserve unexpected changes or unverified resources, and report the exact recovery owner
   and next action. Verify absence of resources actually removed; do not claim cleanup passed
   for preserved resources.
4. Summarize Issue, Project, PR, current base/head, merge SHA, review/integration/closure
   verification, milestone and branch disposition, cleanup, and residual follow-ups. Report
   partial failures and preserved state explicitly. Codex Plan Mode remains read-only.
