# Agent Task Runbook

Use this runbook for GitHub-tracked implementation. The rules in
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) and
[COMMIT_POLICY.md](COMMIT_POLICY.md) remain authoritative. Obtain explicit
authorization before any GitHub mutation, push, pull request, merge, Issue
closure, Project update, staging, commit, or branch deletion.

## Branch and Worktree Model

```text
main
└── issue-100-parent          parent worktree; PR to main (merge commit)
    ├── issue-101-first-task  child worktree; PR to parent (squash merge)
    └── issue-102-next-task   child worktree; PR to parent (squash merge)
```

- Record the parent branch and exact starting commit in the parent Issue.
  Normally create it from the current `origin/main`; never assume a local
  `main` that is ahead or diverged is the intended base.
- Name parent and child branches `issue-<number>-<short-slug>`.
- Put task worktrees in a sibling root such as
  `../<repository>-worktrees/issue-<parent>-<slug>/`. Keep the primary
  worktree on `main` and preserve its unrelated changes.
- Assign one integration owner to the parent worktree and one owner to each
  child worktree. Worktrees may coexist, but only one write-capable owner is
  active at a time. A dependent child waits until its prerequisite is
  integrated.
- Do not commit feature work directly to the parent branch. It accepts child
  squash merges and synchronization merges from `main`. Handle an integration
  fix through a child Issue, branch, and worktree.
- Do not rebase or force-update a published parent or child branch. After a
  fresh fetch and approval, merge `origin/main` into the parent and the parent
  into active children; resolve a child's conflicts in its own worktree.

Every child PR targets the parent branch and uses **Squash and merge**. Child
commits still follow the commit policy, while the resulting squash commit must
be one coherent change that identifies the child Issue and PR. The final
parent PR targets `main` and uses a merge commit, preserving one squash commit
per child PR.

For a standalone leaf Issue, create its branch in a sibling worktree from the
approved `main` ref and target its PR directly to `main`; omit the parent steps.

## Create the Worktrees

First fetch and inspect without changing branches:

```bash
git fetch origin
git worktree list
git status --short
git log --left-right --count origin/main...main
```

If `main` is ahead, behind, or diverged, identify and record the approved base
commit before continuing. Then adapt these templates:

```bash
git worktree add -b issue-<parent>-<slug> \
  ../<repository>-worktrees/issue-<parent>-<slug>/parent \
  <approved-base-ref>

git worktree add -b issue-<child>-<slug> \
  ../<repository>-worktrees/issue-<parent>-<slug>/issue-<child>-<slug> \
  issue-<parent>-<slug>
```

If an approved branch already exists, omit `-b` and add a worktree for that
branch. A branch can be checked out in only one worktree. Create a child only
from a clean, current parent branch.

## Task Lifecycle

1. **Qualify the work.** Read `README.md` and the governing policies. Inspect
   the Issue, parent, Project, milestone, iteration, priority, acceptance
   criteria, dependencies, and current worktrees. Resolve blockers and obtain
   the required authorization.
2. **Prepare the parent.** Create or reuse its branch and worktree at the
   recorded base commit, assign the integration owner, and move the parent to
   the correct Project state. Publish only when authorized. After the first
   child lands, open the parent PR to `main` as a draft with `Refs #<parent>`.
3. **Start one child.** Confirm that the child is `Ready`, scheduled, and
   unblocked. Create its branch and worktree from the current parent commit,
   assign its owner, and move it to `In progress`.
4. **Implement and verify.** Change only the child scope. Follow the commit
   policy, update tests and documentation, run relevant checks, and review the
   final diff and worktree status.
5. **Review the child.** Merge the latest parent branch into the child without
   rebasing, rerun affected checks, and open a draft PR whose base is the
   parent branch and whose head is the child branch. Use `Refs #<child>`. Mark
   it ready and move the child to `In review` only when all evidence is present.
6. **Integrate the child.** Reconfirm the PR base and head, then use **Squash
   and merge** when authorized. The child must contain the current parent tip;
   otherwise merge the parent into it, rerun affected checks, and repeat
   review. Record the child head SHA and resulting squash SHA, then fetch and
   fast-forward the clean parent worktree from `origin/<parent-branch>` with
   `--ff-only`; stop on divergence. Verify the squash diff and run focused
   integration checks there. On success, close the child Issue manually and
   verify `Done`. On failure, block sibling merges and keep the child open in
   `In progress`; require an authorized revert PR or follow-up child fix instead
   of editing the parent directly. If it was already closed, reopen it to
   `Backlog` and reschedule it as required by the policy.
7. **Complete the parent.** After all required children are `Done`, merge the
   exact approved `origin/main` tip into the parent after a fresh fetch, resolve
   conflicts there, and run the complete parent acceptance checks. Mark the
   parent PR ready, replace `Refs #<parent>` with `Closes #<parent>`, move it to
   `In review`, and merge it to `main` with a merge commit when authorization
   includes the resulting parent closure. Record and verify the final merge
   SHA, Issue closure, Project `Done`, and milestone state.
8. **Clean up and hand off.** Remove a worktree only after its status is clean
   and its integration is verified. Never use forced worktree removal. Branch
   deletion requires separate authorization; because squash merging does not
   mark a child branch as Git-merged, verify PR and commit recoverability before
   deleting it. Retain the parent worktree until the parent reaches `main`.

The handoff must include Issue and PR links, base/head branches and SHAs,
resulting squash SHAs, worktree paths, Project metadata, completed and remaining
criteria, checks, blockers, cleanup, GitHub mutations, and the next owner or
action.
