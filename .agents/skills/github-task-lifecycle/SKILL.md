---
name: github-task-lifecycle
description: >-
  Guard normal GitHub-tracked planning, linking, delivery, integration, and cleanup;
  excludes explicit full-lifecycle project-workflow invocation and story-runtime work.
---

Follow [GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md). Before reader-facing records, read
the [authoring guide](references/issue-pr-authoring.md) and relevant raw template.

This is the default, narrow-authority workflow. Read-only inspection is allowed. Record the
planning gate before writes. Create or update an Issue, Project item, relationship, branch,
commit, push, PR, review request, merge, closure, branch deletion, or worktree removal only
when that step is authorized. Never infer later delivery authority from implementation
authority. For user-invoked end-to-end authority, the root uses `$project-workflow` instead.

## Plan and link

1. Search open and closed Issues; reuse the owning item when possible.
2. For nontrivial work, verify the Issue body, native hierarchy/dependencies, Project #1
   membership, Requirement, Size, Iteration, milestone, labels, owner, base, and status.
3. Keep one delivery unit on one Issue. Create children only as a known set of at least two
   independently useful, necessary siblings.
4. Move an unblocked executable leaf to `Ready`; implementation moves it to `In progress`.
5. Create `issue-<number>-<slug>` from the accepted base through the Issue Development link,
   then add one sibling worktree. Verify repository, branch, HEAD, ancestry, and porcelain.

Use native sub-Issue and dependency relations. Preserve successful IDs after a partial
failure; repair missing state without duplication. New scope repeats the planning gate.

## Deliver

Follow [COMMIT_POLICY.md](../../../COMMIT_POLICY.md) and, when applicable,
[DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md). Do not reset, clean, rebase, force-push,
or overwrite unrelated changes.

Create a draft PR when useful and mark it ready only after its description, scope, affected
verification, docs, diff, base, and Development/Project links are current. Use `Refs #N` for
partial work and `Closes #N` only for a completing PR to `main`.

At ready-for-review, follow [review routing and closure](../../../GITHUB_WORKFLOW.md#asynchronous-review-and-ci):
record the rationale, choose one provider selector, and request through the managed workflow
while affected CI starts. Reconcile numbered rounds before requesting another. Medium
feedback is optional through round 2; round 3 onward accepts only confirmed High/Critical
defects. Stop at verified closure without requiring another clean AI review. Preserve review
history across head/base changes and provider switches; never routinely request both providers.
Mutating automation requires `workflow:managed`. Recheck current base/head, final-diff
inspection, blocker dispositions, approvals, checks, and mergeability before manual GitHub
squash merge. Do not enable auto-merge or merge queue. Include the owning Issue in the squash
commit message.

## Verify and finish

Runtime code changes use `PYTHONPATH=src python -m tests.runner run` with exact IDs and JSON
evidence. Runtime and test changes require full CI under TEST_POLICY.md.
Policy/docs/templates/skills/development-agent configuration/module-index-only changes use
static checks and diff review only.

After merge, verify the merged SHA, review closure, current-head checks, Issue closure,
Project `Done`, milestone disposition, Development links, and remote branch state. Before cleanup,
record worktree/branch/HEAD/porcelain, integration, recoverability, and authority. Remove only
clean integrated worktrees without force, delete branches separately, and verify absence.

For PR review comments, follow [review comments](../../../GITHUB_WORKFLOW.md#review-comments):
verify each finding against the current diff, reply with the applied change and verification or
rejection rationale, then resolve the thread. Recheck outdated or drifted threads; do not
resolve findings that still need work.

Report all mutations, failures, preserved state, current head/base, and the next owner/action.
