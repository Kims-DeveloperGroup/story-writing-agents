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

At ready-for-review, record the head SHA and request Copilot and Codex concurrently with
affected CI. Batch blocking feedback. After material fixes, record the new SHA and make one
final concurrent review request; never request again after every push. Only unresolved
Copilot High, Codex P0/P1, required approvals, and required checks block merge.

Mutating automation requires `workflow:managed`. Auto-merge or merge queue may be enabled
only after gates pass. Use GitHub squash merge for every PR and include the owning Issue in
the squash commit message.

## Verify and finish

Runtime code changes use `PYTHONPATH=src python -m tests.runner run` with exact IDs and JSON
evidence. Policy/docs/templates/skills/development-agent configuration/module-index-only/
test-only changes use static checks and diff review only.

After merge, verify the merged SHA, current-head review/check evidence, Issue closure, Project
`Done`, milestone disposition, Development links, and remote branch state. Before cleanup,
record worktree/branch/HEAD/porcelain, integration, recoverability, and authority. Remove only
clean integrated worktrees without force, delete branches separately, and verify absence.

Report all mutations, failures, preserved state, current head/base, and the next owner/action.
