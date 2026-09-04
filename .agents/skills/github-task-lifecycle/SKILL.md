---
name: github-task-lifecycle
description: >-
  Use for GitHub-tracked planning, inspection, Issues, Projects, branches, worktrees, PRs,
  integration/cleanup. Excludes untracked and story-runtime work.
---

# GitHub Task Lifecycle

[GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md) governs authorization/metadata;
[AGENTS.md](../../../AGENTS.md) governs routing/ownership/verification. Never override them.

## Authorization

Implementation authorizes only workflow-defined initial planning mutations and native
Development-linked task-branch creation. Every other GitHub mutation, push, PR, merge,
Issue closure, stage, commit, or branch deletion requires explicit authorization.

- Resolve repository, Issue, Project, Iteration, base, authorization, owner, worktree, and
  disposition. Stop on missing/mismatched inputs, identity, authority, or gate.
- Preflight repository access, `gh` authentication with `project` scope, Project fields,
  milestones, Iterations, dependencies, and worktrees.
- Execute/verify one mutation at a time. On failure, preserve successes, report partial
  state, and repair only verified gaps.

## Planning

Search all Issue states; draft root/descendants; create/reuse root first.

```bash
issue_url="$(gh issue create --repo "$repository" --title "$title" \
  --body-file "$body" --label "$type" --label "$area")"
item_id="$(gh project item-add "$project_number" --owner "$organization" \
  --url "$issue_url" --format json --jq .id)"
gh project item-edit --id "$item_id" --project-id "$project_node_id" \
  --field-id "$field_node_id" --single-select-option-id "$option_id"
gh project item-edit --id "$item_id" --project-id "$project_node_id" \
  --field-id "$iteration_field_node_id" --iteration-id "$iteration_id"
```

Resolve IDs at runtime; use one logical Iteration tree-wide. Creating an Iteration
when none is unused requires Project-schema authorization. Set Priority through the
organization Issue field, never its same-named Project field.

Create/re-read native relationships:

```bash
gh api --method POST "repos/$repository/issues/$parent_number/sub_issues" \
  -F sub_issue_id="$child_database_id"
gh api --method POST "repos/$repository/issues/$child_number/dependencies/blocked_by" \
  -F issue_id="$blocker_database_id"
```

Pass only after verifying bodies, complete hierarchy/dependencies, canonical Project
membership/fields, Iteration, milestone, labels, assignee, and an unblocked `Ready` leaf.
Standalone work omits a parent; later children repeat this gate.

## Branches

Record parent branch/start SHA in Issue. Name branches `issue-<n>-<slug>`. Use a
sibling worktree path and preserve primary-worktree changes.

Create branch only through Issue Development:

```bash
gh api graphql -f query='mutation($r:ID!,$i:ID!,$o:GitObjectID!,$n:String!){
  createLinkedBranch(input:{repositoryId:$r,issueId:$i,oid:$o,name:$n}){
    linkedBranch{ref{name target{oid}}}}}' \
  -F r="$repository_id" -F i="$issue_id" -F o="$approved_base_oid" -f n="$branch"
git fetch origin "$branch"
git worktree add --track -b "$branch" "$worktree" "origin/$branch"
```

Stop on an unlinked existing branch. Repair legacy traceability only through an authorized
linked PR. Never recreate, rename, replace, rebase, or force-update. Keep one worktree per
branch; omit `--track -b` for an existing local branch. Base children only on clean,
current, GitHub-present parent commits.

Match the handoff before other task reads/writes:

```bash
pwd -P
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git merge-base HEAD "$approved_base_oid"
git merge-base --is-ancestor "$approved_base_oid" HEAD
git status --porcelain=v1 -z --untracked-files=all | od -An -tx1
git status --porcelain=v1 -z --untracked-files=all | shasum -a 256
```

Match capability, identities, ancestry, targets, and exact porcelain bytes, SHA-256, and
decoded paths. Never reset, checkout, clean, or revert to match. Verify Development and
Project links:

```bash
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){linkedBranches(first:20){nodes{ref{name}}}}}}' -F o="$organization" -F r="$repository_name" -F n="$issue_number"
gh project item-list "$project_number" --owner "$organization" --limit 100 --format json \
  --jq '.items[] | select(.content.number == '$issue_number') | .["linked pull requests"]'
```

## Integration

1. Parent starts at approved `main`; children start at current parent; standalone leaves
   start at approved `main`. One writer works; dependent children await integration.
2. Parent accepts only authorized child squashes at its current tip and approved-main sync;
   fixes use children. After first child, authorized draft parent PR targets `main` with
   `Refs #<parent>`.
3. Merge parent into child without rebase, run affected checks, then draft the child PR to
   parent with `Refs #<child>`. Ready/`In review` requires complete evidence plus verified
   Development and Project PR links.
4. Child commits follow [COMMIT_POLICY.md](../../../COMMIT_POLICY.md); their squash coherently
   identifies Issue/PR. Record source/merge SHAs; fast-forward clean parent with
   `--ff-only`, inspect the squash, and rerun integration evidence.
5. On success, manually close and verify `Done`. On failure, block siblings and use an
   authorized revert PR or follow-up child.
6. After all children, merge approved main into parent, run final evidence, change `Refs` to
   `Closes`, set `In review`, and perform only an authorized merge commit. Verify its SHA,
   Issue closure, `Done`, and milestone.

Fresh-fetch before synchronization. Reconfirm head/base before merges. Never rebase,
force-push, auto-merge, or auto-close.

Separately authorize these mutations:

```bash
gh pr create --draft --base "$base_branch" --head "$branch" --body "Refs #$issue_number"
gh pr ready "$pr_number"
gh pr merge "$child_pr" --squash
gh pr merge "$parent_pr" --merge
gh issue close "$issue_number" --repo "$repository"
```

Set `In progress` only after authorized work starts with assignee/Development link; drafts
remain there. After each transition, re-read draft state, Development links, Project status,
closure, and linked PRs. Reopen failed closed work to `Backlog` and replan; keep interrupted active
work `In progress` and blocked.

## Cleanup

Record worktree, branch, HEAD, porcelain bytes/hash/paths, Issue/PR state, SHAs,
recoverability, cleanup authority, outcome, reason, and next owner.

Never clean automatically. Remove only an authorized, clean, integrated, recoverable
worktree whose disposition permits removal:

```bash
git worktree remove "$worktree"
git worktree list --porcelain
test ! -e "$worktree"
```

Never force removal. Branch deletion needs separate authorization. Retain the parent
worktree until its branch reaches `main`.
